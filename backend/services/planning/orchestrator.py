"""Planning pipeline orchestration (PLAN_SCOPE → execution plan)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid

from services.planning.gap_detector import (
    auto_pin_single_source_connector,
    sanitize_unconfirmed_google_destination,
)
from services.planning.clarification_apply import apply_clarification_selections
from services.planning.schemas import merge_plan_into_classification
from services.planning.plan_builder import (
    build_plan_steps,
    finalize_plan_status,
)
from services.planning.resource_catalog import ResourceCatalog, load_resource_catalog
from services.planning.connector_guards import validate_classification_resources
from services.planning.schemas import ExecutionPlan, ResourceRef
from services.planning.plan_context import pipeline_state_from_plan


def build_catalog_from_classify_fetch(
    *,
    sql_connection_names: List[str],
    salesforce_connections_json: Dict[str, Any],
    google_sheet_rows: List[Dict[str, Any]],
    has_google_sheets: bool,
) -> ResourceCatalog:
    """Backward-compatible alias — prefer ClassifyContext.catalog from load_classify_context."""
    from services.intent.classify_context import build_catalog_from_classify_fetch as _build

    return _build(
        sql_connection_names=sql_connection_names,
        salesforce_connections_json=salesforce_connections_json,
        google_sheet_rows=google_sheet_rows,
        has_google_sheets=has_google_sheets,
    )


from services.intent.plan_assembly import assemble_migration_plan_tail as _assemble_migration_plan_tail


def _emit_post_kernel_scope_activity(out: Dict[str, Any]) -> None:
    """Scope milestones after planning kernel resolves connectors (requires active activity bus)."""
    from services.planning.planning_activity import (
        emit_planning_activity,
        format_destination_detail,
        format_source_detail,
    )

    src_detail = format_source_detail(out)
    if src_detail:
        emit_planning_activity(
            "resolve_source",
            "Identified source",
            "done",
            segment="scope",
            detail=src_detail,
        )
    dest_detail = format_destination_detail(out)
    if dest_detail:
        emit_planning_activity(
            "resolve_destination",
            "Identified destination",
            "done",
            segment="scope",
            detail=dest_detail,
        )
    emit_planning_activity(
        "bind_connectors",
        "Matching connectors to your account",
        "done",
        segment="scope",
    )


def _emit_finalize_plan_activity(out: Dict[str, Any], plan: ExecutionPlan) -> None:
    """Plan-build milestones after finalize_planner_session (requires active activity bus)."""
    from services.planning.planning_activity import (
        emit_plan_steps_from_plan,
        emit_planning_activity,
    )

    emit_plan_steps_from_plan(plan)
    if plan.steps:
        emit_planning_activity(
            "plan_ready",
            f"Plan ready ({len(plan.steps)} steps)",
            "done",
            segment="plan_build",
        )
        emit_planning_activity(
            "assemble_plan",
            "Assembling execution plan",
            "done",
            segment="plan_build",
        )
    elif plan.blocking_clarifications() or out.get("needs_clarification"):
        emit_planning_activity(
            "clarification_needed",
            "Need your input to continue",
            "done",
            segment="plan_build",
        )
        emit_planning_activity(
            "assemble_plan",
            "Assembling execution plan",
            "done",
            segment="plan_build",
        )
    else:
        out["_planning_terminal_deferred"] = True
        emit_planning_activity(
            "finalize_plan",
            "Finalizing execution plan",
            "running",
            segment="plan_build",
        )


async def _repair_stalled_migration_plan(
    out: Dict[str, Any],
    plan: ExecutionPlan,
    *,
    user_id: str,
    prompt: str,
    catalog: ResourceCatalog,
    parsed_goal: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    plan_auto_run: bool = False,
) -> tuple[Dict[str, Any], ExecutionPlan]:
    """
    Migrate flows can finish all connector picks yet have no steps and no open clarifications
    (e.g. column pick or destination entity never surfaced). Re-run tail / field steps instead
    of leaving the UI blank.
    """
    from services.planning.connector_guards import (
        classification_source_endpoint_resolved,
    )
    from services.planning.binding.ensure import ensure_plan_binding_if_needed
    from services.planning.binding.guards import load_plan_contract
    from services.planning.extract_field_clarification import (
        needs_extract_field_clarification,
        resolve_field_clarification_for_plan,
    )
    from services.planning.goal_parser import enrich_scope_from_llm
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.planning.observability import log_planner_debug
    from services.planning.op_requirements import DESTINATION_ENTITY, read_plan_requirements
    from services.planning.plan_extract_gates import ready_for_extract_field_step

    if plan.blocking_clarifications() or plan.steps:
        return out, plan
    tokens = out.get("_pipeline_tokens") or []
    if not any(t in tokens for t in ("map", "migrate", "create", "extract")):
        return out, plan
    reqs = read_plan_requirements(out, parsed_goal=parsed_goal)
    if (
        DESTINATION_ENTITY not in {s.slot for s in reqs.all_slots()}
        and "create" not in tokens
    ):
        return out, plan
    if not classification_source_endpoint_resolved(out):
        return out, plan

    log_planner_debug(
        "repair_stalled_migration_started",
        user_id=str(user_id),
        conversation_id=conversation_id,
        classification=out,
        plan=plan,
    )

    pg = parsed_goal or await enrich_scope_from_llm(
        out,
        prompt,
        user_id=str(user_id),
        conversation_id=conversation_id,
    )
    migration_intent = None
    raw_intent = (plan.metadata or {}).get("migration_intent") or out.get("migration_intent")
    if raw_intent:
        migration_intent = PromptMigrationIntent.model_validate(raw_intent)

    plan_contract = load_plan_contract(plan, out)
    if classification_source_endpoint_resolved(out):
        out, plan, migration_intent, plan_contract = await ensure_plan_binding_if_needed(
            out,
            plan,
            parsed_goal=pg,
            user_id=str(user_id),
            prompt=prompt,
            catalog=catalog,
            conversation_id=conversation_id,
            migration_intent=migration_intent,
        )
        if plan_contract:
            plan.metadata["plan_contract"] = plan_contract.model_dump(mode="json")

    if ready_for_extract_field_step(out, plan_contract, pg) and migration_intent:
        migration_intent = await resolve_field_clarification_for_plan(
            migration_intent,
            user_id=str(user_id),
            classification=out,
            prompt=prompt,
            plan_contract=plan_contract,
        )
        needs_fields = bool(
            migration_intent.needs_extract_clarification
            or needs_extract_field_clarification(migration_intent, prompt)
        )
        if needs_fields and not out.get("extract_fields_confirmed"):
            plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
            from services.planning.clarification_sync import sync_plan_clarifications
            from services.planning.op_requirements import read_plan_requirements

            reqs = read_plan_requirements(out, parsed_goal=pg)
            plan = await sync_plan_clarifications(
                plan,
                out,
                user_id=str(user_id),
                requirements=reqs,
                plan_contract=plan_contract,
                parsed_goal=pg,
                migration_intent=migration_intent,
                catalog=catalog,
                prompt=prompt,
                advance_one=True,
            )
            plan.status = "awaiting_clarification"
            log_planner_debug(
                "repair_appended_extract_fields",
                user_id=str(user_id),
                conversation_id=conversation_id,
                classification=out,
                plan=plan,
            )
            return out, plan

    out, plan, _, _ = await _assemble_migration_plan_tail(
        out,
        plan,
        user_id=str(user_id),
        prompt=prompt,
        parsed_goal=pg,
        catalog=catalog,
        migration_intent=migration_intent,
    )
    plan = finalize_plan_status(plan, plan_auto_run=plan_auto_run)
    log_planner_debug(
        "repair_stalled_migration_completed",
        user_id=str(user_id),
        conversation_id=conversation_id,
        classification=out,
        plan=plan,
        extra={"step_count": len(plan.steps or [])},
    )
    return out, plan


async def _apply_connector_enrichment(
    out: Dict[str, Any],
    *,
    prompt: str,
    user_id: str,
    catalog: Optional[ResourceCatalog] = None,
    conversation_history: Optional[List[Dict]] = None,
) -> tuple[Dict[str, Any], ResourceCatalog]:
    """Resolve connectors once; validate catalog membership."""
    cat = catalog or await load_resource_catalog(user_id)

    out = sanitize_unconfirmed_google_destination(out, prompt, cat)
    from services.planning.connector_endpoint_confirm import (
        sanitize_unconfirmed_connector_endpoints,
    )

    out = sanitize_unconfirmed_connector_endpoints(out, prompt, cat)
    out = validate_classification_resources(out, cat)
    return out, cat


async def enrich_classification_with_planning(
    classification: Dict[str, Any],
    *,
    user_id: str,
    prompt: str,
    catalog: Optional[ResourceCatalog] = None,
    plan_auto_run: bool = False,
    conversation_history: Optional[List[Dict]] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Attach execution plan / clarification metadata to classification.
    Expects classification to already have resource hints applied.
    """
    out = dict(classification or {})

    plan_route = (out.get("plan_route") or "").strip()
    intent_l = (out.get("intent") or "").lower()
    if plan_route in ("info_path", "general_path") or intent_l in ("info", "general"):
        cat = catalog or await load_resource_catalog(user_id)
        if plan_route == "info_path" or intent_l == "info":
            from services.planning.intent_routing import enrich_info_query_classification

            return await enrich_info_query_classification(
                out, prompt, cat, user_id=str(user_id)
            )
        from services.intent.conversation_turn import enrich_general_turn

        return await enrich_general_turn(
            prompt,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
            conversational_turn=out.get("conversational_turn"),
            off_topic=out.get("off_topic"),
        )

    out, cat = await _apply_connector_enrichment(
        out,
        prompt=prompt,
        user_id=str(user_id),
        catalog=catalog,
        conversation_history=conversation_history,
    )

    from services.planning.destination_object_enrichment import (
        enrich_destination_target_from_prompt,
        should_defer_destination_object_enrichment_early,
    )

    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        status="draft",
        goal_summary=None,
        metadata={"classification": dict(out)},
    )

    if out.get("flow_id"):
        from services.planning.flow_steps import apply_saved_flow_to_classification

        out = await apply_saved_flow_to_classification(out, str(out["flow_id"]), str(user_id))

    from services.planning.goal_parser import enrich_scope_from_llm
    from services.planning.continuation_policy import (
        apply_fresh_extract_goal_normalization,
    )
    from services.planning.plan_scope import (
        resolve_pipeline_tokens_from_goal,
        sync_scope_into_classification,
    )

    parsed_goal = await enrich_scope_from_llm(
        out,
        prompt,
        user_id=str(user_id),
        conversation_id=conversation_id,
        conversation_history=conversation_history,
    )
    from services.planning.scope_validator import validate_and_normalize_plan_scope

    parsed_goal, out = validate_and_normalize_plan_scope(parsed_goal, out, prompt)
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
    out, parsed_goal = apply_fresh_extract_goal_normalization(out, parsed_goal, prompt)
    pipeline_tokens = resolve_pipeline_tokens_from_goal(out, parsed_goal)
    out = sync_scope_into_classification(out, parsed_goal, pipeline_tokens)
    if parsed_goal.get("operations"):
        out["operations"] = parsed_goal["operations"]

    from services.planning.planning_activity import (
        emit_planning_activity,
        format_destination_detail,
        format_operations_detail,
        format_source_detail,
        emit_plan_steps_from_plan,
    )

    ops_detail = format_operations_detail(parsed_goal)
    if ops_detail:
        emit_planning_activity(
            "detect_operations",
            "Detected operations",
            "done",
            segment="scope",
            detail=ops_detail,
        )

    emit_planning_activity(
        "bind_connectors",
        "Matching connectors to your account",
        "running",
        segment="scope",
    )

    from services.planning.observability import log_plan_scope_trace

    log_plan_scope_trace(
        "synced_to_classification",
        conversation_id=conversation_id,
        user_id=str(user_id),
        operations=parsed_goal.get("operations"),
        pipeline_ops=pipeline_tokens,
        needs_source=parsed_goal.get("needs_source"),
        needs_destination=parsed_goal.get("needs_destination"),
        source_phrase=parsed_goal.get("source_phrase"),
        destination_phrase=parsed_goal.get("destination_phrase"),
    )

    from services.file_extraction.pending_upload import is_file_source
    from services.planning.plan_scope import goal_requests_map_or_migrate

    if is_file_source(out) and goal_requests_map_or_migrate(parsed_goal):
        out["session_action"] = "fresh_full_pipeline"

    out = auto_pin_single_source_connector(out, cat)
    from services.planning.destination_catalog_validate import (
        clear_destination_when_not_needed,
        strip_unvalidated_destination_confirm_flags,
    )

    out = clear_destination_when_not_needed(out, parsed_goal)
    out = strip_unvalidated_destination_confirm_flags(out)

    if not should_defer_destination_object_enrichment_early(out):
        from services.planning.destination_catalog_validate import read_needs_destination

        if read_needs_destination(out, parsed_goal):
            out = await enrich_destination_target_from_prompt(
                out, user_id=str(user_id), prompt=prompt
            )

    out["_plan_scope"] = parsed_goal
    from services.planning.planning_checkpoint import persist_planning_checkpoint

    out, plan = persist_planning_checkpoint(out, plan, parsed_goal=parsed_goal)
    from services.planning.connector_guards import apply_migrate_destination_guards
    from services.planning.create_metadata import (
        apply_create_metadata_to_classification,
        clear_catalog_workbook_pins_for_create,
    )

    out, parsed_goal = apply_migrate_destination_guards(out, parsed_goal, None)
    out = apply_create_metadata_to_classification(out, parsed_goal)
    out = clear_catalog_workbook_pins_for_create(out, parsed_goal)

    from services.planning.multi_source import apply_multi_source_hints

    out = apply_multi_source_hints(out, prompt, cat)
    out = clear_catalog_workbook_pins_for_create(out, parsed_goal)
    out = sync_scope_into_classification(
        out,
        parsed_goal,
        resolve_pipeline_tokens_from_goal(out, parsed_goal),
    )
    if parsed_goal.get("operations"):
        out["operations"] = parsed_goal["operations"]

    from services.planning.clarification_pipeline import PlannerSession
    from services.planning.planning_kernel import run_planning_kernel

    session = PlannerSession(
        classification=out,
        plan=plan,
        parsed_goal=parsed_goal,
        prompt=prompt,
        conversation_history=conversation_history,
    )
    from services.planning.destination_catalog_validate import read_needs_destination
    from services.planning.source_catalog_validate import read_needs_source

    session = await run_planning_kernel(
        session,
        plan_route="fresh_path",
        user_id=str(user_id),
        catalog=cat,
        conversation_id=conversation_id,
        plan_auto_run=plan_auto_run,
        run_source_resolver=read_needs_source(out, parsed_goal),
        run_destination_resolver=read_needs_destination(out, parsed_goal),
    )
    out = session.classification
    plan = session.plan

    src_detail = format_source_detail(out)
    if src_detail:
        emit_planning_activity(
            "resolve_source",
            "Identified source",
            "done",
            segment="scope",
            detail=src_detail,
        )
    dest_detail = format_destination_detail(out)
    if dest_detail:
        emit_planning_activity(
            "resolve_destination",
            "Identified destination",
            "done",
            segment="scope",
            detail=dest_detail,
        )
    emit_planning_activity(
        "bind_connectors",
        "Matching connectors to your account",
        "done",
        segment="scope",
    )
    emit_planning_activity(
        "assemble_plan",
        "Assembling execution plan",
        "running",
        segment="plan_build",
    )

    from services.planning.planner_session_loop import finalize_planner_session

    session = await finalize_planner_session(
        session,
        user_id=str(user_id),
        catalog=cat,
        plan_auto_run=plan_auto_run,
        conversation_id=conversation_id,
    )
    out = session.classification
    plan = session.plan

    emit_plan_steps_from_plan(plan)
    if plan.steps:
        emit_planning_activity(
            "plan_ready",
            f"Plan ready ({len(plan.steps)} steps)",
            "done",
            segment="plan_build",
        )
        emit_planning_activity(
            "assemble_plan",
            "Assembling execution plan",
            "done",
            segment="plan_build",
        )
    elif plan.blocking_clarifications() or out.get("needs_clarification"):
        emit_planning_activity(
            "clarification_needed",
            "Need your input to continue",
            "done",
            segment="plan_build",
        )
        emit_planning_activity(
            "assemble_plan",
            "Assembling execution plan",
            "done",
            segment="plan_build",
        )
    else:
        # Assembly may still recover on emit — defer terminal failure/success.
        out["_planning_terminal_deferred"] = True
        emit_planning_activity(
            "finalize_plan",
            "Finalizing execution plan",
            "running",
            segment="plan_build",
        )

    if not plan.blocking_clarifications() and plan.steps:
        from services.pipeline_learning.service import PipelineLearningService

        if PipelineLearningService.enabled():
            PipelineLearningService.schedule_capture(
                lambda: PipelineLearningService.capture_plan_proposed(
                    user_id=str(user_id),
                    conversation_id=str(conversation_id) if conversation_id else None,
                    prompt=prompt,
                    classification=dict(out),
                )
            )
    out["pipeline_state"] = pipeline_state_from_plan(
        plan.status,
        needs_clarification=bool(out.get("needs_clarification")),
    )
    from services.planning.observability import log_planner_event

    log_planner_event(
        "plan_enriched",
        user_id=str(user_id),
        plan_id=out.get("plan_id"),
        plan_status=out.get("plan_status"),
        pipeline_state=out.get("pipeline_state"),
        pipeline_label=plan.metadata.get("pipeline_label"),
        clarification_types=[c.clarification_type for c in plan.clarifications],
    )
    from services.planning.plan_extract_gates import assess_planning_state

    migration_intent_raw = (plan.metadata or {}).get("migration_intent")
    artifact = assess_planning_state(
        out,
        plan,
        migration_intent=migration_intent_raw,
        original_prompt=prompt,
    )
    out["planning_artifact"] = artifact.model_dump(mode="json")
    log_planner_event(
        "planning_state",
        user_id=str(user_id),
        plan_id=out.get("plan_id"),
        extra={
            "readiness": artifact.readiness.value,
            "next_action": artifact.next_action,
            "gap_count": len(artifact.gaps),
        },
    )
    return out


async def resume_classification_after_clarifications(
    pending: Dict[str, Any],
    selections: List[Dict[str, Any]],
    *,
    user_id: str,
    prompt: Optional[str] = None,
    reply_text: Optional[str] = None,
    catalog: Optional[ResourceCatalog] = None,
    plan_auto_run: bool = False,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Rebuild classification after user answers planner clarifications (clicks or chat)."""
    from services.planning.clarification_reply import parse_clarification_reply
    from services.planning.observability import log_planner_debug
    from services.planning.resource_catalog import load_resource_catalog

    base = dict(pending.get("classification") or {})
    plan_data = pending.get("execution_plan") or {}
    plan = ExecutionPlan.model_validate(plan_data)
    from services.planning.planning_checkpoint import hydrate_classification_from_checkpoint

    base = hydrate_classification_from_checkpoint(base, plan)
    open_clarifications = pending.get("clarifications") or base.get("clarifications") or []
    original = (pending.get("original_prompt") or "").strip()

    resolved_selections = list(selections or [])
    if reply_text and not resolved_selections:
        resolved_selections = parse_clarification_reply(reply_text, open_clarifications)
        if not resolved_selections:
            from services.planning.binding.clarification_resolve import (
                resolve_entity_clarification_selections,
            )
            from services.planning.plan_scope import read_plan_scope

            resolved_selections = await resolve_entity_clarification_selections(
                reply_text,
                open_clarifications,
                user_id=str(user_id),
                classification=base,
                prompt=original or None,
                conversation_id=pending.get("conversation_id"),
                parsed_goal=read_plan_scope(base) or None,
            )

    selection_types = [
        (open_clarifications or [{}])[0].get("clarification_type")
        if len(resolved_selections) == 1 and not open_clarifications
        else None
    ]
    for clar in open_clarifications:
        if any(s.get("clarification_id") == clar.get("id") for s in resolved_selections):
            ctype = clar.get("clarification_type") if isinstance(clar, dict) else getattr(clar, "clarification_type", None)
            if ctype:
                selection_types.append(ctype)
    selection_types = [t for t in selection_types if t]

    cat = catalog or await load_resource_catalog(user_id)
    base["_planning_catalog"] = cat

    log_planner_debug(
        "resume_started",
        user_id=str(user_id),
        conversation_id=pending.get("conversation_id"),
        classification=base,
        plan=plan,
        selection_types=selection_types or None,
        extra={"selection_count": len(resolved_selections)},
    )

    from services.planning.planning_activity import (
        emit_planning_activity,
        format_operations_detail,
    )

    emit_planning_activity(
        "merge_clarifications",
        "Merging your answers",
        "running",
        segment="scope",
    )

    out, plan = apply_clarification_selections(
        base, plan, resolved_selections, catalog=cat
    )
    emit_planning_activity(
        "merge_clarifications",
        "Merging your answers",
        "done",
        segment="scope",
    )
    from services.planning.clarification_queue import merge_plan_clarification_queue_async
    from services.planning.op_requirements import read_plan_requirements
    from services.planning.plan_scope import read_plan_scope
    from services.planning.binding.guards import load_plan_contract

    _resume_goal = read_plan_scope(out) or {}
    _plan_contract = load_plan_contract(plan, out)
    _reqs = read_plan_requirements(out, parsed_goal=_resume_goal)
    plan = await merge_plan_clarification_queue_async(
        plan,
        out,
        _reqs,
        user_id=str(user_id),
        plan_contract=_plan_contract,
        parsed_goal=_resume_goal,
        catalog=cat,
        prompt=(pending.get("original_prompt") or prompt or "").strip() or None,
    )
    log_planner_debug(
        "resume_after_apply",
        user_id=str(user_id),
        conversation_id=pending.get("conversation_id"),
        classification=out,
        plan=plan,
        selection_types=selection_types or None,
    )
    from services.planning.clarification_apply import (
        _restore_planner_endpoint_pins,
        _sync_migration_intent_to_classification,
    )

    prior_plan = ExecutionPlan.model_validate(plan_data)
    out, plan = _restore_planner_endpoint_pins(
        out, base, prior_plan=prior_plan, plan=plan
    )
    out = _sync_migration_intent_to_classification(out, (plan.metadata or {}).get("migration_intent"))
    if resolved_selections:
        out.pop("_endpoint_ambiguous", None)

    original = (pending.get("original_prompt") or prompt or "").strip()
    planning_prompt = original or (reply_text or "").strip()

    from services.planning.create_metadata import (
        apply_create_metadata_to_classification,
        classification_requests_new_spreadsheet,
        clear_catalog_workbook_pins_for_create,
    )
    from services.planning.plan_scope import read_plan_scope

    parsed_goal_after_apply = read_plan_scope(out) or {}
    out = apply_create_metadata_to_classification(out, parsed_goal_after_apply)
    out = clear_catalog_workbook_pins_for_create(out, parsed_goal_after_apply)
    create_active = classification_requests_new_spreadsheet(out, parsed_goal_after_apply)

    from services.intent.resume import resume_intent_after_clarification

    out = await resume_intent_after_clarification(
        out,
        prompt=planning_prompt,
        user_id=str(user_id),
        catalog=cat,
        selection_types=selection_types,
        conversation_id=pending.get("conversation_id"),
    )

    if original and resolved_selections:
        # User just disambiguated a workbook/org — do not re-run phrase matching on the full prompt.
        out["_endpoints_resolved"] = True

    if out.get("flow_id"):
        from services.planning.flow_steps import apply_saved_flow_to_classification

        out = await apply_saved_flow_to_classification(out, str(out["flow_id"]), str(user_id))

    from services.planning.connector_endpoint_confirm import (
        sanitize_unconfirmed_connector_endpoints,
    )

    out = sanitize_unconfirmed_connector_endpoints(out, planning_prompt, cat)
    out = auto_pin_single_source_connector(out, cat)

    parsed_goal_repair: Optional[Dict[str, Any]] = None

    from services.planning.connector_guards import classification_source_endpoint_resolved
    from services.planning.clarification_queue import is_metadata_clarification_resume
    from services.planning.plan_scope import (
        merge_plan_scope_preserving_export_ops,
        read_plan_scope,
    )
    from services.planning.planning_checkpoint import resolved_goal_for_session

    prior_scope = resolved_goal_for_session(
        out,
        parsed_goal=parsed_goal_after_apply,
        plan=plan,
    )
    metadata_only_resume = is_metadata_clarification_resume(selection_types)
    continuation_path = (out.get("plan_route") or base.get("plan_route")) == "continuation_path"
    if metadata_only_resume:
        from services.planning.continuation_policy import RESUME_MODE_CLARIFICATION

        out["_resume_mode"] = RESUME_MODE_CLARIFICATION
    elif continuation_path:
        from services.planning.continuation_policy import RESUME_MODE_SCOPE_TURN

        out["_resume_mode"] = RESUME_MODE_SCOPE_TURN
    active_job_id = (out.get("active_job_id") or base.get("active_job_id") or "").strip()
    resume_job = None
    if active_job_id:
        from services.intent.job_context import load_job_context

        resume_job = await load_job_context(
            user_id=str(user_id),
            job_id=active_job_id,
            conversation_id=pending.get("conversation_id"),
            conversation_history=conversation_history or pending.get("conversation_history"),
        )
        if resume_job and continuation_path:
            from services.intent.job_authority import apply_job_authority

            out = apply_job_authority(out, resume_job)

    if metadata_only_resume:
        parsed_goal = dict(prior_scope)
        from services.planning.plan_scope import (
            resolve_pipeline_tokens_from_goal,
            sync_scope_into_classification,
        )

        pipeline_tokens = resolve_pipeline_tokens_from_goal(out, parsed_goal)
        out = sync_scope_into_classification(out, parsed_goal, pipeline_tokens)
        out["_plan_scope"] = parsed_goal
        if parsed_goal.get("operations"):
            out["operations"] = parsed_goal["operations"]
        parsed_goal_repair = parsed_goal
        log_planner_debug(
            "resume_metadata_clarifications",
            user_id=str(user_id),
            conversation_id=pending.get("conversation_id"),
            classification=out,
            plan=plan,
            selection_types=selection_types or None,
            extra={"goal_ops": list(parsed_goal.get("operations") or [])},
        )
    elif classification_source_endpoint_resolved(out):
        from services.planning.connector_guards import apply_migrate_destination_guards
        from services.planning.create_metadata import apply_create_metadata_to_classification

        refresh_detail = "Continuing from active job" if continuation_path else None
        emit_planning_activity(
            "refresh_scope",
            "Refreshing plan scope" if continuation_path else "Updating plan scope",
            "running",
            segment="scope",
            detail=refresh_detail,
        )
        if continuation_path and active_job_id and resume_job and not metadata_only_resume:
            from services.planning.goal_parser import enrich_continuation_scope_from_llm

            parsed_goal = await enrich_continuation_scope_from_llm(
                out,
                planning_prompt,
                user_id=str(user_id),
                job=resume_job,
                conversation_id=pending.get("conversation_id"),
                conversation_history=conversation_history or pending.get("conversation_history"),
                session_decision=out.get("_session_continuation") or out,
            )
        elif continuation_path and prior_scope.get("operations"):
            parsed_goal = dict(prior_scope)
        else:
            from services.planning.goal_parser import enrich_scope_from_llm

            parsed_goal = await enrich_scope_from_llm(
                out,
                planning_prompt,
                user_id=str(user_id),
                conversation_id=pending.get("conversation_id"),
            )
            parsed_goal = merge_plan_scope_preserving_export_ops(prior_scope, parsed_goal)
        emit_planning_activity(
            "refresh_scope",
            "Refreshing plan scope" if continuation_path else "Updating plan scope",
            "done",
            segment="scope",
            detail=refresh_detail,
        )
        log_planner_debug(
            "resume_after_plan_scope",
            user_id=str(user_id),
            conversation_id=pending.get("conversation_id"),
            classification=out,
            plan=plan,
            extra={
                "prior_ops": list(prior_scope.get("operations") or []),
                "refreshed_ops": list(parsed_goal.get("operations") or []),
            },
        )
        from services.planning.scope_validator import validate_and_normalize_plan_scope

        parsed_goal, out = validate_and_normalize_plan_scope(
            parsed_goal, out, planning_prompt
        )
        out["_plan_scope"] = parsed_goal
        if parsed_goal.get("operations"):
            out["operations"] = parsed_goal["operations"]
        out, parsed_goal = apply_migrate_destination_guards(out, parsed_goal, None)
        out = apply_create_metadata_to_classification(out, parsed_goal)
        out = clear_catalog_workbook_pins_for_create(out, parsed_goal)
        out["_plan_scope"] = parsed_goal
        parsed_goal_repair = parsed_goal
        from services.planning.continuation_policy import apply_fresh_extract_goal_normalization

        out, parsed_goal = apply_fresh_extract_goal_normalization(
            out, parsed_goal, planning_prompt
        )
        from services.planning.plan_scope import (
            resolve_pipeline_tokens_from_goal,
            sync_scope_into_classification,
        )

        pipeline_tokens = resolve_pipeline_tokens_from_goal(out, parsed_goal)
        out = sync_scope_into_classification(out, parsed_goal, pipeline_tokens)
        out["_plan_scope"] = parsed_goal
        if parsed_goal.get("operations"):
            out["operations"] = parsed_goal["operations"]
        parsed_goal_repair = parsed_goal
    elif create_active or parsed_goal_after_apply:
        out = apply_create_metadata_to_classification(out, parsed_goal_after_apply)
        out = clear_catalog_workbook_pins_for_create(out, parsed_goal_after_apply)
        from services.planning.plan_scope import (
            resolve_pipeline_tokens_from_goal,
            sync_scope_into_classification,
        )

        pipeline_tokens = resolve_pipeline_tokens_from_goal(out, parsed_goal_after_apply)
        out = sync_scope_into_classification(out, parsed_goal_after_apply, pipeline_tokens)
        out["_plan_scope"] = parsed_goal_after_apply
        parsed_goal_repair = parsed_goal_after_apply

    from services.planning.destination_catalog_validate import (
        clear_destination_when_not_needed,
        read_needs_destination,
        strip_unvalidated_destination_confirm_flags,
    )
    from services.planning.source_catalog_validate import read_needs_source
    from services.planning.plan_scope import read_plan_scope
    from services.planning.planning_checkpoint import plan_scope_is_resolved, resolved_goal_for_session

    _resume_goal = parsed_goal_repair or resolved_goal_for_session(out, plan=plan)
    if plan_scope_is_resolved(_resume_goal):
        out = clear_destination_when_not_needed(out, _resume_goal)
    from services.planning.slot_fills import (
        hydrate_classification_from_slot_fills,
        sync_authoritative_flags_from_slot_fills,
    )

    out = hydrate_classification_from_slot_fills(out, plan=plan)
    out = sync_authoritative_flags_from_slot_fills(out, plan=plan)
    out = strip_unvalidated_destination_confirm_flags(out)

    ops_detail = format_operations_detail(_resume_goal)
    if ops_detail:
        emit_planning_activity(
            "detect_operations",
            "Detected operations",
            "done",
            segment="scope",
            detail=ops_detail,
        )
    emit_planning_activity(
        "bind_connectors",
        "Matching connectors to your account",
        "running",
        segment="scope",
    )

    from services.planning.clarification_pipeline import PlannerSession
    from services.planning.planning_kernel import run_planning_kernel
    from services.planning.planner_session_loop import finalize_planner_session

    session = PlannerSession(
        classification=out,
        plan=plan,
        parsed_goal=parsed_goal_repair or resolved_goal_for_session(out, plan=plan),
        prompt=planning_prompt,
        conversation_history=conversation_history or pending.get("conversation_history"),
    )
    session = await run_planning_kernel(
        session,
        plan_route=(out.get("plan_route") or "fresh_path"),
        user_id=str(user_id),
        catalog=cat,
        job=resume_job,
        conversation_id=pending.get("conversation_id"),
        plan_auto_run=plan_auto_run,
        run_source_resolver=read_needs_source(out, _resume_goal),
        run_destination_resolver=read_needs_destination(out, _resume_goal),
    )

    from services.intent.apply import apply_intent_pipeline_to_classification

    session.classification = await apply_intent_pipeline_to_classification(
        session.classification,
        prompt=planning_prompt,
        user_id=str(user_id),
        catalog=cat,
        conversation_id=pending.get("conversation_id"),
        phase="post_planning",
    )
    _resume_goal = parsed_goal_repair or read_plan_scope(session.classification)
    session.parsed_goal = _resume_goal

    _emit_post_kernel_scope_activity(session.classification)
    emit_planning_activity(
        "assemble_plan",
        "Assembling execution plan",
        "running",
        segment="plan_build",
    )

    session = await finalize_planner_session(
        session,
        user_id=str(user_id),
        catalog=cat,
        plan_auto_run=plan_auto_run,
        conversation_id=pending.get("conversation_id"),
    )
    out = session.classification
    plan = session.plan

    _emit_finalize_plan_activity(out, plan)

    log_planner_debug(
        "resume_completed",
        user_id=str(user_id),
        conversation_id=pending.get("conversation_id"),
        classification=out,
        plan=plan,
        extra={
            "step_count": len(plan.steps or []),
            "ui_expectation": (
                "clarification"
                if out.get("needs_clarification")
                else ("plan_approval" if (plan.steps and plan.status == "awaiting_approval") else "none")
            ),
        },
    )
    if resolved_selections and not out.get("needs_clarification"):
        try:
            from services.labeling.capture_hooks import schedule_clarification_label_evidence

            schedule_clarification_label_evidence(
                user_id=str(user_id),
                conversation_id=pending.get("conversation_id"),
                prompt=planning_prompt,
                classification=out,
                selections=resolved_selections,
            )
        except Exception:
            pass
    return out


def apply_planner_post_classification(
    classification: Dict[str, Any],
    *,
    prompt: str,
    catalog: ResourceCatalog,
    conversation_history: Optional[List[Dict]] = None,
    extraction_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resource hints + session continuation metadata (no keyword endpoint matching)."""
    from services.planning.continuation_policy import apply_extraction_session_continuation

    out = dict(classification or {})
    if extraction_meta:
        out = apply_extraction_session_continuation(
            out,
            extraction_meta=extraction_meta,
            prompt=prompt,
            catalog=catalog,
            conversation_history=conversation_history,
        )
    from services.planning.continuation_policy import apply_migration_failure_continuation

    out = apply_migration_failure_continuation(
        out,
        prompt=prompt,
        conversation_history=conversation_history,
    )
    return out
