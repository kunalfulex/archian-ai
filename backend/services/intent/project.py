"""Project IntentState → legacy classification dict for existing handlers.

Legacy consumers still read ``migration_intent`` on the classification dict (P5.4).
Canonical field/entity state lives on ``IntentState``; ``migration_intent`` is written
only via projection for API/frontend compatibility until Phase 5 retires it.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.state import IntentState
from services.intent.types import DestinationNeed, RouteTarget, TurnType, IntentStepId


def _intent_from_turn_type(turn_type: Optional[str]) -> str:
    mapping = {
        TurnType.EXTRACT.value: "extract",
        TurnType.MAP.value: "map_data",
        TurnType.MIGRATE.value: "migrate_data",
        TurnType.DATA_OPS.value: "multi",
        TurnType.INFO.value: "info",
        TurnType.GENERAL.value: "general",
        TurnType.METADATA_DEPLOY.value: "metadata_deploy",
        TurnType.DEPLOY.value: "metadata_deploy",
        TurnType.JOIN.value: "join",
        TurnType.CREATE.value: "create",
        TurnType.UPSERT.value: "upsert",
        TurnType.UPDATE.value: "update",
        TurnType.SEARCH.value: "search",
    }
    return mapping.get(turn_type or "", turn_type or "extract")


def project_to_classification(state: IntentState) -> Dict[str, Any]:
    """Build a legacy classification dict from IntentState (partial until all steps run)."""
    out: Dict[str, Any] = {
        "intent": _intent_from_turn_type(state.turn_type),
        "confidence": state.classifier_snapshot.get("confidence", 0.85),
        "reasoning": state.goal_summary or state.classifier_snapshot.get("reasoning"),
        "conversational_turn": state.conversational,
        "off_topic": state.off_topic,
    }

    if state.mode:
        out["mode"] = state.mode
    elif state.turn_type == TurnType.EXTRACT.value:
        out["mode"] = "extract"
    elif state.turn_type in (TurnType.MAP.value, TurnType.MIGRATE.value):
        out["mode"] = state.turn_type
    elif state.turn_type == TurnType.CREATE.value:
        out["mode"] = "create"
    elif state.turn_type == TurnType.UPSERT.value:
        out["mode"] = "upsert"
    elif state.turn_type == TurnType.UPDATE.value:
        out["mode"] = "update"
    elif state.turn_type == TurnType.SEARCH.value:
        out["mode"] = "search"
    elif state.turn_type in (TurnType.DEPLOY.value, TurnType.METADATA_DEPLOY.value):
        out["mode"] = "extract"
    elif state.turn_type == TurnType.JOIN.value:
        out["mode"] = "join"

    if state.operations:
        out["operations"] = list(state.operations)

    if state.active_job_id:
        out["active_job_id"] = state.active_job_id
    if state.session_action:
        out["session_action"] = state.session_action
    if state.continuation_phase:
        out["continuation_phase"] = state.continuation_phase
    if state.session_confidence:
        out["session_confidence"] = state.session_confidence
    if state.last_completed_step:
        out["last_completed_step"] = state.last_completed_step
    out["fresh_extract"] = state.fresh_extract
    out["continuation_turn"] = state.continuation_turn

    if state.source.connector_type and state.source.connector_type != "unknown":
        out["source"] = state.source.connector_type
    if state.source.display_name:
        table = (state.source_entity.entity_key or out.get("source_table_name") or "").strip()
        name = state.source.display_name.strip()
        if not (state.active_job_id and table and name.lower() == table.lower()):
            out["source_name"] = name
    if state.source.connector_id:
        out["source_connector_id"] = state.source.connector_id
    if state.source.sheet_name:
        out["source_sheet_name"] = state.source.sheet_name
    if state.source.spreadsheet_id:
        out["source_spreadsheet_id"] = state.source.spreadsheet_id

    needs_dest = state.needs_destination
    s04_ran = IntentStepId.S04_DESTINATION_NEED.value in (state.steps_completed or [])
    if needs_dest == DestinationNeed.NONE.value and s04_ran:
        out["destination"] = "unknown"
        out.pop("destination_name", None)
    elif state.destination.connector_type and state.destination.connector_type != "unknown":
        out["destination"] = state.destination.connector_type
        if state.destination.display_name:
            out["destination_name"] = state.destination.display_name
        if state.destination.connector_id:
            out["destination_connector_id"] = state.destination.connector_id

    if state.destination_same_as_source:
        out["destination_same_as_source"] = True

    if state.source_entity.entity_key:
        ct = (state.source.connector_type or "").lower()
        if ct == "sql":
            out["source_table_name"] = state.source_entity.entity_key
        elif ct == "salesforce":
            out["source_object_api_name"] = state.source_entity.entity_key
        elif ct == "google_sheets":
            out["source_sheet_name"] = state.source_entity.entity_key

    if state.fields.confirmed:
        out["extract_fields_confirmed"] = True

    if state.fields.scope and state.fields.scope != "unspecified":
        existing = out.get("migration_intent")
        base = dict(existing) if isinstance(existing, dict) else {}
        out["migration_intent"] = {
            **base,
            "extract_field_scope": state.fields.scope,
            "field_scope_confidence": state.fields.scope_confidence,
            "field_hints": state.fields.hints,
            "field_hints_strict": state.fields.hints_strict,
            "extract_fields_confirmed": state.fields.confirmed,
            "driving_entity_key": state.source_entity.entity_key or base.get("driving_entity_key"),
            "driving_entity_label": state.source_entity.entity_label or base.get("driving_entity_label"),
        }
    elif state.fields.hints_strict and state.fields.hints:
        existing = out.get("migration_intent")
        base = dict(existing) if isinstance(existing, dict) else {}
        out["migration_intent"] = {
            **base,
            "extract_field_scope": state.fields.scope or "explicit",
            "field_scope_confidence": state.fields.scope_confidence,
            "field_hints": state.fields.hints,
            "field_hints_strict": state.fields.hints_strict,
            "extract_fields_confirmed": state.fields.confirmed,
            "driving_entity_key": state.source_entity.entity_key,
            "driving_entity_label": state.source_entity.entity_label,
        }

    if state.plan_id:
        out["plan_id"] = state.plan_id
    if state.plan_status:
        out["plan_status"] = state.plan_status

    if state.active_gap or state.gaps:
        out["needs_clarification"] = True
    elif state.readiness in ("ready_to_plan", "ready_to_execute"):
        out["needs_clarification"] = False

    out["intent_state"] = state.model_dump(mode="json")
    return out


def merge_intent_into_classification(
    classification: Dict[str, Any],
    state: IntentState,
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Merge pipeline corrections into an existing classifier dict.

    When overwrite=False, intent pipeline only fills gaps and applies S04
    destination rules (extract-only → destination unknown).
    """
    out = dict(classification or {})
    projected = project_to_classification(state)

    needs_dest = state.needs_destination
    s04_ran = IntentStepId.S04_DESTINATION_NEED.value in (state.steps_completed or [])
    if needs_dest == DestinationNeed.NONE.value and s04_ran:
        out["destination"] = "unknown"
        out.pop("destination_name", None)
        out.pop("destination_connector_id", None)
        out.pop("destination_object_api_name", None)

    merge_keys = (
        "source",
        "source_name",
        "source_connector_id",
        "source_spreadsheet_id",
        "destination",
        "destination_name",
        "destination_connector_id",
        "intent",
        "mode",
        "operations",
        "source_table_name",
        "source_object_api_name",
        "source_sheet_name",
        "extract_fields_confirmed",
        "conversational_turn",
        "off_topic",
        "needs_clarification",
        "active_job_id",
        "session_action",
        "continuation_phase",
        "last_completed_step",
        "_continuation_steps",
        "plan_route",
        "fresh_extract",
        "continuation_turn",
    )

    for key in merge_keys:
        val = projected.get(key)
        if val is None:
            continue
        if overwrite or not out.get(key) or out.get(key) in ("unknown", "", None):
            out[key] = val

    # Legacy API projection (P5.4) — prefer intent_state / read_migration_intent_dict in new code.
    if projected.get("migration_intent"):
        existing = out.get("migration_intent")
        if overwrite or not existing:
            out["migration_intent"] = projected["migration_intent"]
        elif isinstance(existing, dict):
            out["migration_intent"] = {**existing, **projected["migration_intent"]}

    if state.source.resolved:
        out["_endpoints_resolved"] = True

    if state.active_gap and overwrite:
        out["needs_clarification"] = True

    out["intent_state"] = state.model_dump(mode="json")
    if state.route == RouteTarget.GENERAL_CHAT.value:
        from services.planning.planner_path import classification_has_plan_scope

        if out.get("needs_clarification") or classification_has_plan_scope(out):
            state.route = RouteTarget.PLANNER.value
        else:
            out["intent"] = "general"
            out["mode"] = "general"
            _strip_conversation_plan_fields(out)
    elif state.route == RouteTarget.INFO_QUERY.value:
        out["intent"] = "info"
        out["mode"] = "general"
        out["plan_route"] = "info_path"
        _strip_conversation_plan_fields(out)
    return out


def _strip_conversation_plan_fields(out: Dict[str, Any]) -> None:
    for key in (
        "operations",
        "execution_plan",
        "plan_status",
        "plan_id",
        "plan_steps_summary",
        "_continuation_steps",
        "active_job_id",
        "needs_clarification",
        "clarifications",
    ):
        out.pop(key, None)
