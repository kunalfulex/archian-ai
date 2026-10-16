from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from agent.plan import ExecutionPlan, PlanStep
from agent.session import AgentSession
from handlers.classification_handler import get_active_intent_state

logger = logging.getLogger(__name__)

_ALLOWED_DATA_OPS = frozenset({"transform", "filter", "dedupe", "validate"})

_OPERATION_ALIASES = {
    "transformation": "transform",
    "deduplication": "dedupe",
    "validation": "validate",
    "cleanup_values": "validate",
    "cleanup": "validate",
}


def _canonicalize_operations_list(ops: List[str]) -> List[str]:
    """Map aliases, drop unknown ops and duplicates; preserve caller order (no fixed pipeline sort)."""
    seen: set[str] = set()
    out: List[str] = []
    for raw in ops:
        key = (raw or "").strip().lower()
        key = _OPERATION_ALIASES.get(key, key)
        if key == "multi":
            continue
        if key not in _ALLOWED_DATA_OPS or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _parse_step_order_json(content: str, allowed: List[str]) -> List[str] | None:
    """Parse LLM JSON {\"order\": [...]}; return a permutation of ``allowed`` or None."""
    raw = (content or "").strip()
    if not raw:
        return None
    try:
        from utils.openai_helpers import parse_openai_json_response

        data = parse_openai_json_response(raw)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None
    order = data.get("order")
    if not isinstance(order, list):
        return None

    canon_allowed = _canonicalize_operations_list(allowed)
    if len(canon_allowed) <= 1:
        return canon_allowed or None

    normalized: List[str] = []
    for item in order:
        key = _OPERATION_ALIASES.get((item or "").strip().lower(), (item or "").strip().lower())
        if key in _ALLOWED_DATA_OPS:
            normalized.append(key)

    if set(normalized) != set(canon_allowed) or len(normalized) != len(canon_allowed):
        return None
    return normalized


_ARCHITECT_TOOL_LABELS = {
    "extract_google_sheets": "Google Sheets extraction",
    "extract_sql": "SQL extraction",
    "extract_salesforce": "Salesforce extraction",
    "extract_file": "File extraction",
    "file_reextract": "File re-extraction",
    "metadata_deploy_salesforce": "Salesforce metadata deploy",
    "google_sheets_create_from_prompt": "Create Google Sheet",
    "google_sheets_update_metadata": "Update spreadsheet metadata",
    "google_sheets_info": "Google Sheets info",
    "gsheets_execute_operation": "Run Sheets operation",
    "sql_table_info": "SQL tables info",
    "salesforce_info_request": "Salesforce info",
    "general_ai_response": "Assistant reply",
    "map_fields": "Field mapping",
    "start_migration": "Start migration",
    "load_flow": "Load saved flow",
    "save_flow": "Save as flow",
    "schema_gate_assess": "Assess destination schema",
    "schema_gate_propose": "Review schema changes",
}


def _is_general_or_info_turn(snap: Dict[str, Any], session: AgentSession) -> bool:
    """Gate 0 plan_route + intent — not prompt keyword routing."""
    raw = snap.get("execution_plan")
    status = (snap.get("plan_status") or "").lower()
    if not status and isinstance(raw, dict):
        status = (raw.get("status") or "").lower()
    if isinstance(raw, dict) and raw.get("steps") and status in ("approved", "running"):
        return False

    plan_route = (snap.get("plan_route") or "").strip()
    if plan_route in ("info_path", "general_path"):
        return True
    intent = (snap.get("intent") or "").lower()
    if intent in ("general", "info"):
        return True
    return session.mode == "general"


class Planner:
    async def build_plan(self, prompt: str, session: AgentSession) -> ExecutionPlan:
        snap = session.classification_snapshot or {}

        recovery_plan = self._build_from_recovery_execution_plan(session)
        if recovery_plan is not None:
            return recovery_plan

        scoped_plan = self._build_from_execution_plan(session)
        if scoped_plan is not None:
            return scoped_plan

        if session.mode == "data_ops":
            return await self._build_data_ops_plan(prompt, session)

        if _is_general_or_info_turn(snap, session):
            step_info = self._resolve_info_architect_step(session)
            tool_id, arguments = step_info or ("general_ai_response", {})
            label = _ARCHITECT_TOOL_LABELS.get(tool_id)
            return ExecutionPlan(
                mode="general",
                steps=[
                    PlanStep(tool_id=tool_id, arguments=dict(arguments or {}), label=label),
                ],
                reasoning="general:conversational_or_info",
            )

        return ExecutionPlan(
            mode=session.mode,
            steps=[],
            reasoning="plan_scoped:awaiting_plan_execution",
        )

    def _build_from_recovery_execution_plan(
        self, session: AgentSession
    ) -> Optional[ExecutionPlan]:
        snap = session.classification_snapshot or {}
        if not snap.get("recovery_approved"):
            return None
        raw = snap.get("recovery_execution_plan") or snap.get("execution_plan")
        if not isinstance(raw, dict) or not raw.get("steps"):
            return None
        from services.planning.plan_runner import agent_plan_from_execution_plan

        plan = agent_plan_from_execution_plan(session, raw)
        if plan.steps:
            mapping_ctx = snap.get("recovery_mapping_context")
            if mapping_ctx:
                for step in plan.steps:
                    if step.tool_id == "map_fields":
                        step.arguments["mapping_context_summary"] = mapping_ctx
            plan.reasoning = "recovery:remediate_migration"
        return plan if plan.steps else None

    def _build_from_execution_plan(
        self, session: AgentSession
    ) -> Optional[ExecutionPlan]:
        from services.planning.plan_runner import agent_plan_from_execution_plan

        snap = session.classification_snapshot or {}

        if _is_general_or_info_turn(snap, session):
            return None
        if snap.get("needs_clarification"):
            return None
        raw = snap.get("execution_plan")
        if not isinstance(raw, dict) or not raw.get("steps"):
            from services.planning.map_flow_debug import log_map_flow

            log_map_flow(
                "execution_plan_missing",
                plan_status=snap.get("plan_status"),
                has_execution_plan=isinstance(raw, dict),
                step_count=len((raw or {}).get("steps") or []) if isinstance(raw, dict) else 0,
            )
            return None
        status = (snap.get("plan_status") or raw.get("status") or "").lower()
        if status not in ("approved", "running"):
            from services.planning.map_flow_debug import log_map_flow

            log_map_flow(
                "execution_plan_not_approved",
                plan_status=status,
                continuation_steps=snap.get("_continuation_steps") or snap.get("_pipeline_tokens"),
            )
            return None
        plan = agent_plan_from_execution_plan(session, raw)
        from services.planning.map_flow_debug import log_map_flow

        log_map_flow(
            "execution_plan_loaded",
            step_tools=[s.tool_id for s in (plan.steps or [])],
            active_job_id=session.active_job_id,
        )
        return plan

    def _resolve_info_architect_step(
        self, session: AgentSession
    ) -> Optional[Tuple[str, Dict]]:
        """Route schema/info questions to connector-specific info tools."""
        snap = session.classification_snapshot or {}
        intent = (snap.get("intent") or "").lower()
        source = (snap.get("source") or "unknown").lower()
        destination = (snap.get("destination") or "unknown").lower()
        source_name = snap.get("source_name")
        destination_name = snap.get("destination_name")

        plan_route = (snap.get("plan_route") or "").strip()
        is_info = intent == "info" or plan_route == "info_path"
        if not is_info:
            return None

        if source == "google_sheets" or destination == "google_sheets":
            sn = (source_name if source == "google_sheets" else destination_name) or source_name
            return ("google_sheets_info", {"workbook_name": sn})

        if source == "sql" or destination == "sql":
            sn = (source_name if source == "sql" else destination_name) or source_name
            return ("sql_table_info", {"source_name": sn})

        if source == "salesforce" or destination == "salesforce":
            sn = (source_name or "").strip()
            if sn and sn.lower() != "unknown":
                return (
                    "salesforce_info_request",
                    {"route_source": source, "route_source_name": source_name},
                )
            dn = (destination_name or "").strip()
            if dn and dn.lower() != "unknown":
                return (
                    "salesforce_info_request",
                    {"route_source": destination, "route_source_name": destination_name},
                )
            return ("salesforce_info_request", {"route_source": "salesforce", "route_source_name": None})

        return None

    async def _build_data_ops_plan(self, prompt: str, session: AgentSession) -> ExecutionPlan:
        if not session.active_job_id:
            return ExecutionPlan(
                mode=session.mode,
                steps=[],
                reasoning="data_ops: no_active_job",
            )
        return ExecutionPlan(
            mode=session.mode,
            steps=[
                PlanStep(
                    tool_id="run_data_ops_plan",
                    arguments={"parent_job_id": session.active_job_id},
                    label="Process extracted data",
                )
            ],
            reasoning="data_ops:plan_scoped",
        )

    async def _llm_order_steps(
        self, prompt: str, ops: List[str], _session: AgentSession
    ) -> List[str]:
        if len(ops) <= 1:
            return ops
        from services.llm import tasks as llm_tasks
        from services.llm.service import task_has_api_key

        if not await task_has_api_key(llm_tasks.DATA_OPS_STEP_ORDER):
            return ops

        allowed_csv = ", ".join(ops)
        user_msg = (
            f"User request:\n{prompt}\n\n"
            f"Reorder these data-processing steps. Use ONLY these ids, each exactly once, "
            f"in the best execution order: {allowed_csv}\n\n"
            'Return JSON only in this shape: {"order": ["...", "..."]}\n\n'
            "Rules:\n"
            "- If the user explicitly sequences steps (e.g. filter before merge), match that order.\n"
            "- If a transform removes columns that a filter still needs, run that filter before that transform.\n"
            "- When the prompt does not imply ordering, prefer transform then filter then dedupe then validate."
        )

        try:
            from services.llm import tasks as llm_tasks
            from services.llm.service import llm_complete_openai_compat

            response = await llm_complete_openai_compat(
                llm_tasks.DATA_OPS_STEP_ORDER,
                messages=[{"role": "user", "content": user_msg}],
                temperature=0,
                max_tokens=200,
            )
            content = (response.choices[0].message.content or "").strip()
            ordered = _parse_step_order_json(content, ops)
            if ordered:
                return ordered
        except Exception as e:
            logger.warning("[AGENT_STEP_PLANNER] ordering failed: %s", e)

        return ops


__all__ = [
    "Planner",
    "_canonicalize_operations_list",
]
