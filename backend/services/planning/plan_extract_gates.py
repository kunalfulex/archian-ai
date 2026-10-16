"""Gating helpers: do not advance to fields/destination/plan until extract source is pinned."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.schemas import PlanContract, PlanEntityRef


def _needs_source_entity(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]],
) -> bool:
    from services.file_extraction.pending_upload import is_file_source
    from services.planning.entity_resolver import _needs_entity_for_role

    if is_file_source(classification):
        return False
    return _needs_entity_for_role(classification, "source", parsed_goal)


def ready_to_build_file_extract_plan(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when a file upload can materialize an extract_file plan step."""
    from services.file_extraction.pending_upload import is_file_source
    from services.planning.connector_guards import classification_source_endpoint_resolved

    if not is_file_source(classification):
        return False
    if not classification_source_endpoint_resolved(classification):
        return False
    goal = parsed_goal or {}
    ops = [
        str(o).lower()
        for o in (goal.get("operations") or classification.get("operations") or [])
    ]
    return "extract" in ops


def _active_job_map_continuation(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """Map/migrate step on data already extracted in this conversation."""
    if not classification.get("active_job_id"):
        return False
    ops = [
        str(o).lower()
        for o in (parsed_goal or {}).get("operations") or classification.get("operations") or []
    ]
    if "extract" in ops:
        return False
    from services.planning.intent_routing import is_map_migrate_classification

    if is_map_migrate_classification(classification):
        return True
    return any(o in ops for o in ("map", "migrate"))


def source_extract_entity_resolved(
    plan_contract: Optional[PlanContract],
    *,
    min_confidence: float = 0.72,
) -> bool:
    if not plan_contract:
        return False
    side = plan_contract.source
    if not side.is_resolved():
        return False
    return float(side.confidence or 0.0) >= min_confidence


def extract_fields_locked(
    classification: Dict[str, Any],
    migration_intent: Any = None,
) -> bool:
    """True when the user already confirmed extract columns/fields for this plan."""
    return bool(
        classification.get("extract_fields_confirmed")
        or (migration_intent and getattr(migration_intent, "extract_fields_confirmed", False))
    )


def extract_fields_ready(
    migration_intent: Any,
    prompt: str,
    *,
    classification: Optional[Dict[str, Any]] = None,
) -> bool:
    if extract_fields_locked(classification or {}, migration_intent):
        return True
    if migration_intent is None:
        return True
    if getattr(migration_intent, "needs_extract_clarification", False):
        return False
    from services.planning.extract_field_clarification import needs_extract_field_clarification

    return not needs_extract_field_clarification(migration_intent, prompt or "")


def needs_source_entity_clarification(
    classification: Dict[str, Any],
    plan_contract: Optional[PlanContract],
    parsed_goal: Optional[Dict[str, Any]],
) -> bool:
    """True when extract/migrate needs a source table/object but none is pinned yet."""
    if _active_job_map_continuation(classification, parsed_goal):
        return False
    from services.planning.connector_guards import classification_source_endpoint_resolved

    if not classification_source_endpoint_resolved(classification):
        return False
    if classification.get("planner_source_entity_confirmed"):
        return False
    if (classification.get("source_table_name") or classification.get("source_object_api_name") or "").strip():
        return False
    if not _needs_source_entity(classification, parsed_goal):
        return False
    return not source_extract_entity_resolved(plan_contract)


def ready_for_extract_field_step(
    classification: Dict[str, Any],
    plan_contract: Optional[PlanContract],
    parsed_goal: Optional[Dict[str, Any]],
) -> bool:
    """True when we can ask which columns to extract (source entity must be pinned)."""
    from services.planning.connector_guards import classification_source_endpoint_resolved

    if _active_job_map_continuation(classification, parsed_goal):
        return classification_source_endpoint_resolved(classification)
    if not classification_source_endpoint_resolved(classification):
        return False
    if not _needs_source_entity(classification, parsed_goal):
        return False
    return source_extract_entity_resolved(plan_contract)


def ready_for_destination_entity_step(
    classification: Dict[str, Any],
    plan_contract: Optional[PlanContract],
    migration_intent: Any,
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]],
) -> bool:
    """True when destination object/table confirmation may run."""
    from services.planning.connector_guards import classification_destination_endpoint_resolved

    if not classification_destination_endpoint_resolved(classification):
        return False
    if not ready_for_extract_field_step(classification, plan_contract, parsed_goal):
        return False
    return extract_fields_ready(migration_intent, prompt, classification=classification)


def ready_to_build_migration_plan(
    classification: Dict[str, Any],
    plan_contract: Optional[PlanContract],
    migration_intent: Any,
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]],
) -> bool:
    """True when execution plan steps may be materialized for approval."""
    from services.planning.op_requirements import read_plan_requirements, ready_to_build_plan

    plan_reqs = read_plan_requirements(classification, parsed_goal=parsed_goal)
    if plan_reqs.pipeline_tokens or classification.get("_plan_requirements"):
        return ready_to_build_plan(
            classification,
            plan_contract=plan_contract,
            parsed_goal=parsed_goal,
            migration_intent=migration_intent,
            requirements=plan_reqs,
        )

    if _active_job_map_continuation(classification, parsed_goal):
        from services.planning.connector_guards import (
            classification_destination_endpoint_resolved,
        )
        from services.planning.op_requirements import DESTINATION_ENTITY, read_plan_requirements

        if not classification_destination_endpoint_resolved(classification):
            return False
        from services.planning.entity_pins import pinned_entity_key

        if pinned_entity_key(classification, role="destination"):
            return True
        reqs = read_plan_requirements(classification, parsed_goal=parsed_goal)
        if DESTINATION_ENTITY in {s.slot for s in reqs.all_slots()}:
            dest = plan_contract.destination if plan_contract else None
            return bool(dest and dest.is_resolved() and float(dest.confidence or 0) >= 0.72)
        return True

    if not ready_for_destination_entity_step(
        classification, plan_contract, migration_intent, prompt, parsed_goal
    ):
        return False
    from services.planning.connector_guards import (
        classification_needs_destination_endpoint,
    )
    from services.planning.op_requirements import DESTINATION_ENTITY, read_plan_requirements

    if not classification_needs_destination_endpoint(classification):
        return True

    reqs = read_plan_requirements(classification, parsed_goal=parsed_goal)
    if DESTINATION_ENTITY in {s.slot for s in reqs.all_slots()}:
        if not classification.get("planner_destination_entity_confirmed"):
            return False
        from services.planning.entity_pins import pinned_entity_key

        if pinned_entity_key(classification, role="destination"):
            return True
    dest = plan_contract.destination if plan_contract else None
    return bool(dest and dest.is_resolved() and float(dest.confidence or 0) >= 0.72)

# --- Planning artifact (merged P4.9) ---

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from services.planning.schemas import ExecutionPlan, normalize_connector_type


class PlanningReadiness(str, Enum):
    """What the system should do next for this conversation turn."""

    NEED_INTENT = "need_intent"  # off-topic or unparseable goal
    NEED_CLARIFICATION = "need_clarification"  # blocking planner UI gap
    NEED_MORE_INFO = "need_more_info"  # chat reply acceptable (empty catalog, etc.)
    READY_TO_PLAN = "ready_to_plan"  # steps built; awaiting user approval
    READY_TO_EXECUTE = "ready_to_execute"  # plan approved; run next tool
    EXECUTING = "executing"  # plan running
    COMPLETED = "completed"  # pipeline finished


class PlanningGapKind(str, Enum):
    SOURCE_CONNECTOR = "source_connector"
    DEST_CONNECTOR = "dest_connector"
    SOURCE_ENTITY = "source_entity"
    EXTRACT_FIELDS = "extract_fields"
    DEST_ENTITY = "dest_entity"
    PLAN_APPROVAL = "plan_approval"
    EXTRACT_AMBIGUITY = "extract_ambiguity"


class ConnectorSlot(BaseModel):
    connector_type: str = "unknown"
    display_name: Optional[str] = None
    connector_id: Optional[str] = None
    resolved: bool = False


class ExtractSpec(BaseModel):
    entity_key: Optional[str] = None
    entity_label: Optional[str] = None
    field_hints: List[str] = Field(default_factory=list)
    fields_confirmed: bool = False
    scope: Optional[str] = None  # explicit | vague | all_descriptive | unspecified


class DestinationSpec(BaseModel):
    entity_key: Optional[str] = None
    entity_label: Optional[str] = None
    confirmed: bool = False


class IntentSummary(BaseModel):
    objective: Optional[str] = None  # migrate | extract | map | transform | ...
    operations: List[str] = Field(default_factory=list)
    original_prompt: Optional[str] = None


class PlanningGap(BaseModel):
    kind: PlanningGapKind
    clarification_type: Optional[str] = None
    message: Optional[str] = None


class PlanningArtifact(BaseModel):
    readiness: PlanningReadiness = PlanningReadiness.NEED_MORE_INFO
    gaps: List[PlanningGap] = Field(default_factory=list)
    intent: IntentSummary = Field(default_factory=IntentSummary)
    source: ConnectorSlot = Field(default_factory=ConnectorSlot)
    destination: ConnectorSlot = Field(default_factory=ConnectorSlot)
    extract: ExtractSpec = Field(default_factory=ExtractSpec)
    dest_target: DestinationSpec = Field(default_factory=DestinationSpec)
    plan_id: Optional[str] = None
    plan_status: Optional[str] = None
    step_count: int = 0
    next_action: Optional[str] = None  # human-readable hint for logs/UI


def _slot_from_classification(
    classification: Dict[str, Any],
    role: str,
) -> ConnectorSlot:
    prefix = "source" if role == "source" else "destination"
    ct = normalize_connector_type(classification.get(prefix) or "")
    name = (classification.get(f"{prefix}_name") or "").strip() or None
    cid = (
        classification.get(f"{prefix}_connector_id")
        or (classification.get("destination_spreadsheet_id") if role == "destination" else None)
    )
    cid = (str(cid).strip() if cid else None) or None
    from services.planning.connector_guards import (
        classification_destination_endpoint_resolved,
        classification_source_endpoint_resolved,
        is_known_connector,
    )

    resolved = (
        classification_source_endpoint_resolved(classification)
        if role == "source"
        else classification_destination_endpoint_resolved(classification)
    )
    return ConnectorSlot(
        connector_type=ct if is_known_connector(ct) else "unknown",
        display_name=name,
        connector_id=cid,
        resolved=bool(resolved),
    )


def _extract_from_intent(migration_intent: Any) -> ExtractSpec:
    if migration_intent is None:
        return ExtractSpec()
    if hasattr(migration_intent, "model_dump"):
        raw = migration_intent.model_dump(mode="json")
    elif isinstance(migration_intent, dict):
        raw = migration_intent
    else:
        return ExtractSpec()
    return ExtractSpec(
        entity_key=(raw.get("driving_entity_key") or "").strip() or None,
        entity_label=(raw.get("driving_entity_label") or "").strip() or None,
        field_hints=[str(h) for h in (raw.get("field_hints") or []) if str(h).strip()],
        fields_confirmed=bool(raw.get("extract_fields_confirmed")),
        scope=raw.get("extract_field_scope"),
    )


def _dest_from_intent(migration_intent: Any, classification: Dict[str, Any]) -> DestinationSpec:
    from services.planning.entity_pins import pinned_entity_key

    key = pinned_entity_key(classification, role="destination")
    label = key
    if migration_intent is not None:
        raw = (
            migration_intent.model_dump(mode="json")
            if hasattr(migration_intent, "model_dump")
            else migration_intent
        )
        if isinstance(raw, dict):
            key = (raw.get("destination_entity_key") or key or "").strip() or None
            label = (raw.get("destination_entity_label") or key or "").strip() or None
    confirmed = bool(classification.get("planner_destination_entity_confirmed"))
    return DestinationSpec(entity_key=key or None, entity_label=label or None, confirmed=confirmed)


def _gaps_from_plan(plan: Optional[ExecutionPlan]) -> List[PlanningGap]:
    if not plan:
        return []
    mapping = {
        "pick_source_connector": PlanningGapKind.SOURCE_CONNECTOR,
        "pick_sql_connector": PlanningGapKind.SOURCE_CONNECTOR,
        "pick_salesforce_org": PlanningGapKind.SOURCE_CONNECTOR,
        "pick_google_account": PlanningGapKind.SOURCE_CONNECTOR,
        "pick_workbook": PlanningGapKind.SOURCE_CONNECTOR,
        "pick_workbook_ambiguous": PlanningGapKind.SOURCE_CONNECTOR,
        "pick_tab": PlanningGapKind.SOURCE_CONNECTOR,
        "pick_connector_by_label": PlanningGapKind.SOURCE_CONNECTOR,
        "create_workbook_name": PlanningGapKind.SOURCE_CONNECTOR,
        "pick_source_entity": PlanningGapKind.SOURCE_ENTITY,
        "pick_extract_fields": PlanningGapKind.EXTRACT_FIELDS,
        "extract_entity_role": PlanningGapKind.EXTRACT_AMBIGUITY,
        "extract_related_vs_secondary": PlanningGapKind.EXTRACT_AMBIGUITY,
        "pick_destination_entity": PlanningGapKind.DEST_ENTITY,
    }
    gaps: List[PlanningGap] = []
    for clar in plan.blocking_clarifications():
        kind = mapping.get(clar.clarification_type, PlanningGapKind.EXTRACT_AMBIGUITY)
        gaps.append(
            PlanningGap(
                kind=kind,
                clarification_type=clar.clarification_type,
                message=clar.prompt,
            )
        )
    return gaps


def strip_conversation_plan_from_classification(classification: Dict[str, Any]) -> bool:
    """
    Safety net: info/general turns must not retain pipeline or execution-plan fields.

    Mutates ``classification`` in place. Used by assess_planning_state and emit routing.
    """
    if not classification:
        return False
    intent = (classification.get("intent") or "").lower()
    if not (
        classification.get("conversational_turn")
        or intent in ("general", "info")
    ):
        return False
    classification["mode"] = "general"
    if intent == "info":
        classification["plan_route"] = "info_path"
    elif intent == "general":
        classification["plan_route"] = "general_path"
    from services.intent.project import _strip_conversation_plan_fields

    _strip_conversation_plan_fields(classification)
    return True


def assess_planning_state(
    classification: Dict[str, Any],
    plan: Optional[ExecutionPlan] = None,
    *,
    migration_intent: Any = None,
    original_prompt: Optional[str] = None,
) -> PlanningArtifact:
    """
    Map current planner state → readiness + structured artifact.

    When intent is info/general, strips plan fields from ``classification`` in place
    before building the artifact (defense in depth vs stale execution_plan).
    """
    base = classification if classification is not None else {}
    strip_conversation_plan_from_classification(base)
    cls = dict(base)
    intent = (cls.get("intent") or cls.get("mode") or "").lower()
    ops = [str(o).lower() for o in (cls.get("operations") or [])]

    artifact = PlanningArtifact(
        intent=IntentSummary(
            objective=intent or None,
            operations=ops,
            original_prompt=original_prompt or cls.get("original_prompt"),
        ),
        source=_slot_from_classification(cls, "source"),
        destination=_slot_from_classification(cls, "destination"),
        extract=_extract_from_intent(migration_intent),
        dest_target=_dest_from_intent(migration_intent, cls),
        plan_id=cls.get("plan_id") or (plan.plan_id if plan else None),
        plan_status=cls.get("plan_status") or (plan.status if plan else None),
        step_count=len(plan.steps) if plan and plan.steps else 0,
    )

    if cls.get("conversational_turn") or intent in ("general", "info"):
        artifact.readiness = PlanningReadiness.NEED_INTENT
        artifact.next_action = "Answer or rephrase the data request"
        artifact.plan_id = None
        artifact.plan_status = None
        artifact.step_count = 0
        return artifact

    plan_status = (artifact.plan_status or "").lower()
    if plan_status == "approved":
        artifact.readiness = PlanningReadiness.READY_TO_EXECUTE
        artifact.next_action = "Run approved plan step (extract → map → migrate)"
        return artifact
    if plan_status == "running":
        artifact.readiness = PlanningReadiness.EXECUTING
        artifact.next_action = "Continue plan execution"
        return artifact
    if plan_status in ("completed",):
        artifact.readiness = PlanningReadiness.COMPLETED
        return artifact

    gaps = _gaps_from_plan(plan)
    artifact.gaps = gaps
    if gaps:
        artifact.readiness = PlanningReadiness.NEED_CLARIFICATION
        artifact.next_action = f"Resolve: {gaps[0].clarification_type or gaps[0].kind.value}"
        return artifact

    if cls.get("needs_clarification"):
        artifact.readiness = PlanningReadiness.NEED_CLARIFICATION
        artifact.next_action = "Resolve planner clarification"
        return artifact

    if artifact.step_count > 0 and plan_status in ("awaiting_approval", "draft"):
        artifact.readiness = PlanningReadiness.READY_TO_PLAN
        artifact.next_action = "Show execution plan for approval"
        return artifact

    if cls.get("_plan_requirements") or cls.get("_pipeline_tokens"):
        from services.planning.op_requirements import read_plan_requirements, ready_to_build_plan

        if ready_to_build_plan(
            cls,
            parsed_goal=cls.get("_plan_scope") if isinstance(cls.get("_plan_scope"), dict) else None,
            migration_intent=migration_intent,
            requirements=read_plan_requirements(cls),
        ):
            artifact.readiness = PlanningReadiness.READY_TO_PLAN
            artifact.next_action = "Build or show execution plan for approval"
            return artifact

    # Connectors unresolved but no formal clarification yet
    from services.planning.connector_guards import (
        classification_destination_endpoint_resolved,
        classification_needs_destination_endpoint,
        classification_needs_source_endpoint,
        classification_source_endpoint_resolved,
    )

    if classification_needs_source_endpoint(cls) and not classification_source_endpoint_resolved(cls):
        artifact.readiness = PlanningReadiness.NEED_CLARIFICATION
        artifact.gaps = [PlanningGap(kind=PlanningGapKind.SOURCE_CONNECTOR)]
        artifact.next_action = "Pick source connector"
        return artifact
    if classification_needs_destination_endpoint(cls) and not classification_destination_endpoint_resolved(cls):
        artifact.readiness = PlanningReadiness.NEED_CLARIFICATION
        artifact.gaps = [PlanningGap(kind=PlanningGapKind.DEST_CONNECTOR)]
        artifact.next_action = "Pick destination connector"
        return artifact

    if artifact.extract.entity_key and not artifact.extract.fields_confirmed:
        mi = migration_intent
        needs_fields = False
        if mi is not None:
            raw = mi.model_dump(mode="json") if hasattr(mi, "model_dump") else mi
            if isinstance(raw, dict):
                needs_fields = bool(raw.get("needs_extract_clarification"))
        if needs_fields:
            artifact.readiness = PlanningReadiness.NEED_CLARIFICATION
            artifact.gaps = [PlanningGap(kind=PlanningGapKind.EXTRACT_FIELDS)]
            artifact.next_action = "Confirm extract fields"
            return artifact

    if (
        classification_needs_destination_endpoint(cls)
        and not artifact.dest_target.entity_key
        and not artifact.dest_target.confirmed
    ):
        artifact.readiness = PlanningReadiness.NEED_CLARIFICATION
        artifact.gaps = [PlanningGap(kind=PlanningGapKind.DEST_ENTITY)]
        artifact.next_action = "Infer or confirm destination entity from extract spec"
        return artifact

    artifact.readiness = PlanningReadiness.NEED_MORE_INFO
    artifact.next_action = "Build or refine execution plan"
    return artifact

