"""Execute saved flows via planning pipeline + AgentRuntime."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


async def build_flow_execution_classification(
    flow_id: str,
    user_id: str,
    *,
    user_prompt: str = "",
    auto_approve: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Build an approved execution plan from a saved flow.
    Build classification with an approved execution plan from a saved flow.
    """
    from services.planning.flow_steps import apply_saved_flow_to_classification
    from services.planning.goal_parser import enrich_scope_from_llm, parse_goal
    from services.planning.schemas import merge_plan_into_classification
    from services.planning.plan_builder import build_plan_steps, apply_plan_execution_summary, finalize_plan_status
    from services.planning.resource_catalog import load_resource_catalog
    from services.planning.schemas import ExecutionPlan
    from services.planning.plan_context import pipeline_state_from_plan

    from db.extracted_schema import get_flow_details

    details = await get_flow_details(str(flow_id), UUID(str(user_id)))
    if not details or details.get("status") != "success":
        return {"status": "error", "message": "Flow not found"}

    classification = await apply_saved_flow_to_classification({}, str(flow_id), str(user_id))
    field_mappings = details.get("field_mappings") or []
    if field_mappings:
        classification["flow_field_mappings"] = field_mappings

    prompt = (user_prompt or "").strip()
    schema = details.get("schema") or {}
    if not prompt:
        prompt = (schema.get("user_prompt") or "").strip()
    if not prompt:
        flow = details.get("flow") or {}
        prompt = f"Run flow {flow.get('migration_name') or flow_id}"

    parsed_goal = await enrich_scope_from_llm(
        classification, prompt, user_id=str(user_id), conversation_id=None
    )
    classification["_plan_scope"] = parsed_goal
    if parsed_goal.get("operations"):
        classification["operations"] = parsed_goal["operations"]

    cat = await load_resource_catalog(str(user_id))
    plan = ExecutionPlan(
        plan_id=classification.get("plan_id"),
        status="draft",
        goal_summary=None,
        metadata={"classification": dict(classification), "trigger": "scheduled_flow"},
    )
    from services.planning.clarification_sync import sync_plan_clarifications
    from services.planning.op_requirements import read_plan_requirements

    parsed_goal = classification.get("_plan_scope") or parsed_goal
    reqs = read_plan_requirements(classification, parsed_goal=parsed_goal)
    plan = await sync_plan_clarifications(
        plan,
        classification,
        user_id=str(user_id),
        requirements=reqs,
        parsed_goal=parsed_goal,
        catalog=cat,
        prompt=prompt,
        advance_one=True,
    )
    if plan.blocking_clarifications():
        return {
            "status": "error",
            "message": "Scheduled flow has unresolved planner clarifications.",
            "needs_clarification": True,
            "clarifications": [c.model_dump(mode="json") for c in plan.clarifications],
        }

    plan = build_plan_steps(classification, prompt=prompt, plan=plan)
    plan = finalize_plan_status(plan, force_auto_approve=auto_approve)
    classification.pop("_plan_scope", None)
    out = merge_plan_into_classification(classification, plan)
    out["pipeline_state"] = pipeline_state_from_plan(
        plan.status,
        needs_clarification=bool(out.get("needs_clarification")),
    )
    return out


async def execute_flow_plan_sync(
    flow_id: str,
    user_id: str,
    *,
    user_prompt: str = "",
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run an approved v2 plan for a flow (drain AgentRuntime stream).
    Used by scheduled flow runner (auto-approve).
    """
    classification = await build_flow_execution_classification(
        flow_id,
        user_id,
        user_prompt=user_prompt,
        auto_approve=True,
    )
    if classification.get("status") == "error":
        return classification
    if classification.get("plan_status") != "approved":
        return {
            "status": "error",
            "message": f"Flow plan not executable (status={classification.get('plan_status')}).",
        }

    from agent.runtime import run_agent_architect_stream

    last_error: Optional[str] = None
    try:
        async for _chunk in run_agent_architect_stream(
            user_prompt or classification.get("reasoning") or "Run flow",
            str(user_id),
            [],
            conversation_id,
            classification,
        ):
            if isinstance(_chunk, str) and '"status": "error"' in _chunk:
                last_error = _chunk
    except Exception as exc:
        logger.exception("execute_flow_plan_sync failed flow=%s", flow_id)
        return {"status": "error", "message": str(exc)}

    if last_error:
        return {"status": "error", "message": "Plan execution reported an error."}
    return {"status": "success", "flow_id": flow_id, "plan_id": classification.get("plan_id")}


async def try_execute_scheduled_flow_via_planner(
    flow_id: str,
    user_id: str,
    *,
    user_prompt: str = "",
    trigger_type: str = "scheduled",
) -> Optional[Dict[str, Any]]:
    """Return result dict if planner path ran; None to fall back to reuse_flow_internal."""
    from services.flow_executions_service import FlowExecutionsService

    execution_id = await FlowExecutionsService.create_execution(str(user_id), flow_id, trigger_type)
    result = await execute_flow_plan_sync(
        flow_id,
        user_id,
        user_prompt=user_prompt,
        conversation_id=None,
    )
    if execution_id:
        result["flow_execution_id"] = execution_id
        status = "completed" if result.get("status") == "success" else "failed"
        try:
            await FlowExecutionsService.complete_execution(
                execution_id,
                status,
                None if status == "completed" else result.get("message"),
            )
        except Exception:
            pass
    return result
