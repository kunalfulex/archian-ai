"""
Unified clarification pipeline for planner v2.

Resolves exactly one next blocking stage per turn (connector → entity → columns → …).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import Clarification, ExecutionPlan, normalize_connector_type


class MigrateStage(str, Enum):
    SOURCE_CONNECTOR_TYPE = "source_connector_type"
    SOURCE_CONNECTOR_NAME = "source_connector_name"
    SOURCE_ENTITY = "source_entity"
    EXTRACT_FIELDS = "extract_fields"
    DEST_CONNECTOR_TYPE = "dest_connector_type"
    DEST_CONNECTOR_NAME = "dest_connector_name"
    DEST_ENTITY = "dest_entity"
    PLAN_READY = "plan_ready"
    PLAN_ASSEMBLY = "plan_assembly"


@dataclass
class PlannerSession:
    classification: Dict[str, Any]
    plan: ExecutionPlan
    migration_intent: Any = None
    plan_contract: Any = None
    parsed_goal: Optional[Dict[str, Any]] = None
    prompt: str = ""
    conversation_history: Optional[List[Dict[str, Any]]] = None


def apply_intent_source_entity_to_classification(
    classification: Dict[str, Any],
    migration_intent: Any,
    *,
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Pin source table/object from prompt-derived migration intent when endpoint is known."""
    out = dict(classification or {})
    if out.get("planner_source_entity_confirmed"):
        return out

    key: Optional[str] = None
    if migration_intent and hasattr(migration_intent, "resolved_driving_key"):
        key = migration_intent.resolved_driving_key()
    elif isinstance(migration_intent, dict):
        key = (migration_intent.get("driving_entity_key") or "").strip() or None
    if not key:
        return out

    src = normalize_connector_type(out.get("source") or "")
    if src == "sql" and not (out.get("source_table_name") or "").strip():
        out["source_table_name"] = key
    elif src == "salesforce" and not (out.get("source_object_api_name") or "").strip():
        out["source_object_api_name"] = key
    elif src == "google_sheets" and not (out.get("source_sheet_name") or "").strip():
        out["source_sheet_name"] = key
    return out


def _answered_clarifications(plan: ExecutionPlan) -> List[Clarification]:
    return [c for c in plan.clarifications if c.selected_option_id]


async def _compose_blocking_clarification(
    session: PlannerSession,
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
) -> ExecutionPlan:
    from services.planning.clarification_compose import enrich_blocking_clarification_on_plan
    from services.planning.plan_scope import read_plan_scope

    return await enrich_blocking_clarification_on_plan(
        session.plan,
        classification=session.classification,
        user_prompt=session.prompt,
        parsed_goal=session.parsed_goal or read_plan_scope(session.classification),
        user_id=str(user_id),
        conversation_id=conversation_id,
        conversation_history=session.conversation_history,
    )


def enforce_single_blocking_clarification(plan: ExecutionPlan) -> ExecutionPlan:
    """Keep at most one open required clarification (answered picks preserved)."""
    plan = plan.model_copy(deep=True)
    answered = _answered_clarifications(plan)
    blocking = plan.blocking_clarifications()
    if len(blocking) <= 1:
        return plan
    plan.clarifications = answered + [blocking[0]]
    if blocking:
        plan.status = "awaiting_clarification"
    return plan


def _ordered_stages(classification: Dict[str, Any]) -> List[MigrateStage]:
    from services.planning.connector_guards import (
        classification_needs_destination_endpoint,
        classification_needs_source_endpoint,
    )
    from services.planning.continuation_policy import should_skip_source_binding
    from services.planning.op_requirements import _destination_entity_slot_applies
    from services.planning.plan_scope import read_plan_scope

    stages: List[MigrateStage] = []
    parsed_goal = read_plan_scope(classification)
    skip_source = should_skip_source_binding(
        classification, parsed_goal, prompt=classification.get("original_prompt")
    )
    if classification_needs_source_endpoint(classification) and not skip_source:
        stages.extend(
            [
                MigrateStage.SOURCE_CONNECTOR_TYPE,
                MigrateStage.SOURCE_CONNECTOR_NAME,
                MigrateStage.SOURCE_ENTITY,
                MigrateStage.EXTRACT_FIELDS,
            ]
        )
    if classification_needs_destination_endpoint(classification):
        stages.extend(
            [
                MigrateStage.DEST_CONNECTOR_TYPE,
                MigrateStage.DEST_CONNECTOR_NAME,
            ]
        )
        if _destination_entity_slot_applies(classification, parsed_goal):
            stages.append(MigrateStage.DEST_ENTITY)
    stages.append(MigrateStage.PLAN_READY)
    return stages


def _source_entity_complete(session: PlannerSession) -> bool:
    cls = session.classification
    if cls.get("planner_source_entity_confirmed"):
        return True
    if (cls.get("source_table_name") or cls.get("source_object_api_name") or "").strip():
        return True
    if (cls.get("source_sheet_name") or "").strip() and normalize_connector_type(
        cls.get("source") or ""
    ) == "google_sheets":
        return True
    intent = session.migration_intent
    if intent and getattr(intent, "resolved_driving_key", lambda: None)():
        return True
    from services.planning.plan_extract_gates import source_extract_entity_resolved

    if session.plan_contract and source_extract_entity_resolved(session.plan_contract):
        return True
    return False


def _extract_fields_complete(session: PlannerSession) -> bool:
    cls = session.classification
    if cls.get("extract_fields_confirmed"):
        return True
    intent = session.migration_intent
    if not intent:
        return True
    if getattr(intent, "extract_fields_confirmed", False):
        return True
    if getattr(intent, "needs_extract_clarification", False):
        return False
    from services.planning.extract_field_clarification import needs_extract_field_clarification

    if needs_extract_field_clarification(intent, session.prompt or ""):
        return False
    return True


async def _stage_is_complete(
    session: PlannerSession,
    stage: MigrateStage,
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> bool:
    from services.planning.connector_guards import (
        classification_destination_endpoint_resolved,
        classification_needs_destination_endpoint,
        classification_needs_source_endpoint,
        classification_source_endpoint_resolved,
        is_known_connector,
    )
    from services.planning.op_requirements import _destination_entity_slot_applies

    cls = session.classification

    if stage == MigrateStage.SOURCE_CONNECTOR_TYPE:
        if not classification_needs_source_endpoint(cls):
            return True
        return is_known_connector(cls.get("source"))

    if stage == MigrateStage.SOURCE_CONNECTOR_NAME:
        if not classification_needs_source_endpoint(cls):
            return True
        return classification_source_endpoint_resolved(cls, catalog=catalog)

    if stage == MigrateStage.SOURCE_ENTITY:
        if not classification_needs_source_endpoint(cls):
            return True
        if not classification_source_endpoint_resolved(cls, catalog=catalog):
            return False
        return _source_entity_complete(session)

    if stage == MigrateStage.EXTRACT_FIELDS:
        if not classification_needs_source_endpoint(cls):
            return True
        if not _source_entity_complete(session):
            return False
        return _extract_fields_complete(session)

    if stage in (
        MigrateStage.DEST_CONNECTOR_TYPE,
        MigrateStage.DEST_CONNECTOR_NAME,
        MigrateStage.DEST_ENTITY,
        MigrateStage.PLAN_READY,
    ):
        if not classification_needs_destination_endpoint(cls):
            return stage == MigrateStage.PLAN_READY
        if not _destination_entity_slot_applies(cls):
            return stage == MigrateStage.PLAN_READY
        if stage == MigrateStage.DEST_CONNECTOR_TYPE:
            return is_known_connector(cls.get("destination"))
        if stage == MigrateStage.DEST_CONNECTOR_NAME:
            return classification_destination_endpoint_resolved(
                cls, catalog=catalog, parsed_goal=session.parsed_goal
            )
        if stage == MigrateStage.DEST_ENTITY:
            from services.planning.create_metadata import (
                classification_requests_new_spreadsheet,
                spreadsheet_title_slot_satisfied,
            )
            from services.planning.entity_pins import pinned_entity_key
            from services.planning.plan_scope import read_plan_scope

            goal = session.parsed_goal or read_plan_scope(cls) or {}
            if classification_requests_new_spreadsheet(cls, goal):
                return spreadsheet_title_slot_satisfied(cls, goal)
            if pinned_entity_key(cls, role="destination"):
                return True
            if cls.get("planner_destination_entity_confirmed"):
                return True
            dest = session.plan_contract.destination if session.plan_contract else None
            return bool(dest and dest.is_resolved() and float(dest.confidence or 0) >= 0.72)
        return False

    return True


async def _prep_destination_connector(
    session: PlannerSession,
    stage: MigrateStage,
    *,
    user_id: str,
    catalog: ResourceCatalog,
) -> None:
    from services.planning.destination_catalog_validate import (
        destination_connector_slot_satisfied,
        read_needs_destination,
    )
    from services.planning.destination_llm_resolve import resolve_destination_connector_via_llm
    from services.planning.plan_scope import read_plan_scope

    goal = dict(session.parsed_goal or read_plan_scope(session.classification) or {})
    if not read_needs_destination(session.classification, goal):
        return

    if not session.classification.get("_destination_connector_llm_attempted"):
        session.classification["_destination_connector_llm_attempted"] = True
        try:
            session.classification = await resolve_destination_connector_via_llm(
                session.classification,
                prompt=session.prompt or "",
                parsed_goal=goal,
                catalog=catalog,
                user_id=str(user_id),
                conversation_id=None,
            )
        except Exception:
            pass
    session.parsed_goal = goal


async def _prep_source_connector(
    session: PlannerSession,
    stage: MigrateStage,
    *,
    user_id: str,
    catalog: ResourceCatalog,
) -> None:
    from services.planning.connector_guards import (
        classification_needs_source_endpoint,
        classification_source_endpoint_resolved,
        is_known_connector,
    )
    from services.planning.gap_detector import auto_pin_single_source_connector
    from services.planning.plan_scope import read_plan_scope
    from services.planning.source_catalog_validate import read_needs_source

    if stage != MigrateStage.SOURCE_CONNECTOR_NAME:
        return

    cls = session.classification
    goal = session.parsed_goal or read_plan_scope(cls) or {}
    if read_needs_source(cls, goal) and not cls.get("_source_connector_llm_attempted"):
        cls["_source_connector_llm_attempted"] = True
        session.classification = cls
        try:
            from services.planning.source_llm_resolve import resolve_source_connector_via_llm

            session.classification = await resolve_source_connector_via_llm(
                session.classification,
                prompt=session.prompt or "",
                parsed_goal=goal,
                catalog=catalog,
                user_id=str(user_id),
                conversation_id=None,
            )
        except Exception:
            pass
        cls = session.classification

    if (
        classification_needs_source_endpoint(cls)
        and is_known_connector(cls.get("source"))
        and not classification_source_endpoint_resolved(cls, catalog=catalog)
    ):
        pinned = auto_pin_single_source_connector(dict(cls), catalog)
        if classification_source_endpoint_resolved(pinned, catalog=catalog):
            session.classification.update(pinned)


async def _prep_extract_fields_intent(
    session: PlannerSession,
    *,
    user_id: str,
) -> None:
    from services.planning.extract_field_clarification import resolve_field_clarification_for_plan

    intent = session.migration_intent
    if not intent:
        return

    has_field_pick = any(
        (a.ambiguity_type or "") == "pick_extract_fields"
        for a in (getattr(intent, "extract_ambiguities", None) or [])
    )
    if not has_field_pick:
        intent = await resolve_field_clarification_for_plan(
            intent,
            user_id=str(user_id),
            classification=session.classification,
            prompt=session.prompt,
            plan_contract=session.plan_contract,
        )
        session.migration_intent = intent

    if intent:
        session.plan.metadata = dict(session.plan.metadata or {})
        session.plan.metadata["migration_intent"] = intent.model_dump(mode="json")


async def _run_pipeline_prep(
    session: PlannerSession,
    *,
    user_id: str,
    catalog: ResourceCatalog,
) -> str:
    """Run connector LLM + field-scope prep for the first incomplete stage (no clar writes)."""
    for stage in _ordered_stages(session.classification):
        if await _stage_is_complete(session, stage, catalog=catalog):
            continue
        if stage in (
            MigrateStage.DEST_CONNECTOR_TYPE,
            MigrateStage.DEST_CONNECTOR_NAME,
        ):
            await _prep_destination_connector(
                session, stage, user_id=str(user_id), catalog=catalog
            )
        elif stage in (
            MigrateStage.SOURCE_CONNECTOR_TYPE,
            MigrateStage.SOURCE_CONNECTOR_NAME,
        ):
            await _prep_source_connector(
                session, stage, user_id=str(user_id), catalog=catalog
            )
        elif stage == MigrateStage.EXTRACT_FIELDS:
            await _prep_extract_fields_intent(session, user_id=str(user_id))
        return stage.value
    return MigrateStage.PLAN_READY.value


async def ensure_migration_intent(
    session: PlannerSession,
    *,
    user_id: str,
    catalog: ResourceCatalog,
    conversation_id: Optional[str] = None,
) -> PlannerSession:
    """Load or refresh migration intent once source endpoint is pinned."""
    from services.planning.connector_guards import classification_source_endpoint_resolved
    from services.planning.goal_parser import enrich_scope_from_llm
    from services.planning.binding.ensure import ensure_plan_binding_if_needed
    from services.planning.plan_scope import read_plan_scope, sync_scope_into_classification
    from services.planning.plan_scope import resolve_pipeline_tokens_from_goal

    parsed_goal = session.parsed_goal or read_plan_scope(session.classification) or {}
    if not classification_source_endpoint_resolved(
        session.classification, catalog=catalog, parsed_goal=parsed_goal
    ):
        return session

    if session.parsed_goal is None:
        session.parsed_goal = await enrich_scope_from_llm(
            session.classification,
            session.prompt,
            user_id=str(user_id),
            conversation_id=conversation_id,
        )
        if session.parsed_goal:
            tokens = resolve_pipeline_tokens_from_goal(
                session.classification, session.parsed_goal
            )
            session.classification = sync_scope_into_classification(
                session.classification, session.parsed_goal, tokens
            )
            session.classification["_plan_scope"] = session.parsed_goal
    elif not read_plan_scope(session.classification):
        session.classification["_plan_scope"] = session.parsed_goal

    if session.parsed_goal:
        session.classification, session.plan, bound_mi, plan_contract = (
            await ensure_plan_binding_if_needed(
                session.classification,
                session.plan,
                parsed_goal=session.parsed_goal,
                user_id=str(user_id),
                prompt=session.prompt,
                catalog=catalog,
                conversation_id=conversation_id,
                conversation_history=session.conversation_history,
                migration_intent=session.migration_intent,
            )
        )
        if bound_mi is not None:
            session.migration_intent = bound_mi
        if plan_contract is not None:
            session.plan_contract = plan_contract

    if session.migration_intent is None:
        raw_intent = (session.plan.metadata or {}).get("migration_intent")
        if raw_intent:
            from services.planning.clarification_apply import _sync_migration_intent_to_classification
            from services.planning.extract_field_clarification import (
                _intent_with_extract_fields_locked,
            )
            from services.planning.migration_intent_schemas import PromptMigrationIntent

            session.migration_intent = PromptMigrationIntent.model_validate(raw_intent)
            session.migration_intent = _intent_with_extract_fields_locked(
                session.migration_intent,
                classification=session.classification,
            )
            session.classification = _sync_migration_intent_to_classification(
                session.classification,
                session.migration_intent.model_dump(mode="json"),
            )

    session.classification = apply_intent_source_entity_to_classification(
        session.classification,
        session.migration_intent,
        prompt=session.prompt,
    )
    if session.migration_intent:
        session.plan.metadata = dict(session.plan.metadata or {})
        session.plan.metadata["migration_intent"] = session.migration_intent.model_dump(
            mode="json"
        )
    return session


async def advance_planner_pipeline(
    session: PlannerSession,
    *,
    user_id: str,
    catalog: ResourceCatalog,
    conversation_id: Optional[str] = None,
    plan_auto_run: bool = False,
) -> PlannerSession:
    """
    Advance the planner by at most one clarification, or delegate to legacy tail
    when source stages are complete.
    """
    from services.planning.observability import log_planner_debug

    session = await ensure_migration_intent(
        session,
        user_id=str(user_id),
        catalog=catalog,
        conversation_id=conversation_id,
    )

    prep_stage = await _run_pipeline_prep(
        session, user_id=str(user_id), catalog=catalog
    )

    log_planner_debug(
        "pipeline_prep",
        user_id=str(user_id),
        conversation_id=conversation_id,
        classification=session.classification,
        plan=session.plan,
        extra={"pipeline_prep_stage": prep_stage},
    )

    session = await _run_plan_assembly_tail(
            session,
            user_id=str(user_id),
            catalog=catalog,
            plan_auto_run=plan_auto_run,
            conversation_id=conversation_id,
        )

    from services.planning.clarification_sync import sync_session_plan_clarifications

    session = await sync_session_plan_clarifications(
        session,
        user_id=str(user_id),
        catalog=catalog,
        conversation_id=conversation_id,
        migration_intent=session.migration_intent,
        advance_one=True,
        compose=True,
    )

    session.classification.pop("_pipeline_user_id", None)
    return session


async def _run_plan_assembly_tail(
    session: PlannerSession,
    *,
    user_id: str,
    catalog: ResourceCatalog,
    plan_auto_run: bool,
    conversation_id: Optional[str] = None,
) -> PlannerSession:
    """Destination entity binding + plan step assembly."""
    from services.planning.clarification_apply import _sync_migration_intent_to_classification
    from services.planning.connector_guards import classification_source_endpoint_resolved
    from services.planning.extract_field_clarification import has_entity_extract_ambiguities
    from services.planning.migration_intent import finalize_migration_intent_after_clarifications
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.intent.plan_assembly import assemble_migration_plan_tail
    from services.planning.plan_builder import finalize_plan_status

    parsed_goal = session.parsed_goal or {}
    from services.planning.plan_scope import read_plan_scope

    if not parsed_goal:
        parsed_goal = read_plan_scope(session.classification)

    if not classification_source_endpoint_resolved(
        session.classification, catalog=catalog, parsed_goal=parsed_goal
    ):
        return session

    from services.planning.destination_catalog_validate import (
        destination_connector_slot_satisfied,
        read_needs_destination,
    )

    session.classification["_planning_catalog"] = catalog
    if read_needs_destination(session.classification, parsed_goal):
        if not destination_connector_slot_satisfied(
            session.classification, catalog, parsed_goal=parsed_goal
        ):
            session.plan = finalize_plan_status(session.plan, plan_auto_run=plan_auto_run)
            return session

    if parsed_goal:
        from services.planning.binding.ensure import ensure_plan_binding_if_needed

        (
            session.classification,
            session.plan,
            bound_mi,
            plan_contract,
        ) = await ensure_plan_binding_if_needed(
            session.classification,
            session.plan,
            parsed_goal=parsed_goal,
            user_id=str(user_id),
            prompt=session.prompt,
            catalog=catalog,
            conversation_id=conversation_id,
            conversation_history=session.conversation_history,
            migration_intent=session.migration_intent,
        )
        if bound_mi is not None:
            session.migration_intent = bound_mi
        if plan_contract is not None:
            session.plan_contract = plan_contract

    migration_intent = session.migration_intent
    clarified_raw = (session.plan.metadata or {}).get("migration_intent")
    if session.classification.get("_extract_intent_clarified") and clarified_raw:
        migration_intent = await finalize_migration_intent_after_clarifications(
            PromptMigrationIntent.model_validate(clarified_raw),
            user_id=str(user_id),
            classification=session.classification,
            prompt=session.prompt,
            parsed_goal=session.parsed_goal,
        )
        session.classification.pop("_extract_intent_clarified", None)

    if migration_intent:
        session.classification = _sync_migration_intent_to_classification(
            session.classification, migration_intent.model_dump(mode="json")
        )

    from services.planning.plan_extract_gates import extract_fields_locked

    extract_fields_done = extract_fields_locked(session.classification, migration_intent)
    entity_clarification = bool(
        not extract_fields_done
        and migration_intent
        and migration_intent.needs_extract_clarification
        and has_entity_extract_ambiguities(migration_intent)
    )
    if entity_clarification or (
        not extract_fields_done and migration_intent and migration_intent.needs_separate_pipelines
    ):
        if migration_intent:
            session.plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
    else:
        out, plan, migration_intent, plan_contract = await assemble_migration_plan_tail(
            session.classification,
            session.plan,
            user_id=str(user_id),
            prompt=session.prompt,
            parsed_goal=session.parsed_goal or {},
            catalog=catalog,
            migration_intent=migration_intent,
        )
        session.classification = out
        session.plan = plan
        session.migration_intent = migration_intent
        session.plan_contract = plan_contract

    session.plan = finalize_plan_status(session.plan, plan_auto_run=plan_auto_run)
    session.migration_intent = migration_intent
    return session
