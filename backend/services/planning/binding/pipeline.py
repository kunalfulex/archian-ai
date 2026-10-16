"""Orchestrate retrieve → bind → validate for plan enrichment."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.planning.binding.bind_destination import (
    bind_destination_target_llm,
    should_bind_destination,
)
from services.planning.binding.bind_source import bind_source_extract_llm
from services.planning.binding.contract import SourceBindLLMOutput
from services.planning.binding.observability import log_binding
from services.planning.binding.retrieve import retrieve_entity_shortlist
from services.planning.binding.validate import validate_plan_binding
from services.planning.continuation_policy import (
    apply_active_job_continuation_flags,
    pin_source_from_active_job,
    should_skip_source_binding,
)
from services.planning.op_requirements import (
    read_plan_requirements,
    requirements_need_dest_bind,
    requirements_need_source_bind,
)
from services.planning.entity_resolver import apply_plan_contract_to_classification
from services.planning.migration_intent_schemas import PromptMigrationIntent
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import ExecutionPlan, PlanContract


async def run_plan_binding_pipeline(
    out: Dict[str, Any],
    plan: ExecutionPlan,
    *,
    user_id: str,
    prompt: str,
    parsed_goal: Dict[str, Any],
    catalog: ResourceCatalog,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List] = None,
) -> Tuple[Dict[str, Any], ExecutionPlan, Optional[PromptMigrationIntent], Optional[PlanContract]]:
    """
    LLM binding path: replaces resolve_migration_intent_for_plan + keyword ground.

    Returns (classification, plan, migration_intent, plan_contract).
    """
    parsed_goal = dict(parsed_goal or {})
    from services.planning.plan_context import effective_planning_prompt

    bind_prompt = effective_planning_prompt(
        prompt or "",
        conversation_history=conversation_history,
    )
    if conversation_history:
        from services.planning.session_facts import (
            apply_session_destination_pins,
            build_conversation_session_facts,
            enrich_destination_phrase_from_conversation_if_needed,
        )

        session_facts = build_conversation_session_facts(conversation_history)
        parsed_goal = await enrich_destination_phrase_from_conversation_if_needed(
            parsed_goal,
            out,
            session_facts,
            user_id=str(user_id),
            prompt=prompt,
            conversation_history=conversation_history,
            conversation_id=conversation_id,
        )
        out, parsed_goal = apply_session_destination_pins(out, parsed_goal, session_facts)
        out["_conversation_session_facts"] = session_facts

    out = apply_active_job_continuation_flags(out)
    plan_route = (out.get("plan_route") or "").strip()
    if plan_route in ("info_path", "general_path"):
        return out, plan, None, None
    from services.file_extraction.pending_upload import file_source_awaiting_extract

    plan_reqs = read_plan_requirements(out)
    need_source_bind = requirements_need_source_bind(plan_reqs, out)
    skip_source = not need_source_bind or (
        out.get("plan_route") != "fresh_path"
        and should_skip_source_binding(out, parsed_goal, prompt=prompt)
    )
    source_pinned = None
    if skip_source and need_source_bind:
        source_pinned, pinned_mi = pin_source_from_active_job(out)
        if pinned_mi.driving_entity_key:
            out["source_table_name"] = out.get("source_table_name") or pinned_mi.driving_entity_key
        log_binding(
            "bind_source",
            f"skipped active_job={out.get('active_job_id')} entity={source_pinned.entity_key or '-'}",
        )
    elif not need_source_bind:
        log_binding("bind_source", "skipped (not required by plan requirements)")

    log_binding(
        "pipeline",
        f"start ops={parsed_goal.get('operations')} "
        f"data_ops={parsed_goal.get('data_operations')} "
        f"src={out.get('source_name')} dest={out.get('destination_name')}",
    )

    source_candidates: list = []
    source_ctx: Dict[str, Any] = {}
    if not skip_source:
        source_candidates, _, source_ctx = await retrieve_entity_shortlist(
            user_id,
            out,
            role="source",
            prompt=bind_prompt,
            parsed_goal=parsed_goal,
        )

    bind_dest_coro = None
    dest_candidates = []
    dest_pinned = None
    from services.planning.entity_pins import pin_destination_from_classification

    dest_pinned = pin_destination_from_classification(out)
    need_dest_bind = requirements_need_dest_bind(plan_reqs) or should_bind_destination(
        out, parsed_goal
    )
    if file_source_awaiting_extract(out):
        need_dest_bind = False
        log_binding(
            "bind_dest",
            "skipped (file source — destination entity after extract)",
        )
    if need_dest_bind and dest_pinned:
        log_binding(
            "bind_dest",
            f"skipped user_confirmed entity={dest_pinned.entity_key or '-'}",
        )
        need_dest_bind = False
    if need_dest_bind:
        from services.planning.destination_catalog_validate import (
            destination_connector_slot_satisfied,
            read_needs_destination,
        )

        if read_needs_destination(out, parsed_goal) and not destination_connector_slot_satisfied(
            out, catalog, parsed_goal=parsed_goal
        ):
            need_dest_bind = False
            log_binding("bind_dest", "skipped (destination connector not validated)")
    if need_dest_bind:
        job_source_key: Optional[str] = None
        job_source_label: Optional[str] = None
        migration_intent_raw = (plan.metadata or {}).get("migration_intent")
        if not isinstance(migration_intent_raw, dict):
            migration_intent_raw = (
                out.get("migration_intent")
                if isinstance(out.get("migration_intent"), dict)
                else None
            )
        if out.get("active_job_id"):
            pinned_src, pinned_mi = pin_source_from_active_job(out)
            if pinned_src.entity_key:
                job_source_key = pinned_src.entity_key
                job_source_label = pinned_src.entity_label or pinned_src.catalog_label
            if pinned_mi.driving_entity_key and not migration_intent_raw:
                migration_intent_raw = pinned_mi.model_dump(mode="json")
        contract_raw = (plan.metadata or {}).get("plan_contract") or out.get("plan_contract")
        if isinstance(contract_raw, dict):
            src_side = contract_raw.get("source") or {}
            if isinstance(src_side, dict) and not job_source_key:
                job_source_key = (src_side.get("entity_key") or "").strip() or None
                job_source_label = (
                    (src_side.get("entity_label") or src_side.get("catalog_label") or "").strip()
                    or job_source_key
                )

        dest_candidates, _, _ = await retrieve_entity_shortlist(
            user_id,
            out,
            role="destination",
            prompt=bind_prompt,
            parsed_goal=parsed_goal,
            migration_intent=migration_intent_raw,
            source_entity_key=job_source_key,
            source_entity_label=job_source_label,
        )
        bind_dest_coro = bind_destination_target_llm(
            bind_prompt,
            user_id=str(user_id),
            classification=out,
            candidates=dest_candidates,
            parsed_goal=parsed_goal,
            conversation_id=conversation_id,
            conversation_history=conversation_history,
            source_entity_key=job_source_key,
            source_entity_label=job_source_label,
        )
    elif not dest_pinned:
        log_binding("bind_dest", "skipped (extract-only or no destination)")

    if need_source_bind:
        if skip_source:
            source_bind = SourceBindLLMOutput(
                entity_key=(source_pinned.entity_key if source_pinned else "") or "",
                extract_field_scope="explicit",
                confidence=float(source_pinned.confidence if source_pinned else 0.0),
            )
        else:
            source_bind = await bind_source_extract_llm(
                bind_prompt,
                user_id=str(user_id),
                classification=out,
                candidates=source_candidates,
                parsed_goal=parsed_goal,
                conversation_id=conversation_id,
            )
    else:
        source_bind = SourceBindLLMOutput(
            entity_key="",
            extract_field_scope="unspecified",
            confidence=0.0,
        )

    dest_bind = None
    if bind_dest_coro is not None:
        dest_bind = await bind_dest_coro

    binding = await validate_plan_binding(
        classification=out,
        prompt=bind_prompt,
        parsed_goal=parsed_goal,
        source_bind=source_bind,
        dest_bind=dest_bind,
        source_candidates=source_candidates,
        dest_candidates=dest_candidates,
        user_id=str(user_id),
        source_pinned=source_pinned,
        dest_pinned=dest_pinned,
        catalog_ctx=source_ctx,
        source_required=need_source_bind,
    )

    migration_intent = binding.migration_intent
    plan_contract = binding.contract
    out = apply_plan_contract_to_classification(out, plan_contract)
    from services.planning.goal_reconciliation import reconcile_goal_after_catalog_binding

    out, parsed_goal, _ = reconcile_goal_after_catalog_binding(
        out, parsed_goal, plan_contract
    )
    plan.metadata["plan_contract"] = plan_contract.model_dump(mode="json")
    if migration_intent:
        plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
        out["migration_intent"] = migration_intent.model_dump(mode="json")

    if binding.field_clarification and migration_intent:
        from services.planning.extract_field_clarification import (
            finalize_extract_field_clarification,
        )

        migration_intent = await finalize_extract_field_clarification(
            migration_intent,
            user_id=str(user_id),
            classification=out,
            prompt=prompt,
        )
        plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
        out["migration_intent"] = migration_intent.model_dump(mode="json")

    log_binding("pipeline", "binding complete → plan_assembly")
    from services.planning.planning_checkpoint import persist_planning_checkpoint

    out, plan = persist_planning_checkpoint(out, plan, parsed_goal=parsed_goal)
    return out, plan, migration_intent, plan_contract
