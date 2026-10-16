"""Mapping execution context built from approved plan pins."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.mapping.brief import compose_mapping_brief
from services.mapping_plan.target_resolution import MappingTargetResolution
from services.planning.schemas import PlanContract, PlanEntityRef, normalize_connector_type


def _normalize_entity_key(key: str, connector_type: str) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    ct = normalize_connector_type(connector_type)
    if ct in ("google_sheets", "gsheets") and "::" in k:
        return k.split("::", 1)[-1].strip() or k
    return k


def pinned_destination_entity_key(
    *,
    classification_snapshot: Optional[Dict[str, Any]],
    step_args: Optional[Dict[str, Any]] = None,
    destination: str = "",
) -> str:
    """Executable destination entity from plan contract, step args, or classification pins."""
    args = step_args or {}
    pinned = (args.get("destination_entity_key") or "").strip()
    if pinned:
        return _normalize_entity_key(pinned, destination)

    snap = classification_snapshot or {}
    contract_raw = snap.get("plan_contract") or {}
    if isinstance(contract_raw, dict):
        for side_key in ("mapping_target", "destination"):
            side = contract_raw.get(side_key)
            if isinstance(side, dict):
                key = (side.get("entity_key") or "").strip()
                if key and key.lower() not in ("unknown",):
                    return _normalize_entity_key(key, destination)

    dest = normalize_connector_type(destination or snap.get("destination") or "")
    if dest == "salesforce":
        v = (snap.get("destination_object_api_name") or "").strip()
    elif dest == "sql":
        v = (snap.get("destination_table_name") or snap.get("destination_table") or "").strip()
    elif dest in ("google_sheets", "gsheets"):
        obj = (snap.get("destination_object_api_name") or "").strip()
        if obj and "::" in obj:
            return _normalize_entity_key(obj, dest)
        v = (snap.get("destination_sheet_name") or "").strip()
    else:
        v = ""
    return _normalize_entity_key(v, dest) if v and v.lower() not in ("unknown",) else ""


def _ref_from_contract_side(raw: Any) -> PlanEntityRef:
    if isinstance(raw, PlanEntityRef):
        return raw
    if isinstance(raw, dict):
        try:
            return PlanEntityRef.model_validate(raw)
        except Exception:
            return PlanEntityRef()
    return PlanEntityRef()


def _plan_contract_from_snap(snap: Dict[str, Any]) -> PlanContract:
    raw = snap.get("plan_contract")
    if isinstance(raw, PlanContract):
        return raw
    if isinstance(raw, dict) and raw:
        try:
            return PlanContract.model_validate(raw)
        except Exception:
            pass
    return PlanContract()


def _side_from_classification(
    snap: Dict[str, Any],
    role: str,
    *,
    entity_key: str = "",
) -> PlanEntityRef:
    connector_type = normalize_connector_type(
        snap.get("source" if role == "source" else "destination") or "unknown"
    )
    display_name = (
        snap.get("source_name") if role == "source" else snap.get("destination_name")
    )
    connector_id = (
        snap.get("source_connector_id") if role == "source" else snap.get("destination_connector_id")
    )
    ref = PlanEntityRef(
        connector_type=connector_type,
        connector_id=str(connector_id) if connector_id else None,
        display_name=(display_name or "").strip() or None,
        entity_key=entity_key or None,
        resolution_source="plan_pinned" if entity_key else "pending",
        confidence=0.95 if entity_key else 0.0,
    )
    if role == "destination" and connector_type in ("google_sheets", "gsheets"):
        ref.spreadsheet_id = (snap.get("destination_spreadsheet_id") or "").strip() or None
        ref.sheet_name = entity_key or (snap.get("destination_sheet_name") or "").strip() or None
    return ref


@dataclass
class MappingExecutionContext:
    source: PlanEntityRef
    destination: PlanEntityRef
    mapping_target: PlanEntityRef
    migration_intent: Dict[str, Any] = field(default_factory=dict)
    mapping_brief: str = ""
    job_id: str = ""
    sample_record: Dict[str, Any] = field(default_factory=dict)
    column_keys: List[str] = field(default_factory=list)
    approved_plan: bool = False
    recovery_field_hints: Optional[str] = None
    destination_connector_type: str = "unknown"

    @property
    def has_pinned_entity(self) -> bool:
        key = (self.mapping_target.entity_key or "").strip()
        return bool(key) and key.lower() not in ("unknown",)

    def to_target_resolution(self) -> MappingTargetResolution:
        """Build a plan-pinned target resolution (no catalog LLM)."""
        dest = normalize_connector_type(self.destination_connector_type)
        key = (self.mapping_target.entity_key or "").strip()
        base = MappingTargetResolution(
            connector_type=dest,
            resolved_target=key,
            resolution_source="plan_pinned",
            confidence=0.95,
        )
        if dest == "salesforce":
            base.target_object_api_name = key
        elif dest == "sql":
            base.target_table = key
        elif dest in ("google_sheets", "gsheets"):
            base.target_sheet = key
        return base


def build_mapping_execution_context(
    *,
    classification_snapshot: Optional[Dict[str, Any]],
    step_args: Optional[Dict[str, Any]] = None,
    extraction_row: Optional[Dict[str, Any]] = None,
    destination: str = "",
    session_prompt: Optional[str] = None,
) -> MappingExecutionContext:
    snap = dict(classification_snapshot or {})
    args = step_args or {}
    row = extraction_row or {}
    dest_type = normalize_connector_type(
        destination or args.get("destination") or snap.get("destination") or "unknown"
    )

    contract = _plan_contract_from_snap(snap)
    source = contract.source if contract.source.entity_key else _side_from_classification(snap, "source")
    destination_ref = (
        contract.destination
        if contract.destination.entity_key or contract.destination.connector_type != "unknown"
        else _side_from_classification(snap, "destination")
    )

    entity_key = pinned_destination_entity_key(
        classification_snapshot=snap,
        step_args=args,
        destination=dest_type,
    )
    if entity_key:
        destination_ref = destination_ref.model_copy(
            update={
                "entity_key": entity_key,
                "resolution_source": "plan_pinned",
                "confidence": max(float(destination_ref.confidence or 0), 0.95),
            }
        )
        if dest_type in ("google_sheets", "gsheets"):
            destination_ref = destination_ref.model_copy(
                update={"sheet_name": entity_key or destination_ref.sheet_name}
            )
        mapping_target = destination_ref.model_copy()
    else:
        mapping_target = contract.mapping_entity()

    migration_intent = dict(contract.migration_intent or snap.get("migration_intent") or {})
    recovery = (
        (args.get("mapping_context_summary") or "").strip()
        or (snap.get("recovery_mapping_context") or "").strip()
        or None
    )
    # Recovery-only hints — not the full pipeline prompt
    if recovery and session_prompt and recovery == (session_prompt or "").strip():
        recovery = None

    sample = {}
    data = row.get("extracted_data") or []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        sample = data[0]
    elif isinstance(data, dict):
        sample = data

    column_keys = [k for k in sample.keys() if k not in ("Attachments", "_attachments", "attachments")]

    plan_status = (snap.get("plan_status") or "").strip().lower()
    approved_plan = plan_status in ("approved", "running") or bool(snap.get("planner_resolved"))

    mapping_brief = compose_mapping_brief(
        source=source,
        destination=destination_ref,
        mapping_target=mapping_target,
        migration_intent=migration_intent,
        recovery_field_hints=recovery,
    )

    return MappingExecutionContext(
        source=source,
        destination=destination_ref,
        mapping_target=mapping_target,
        migration_intent=migration_intent,
        mapping_brief=mapping_brief,
        job_id=str(args.get("job_id") or row.get("job_id") or ""),
        sample_record=sample,
        column_keys=column_keys,
        approved_plan=approved_plan,
        recovery_field_hints=recovery,
        destination_connector_type=dest_type,
    )


def requires_destination_entity_for_mapping(destination: str) -> bool:
    return normalize_connector_type(destination) in ("salesforce", "sql", "google_sheets")
