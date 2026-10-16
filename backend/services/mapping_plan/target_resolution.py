"""Connector-agnostic mapping target resolution contract and strategies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MappingTargetResolution:
    connector_type: str
    resolved_target: Optional[str] = None
    resolution_source: str = "source_fallback"
    confidence: float = 0.0
    clarification_needed: bool = False
    candidates: List[str] = field(default_factory=list)
    target_object_api_name: Optional[str] = None
    target_table: Optional[str] = None
    target_sheet: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _snap_hint(snap: Dict[str, Any], dest: str) -> Optional[str]:
    contract = snap.get("plan_contract") or {}
    if isinstance(contract, dict):
        for side_key in ("mapping_target", "destination"):
            side = contract.get(side_key)
            if isinstance(side, dict):
                key = (side.get("entity_key") or "").strip()
                if key and key.lower() not in ("unknown",):
                    if dest in ("google_sheets", "gsheets") and "::" in key:
                        return key.split("::", 1)[-1].strip() or key
                    return key

    if dest == "salesforce":
        v = (snap.get("destination_object_api_name") or "").strip()
    elif dest == "sql":
        v = (snap.get("destination_table_name") or snap.get("destination_table") or "").strip()
    elif dest in ("google_sheets", "gsheets"):
        v = (snap.get("destination_sheet_name") or "").strip()
    else:
        return None
    return v if v and v.lower() not in ("unknown",) else None


def _hint_resolution_source(snap: Dict[str, Any]) -> str:
    contract = snap.get("plan_contract") or {}
    if not isinstance(contract, dict):
        return "classification_explicit"
    for side_key in ("mapping_target", "destination"):
        side = contract.get(side_key)
        if isinstance(side, dict) and (side.get("entity_key") or "").strip():
            return "plan_pinned"
    return "classification_explicit"


def resolve_mapping_target(
    *,
    destination: str,
    mapping_instruction: Optional[str],
    classification_snapshot: Optional[Dict[str, Any]] = None,
) -> MappingTargetResolution:
    dest = (destination or "").strip().lower()
    snap = classification_snapshot or {}
    instruction = (mapping_instruction or "").strip()

    hint = _snap_hint(snap, dest)
    if hint:
        pin_source = _hint_resolution_source(snap)
        confidence = 0.95 if pin_source == "plan_pinned" else 0.85
        if dest == "salesforce":
            return MappingTargetResolution(
                connector_type="salesforce",
                resolved_target=hint,
                target_object_api_name=hint,
                resolution_source=pin_source,
                confidence=confidence,
            )
        if dest == "sql":
            return MappingTargetResolution(
                connector_type="sql",
                resolved_target=hint,
                target_table=hint,
                resolution_source=pin_source,
                confidence=confidence,
            )
        if dest in ("google_sheets", "gsheets"):
            return MappingTargetResolution(
                connector_type="google_sheets",
                resolved_target=hint,
                target_sheet=hint,
                resolution_source=pin_source,
                confidence=confidence,
            )

    if dest == "salesforce":
        # Salesforce prompt targets require async catalog + LLM (resolve_mapping_target_async).
        return MappingTargetResolution(connector_type="salesforce")

    if dest == "sql":
        from services.mapping_plan.parse_instruction import parse_mapping_instruction

        parsed = parse_mapping_instruction(instruction, {}, destination="sql")
        if parsed.target_table:
            return MappingTargetResolution(
                connector_type="sql",
                resolved_target=parsed.target_table,
                target_table=parsed.target_table,
                resolution_source="prompt_explicit",
                confidence=0.9,
            )
        return MappingTargetResolution(connector_type="sql")

    if dest in ("google_sheets", "gsheets"):
        from services.mapping_plan.parse_instruction import parse_mapping_instruction

        parsed = parse_mapping_instruction(instruction, {}, destination="google_sheets")
        if parsed.target_sheet:
            return MappingTargetResolution(
                connector_type="google_sheets",
                resolved_target=parsed.target_sheet,
                target_sheet=parsed.target_sheet,
                resolution_source="prompt_explicit",
                confidence=0.9,
            )
        return MappingTargetResolution(connector_type="google_sheets")

    return MappingTargetResolution(connector_type=dest or "unknown")


async def resolve_mapping_target_async(
    *,
    destination: str,
    mapping_instruction: Optional[str],
    classification_snapshot: Optional[Dict[str, Any]] = None,
    user_id: Any = None,
    destination_name: Optional[str] = None,
) -> MappingTargetResolution:
    """Async hybrid resolver — DB catalog + LLM (same pattern as source selection)."""
    snap = classification_snapshot or {}
    base = resolve_mapping_target(
        destination=destination,
        mapping_instruction=mapping_instruction,
        classification_snapshot=snap,
    )
    dest = (destination or "").strip().lower()
    if not user_id:
        return base

    has_target = bool(
        base.target_object_api_name or base.target_table or base.target_sheet
    )
    # Plan / classification pins are authoritative — do not re-resolve via catalog LLM.
    hint = _snap_hint(snap, dest)
    if hint:
        pinned = resolve_mapping_target(
            destination=destination,
            mapping_instruction=mapping_instruction,
            classification_snapshot=snap,
        )
        if pinned.resolution_source == "plan_pinned":
            pinned.confidence = 0.95
        return pinned

    instruction = (mapping_instruction or snap.get("original_prompt") or "").strip()

    if dest == "salesforce":
        from utils.destination_connector_resolve import resolve_salesforce_token_record
        from services.mapping_plan.catalog_target_resolver import (
            resolve_salesforce_destination_from_catalog,
        )

        tr = await resolve_salesforce_token_record(user_id, destination_name=destination_name)
        org_id = tr.get("org_id") if tr else None
        resolved = await resolve_salesforce_destination_from_catalog(
            user_id=user_id,
            org_id=org_id,
            prompt=instruction,
            hint=hint or base.target_object_api_name,
        )
        if not resolved:
            return base
        source = "llm_org_catalog"
        if hint:
            source = "classification_llm_validated"
        return MappingTargetResolution(
            connector_type="salesforce",
            resolved_target=resolved,
            target_object_api_name=resolved,
            resolution_source=source,
            confidence=0.9,
        )

    if dest == "sql":
        from utils.destination_connector_resolve import resolve_sql_connector_record
        from services.mapping_plan.catalog_target_resolver import (
            resolve_sql_destination_table_from_catalog,
        )

        tr = await resolve_sql_connector_record(user_id, destination_name=destination_name)
        connector_id = str(tr.get("id")) if tr and tr.get("id") else None
        if not connector_id and not has_target:
            return base
        resolved = await resolve_sql_destination_table_from_catalog(
            user_id=user_id,
            connector_id=connector_id,
            prompt=instruction,
            hint=hint or base.target_table,
        )
        if not resolved:
            return base
        source = "llm_sql_catalog" if not hint else "classification_llm_validated"
        return MappingTargetResolution(
            connector_type="sql",
            resolved_target=resolved,
            target_table=resolved,
            resolution_source=source,
            confidence=0.9,
        )

    if dest in ("google_sheets", "gsheets"):
        from services.mapping_plan.catalog_target_resolver import (
            resolve_google_sheets_destination_from_catalog,
        )

        spreadsheet_id = (
            (snap.get("destination_spreadsheet_id") or "").strip()
            or None
        )
        resolved = await resolve_google_sheets_destination_from_catalog(
            user_id=user_id,
            spreadsheet_name=destination_name,
            spreadsheet_id=spreadsheet_id,
            prompt=instruction,
            hint=hint or base.target_sheet,
        )
        if not resolved:
            return base
        source = "llm_gsheets_catalog" if not hint else "classification_llm_validated"
        return MappingTargetResolution(
            connector_type="google_sheets",
            resolved_target=resolved,
            target_sheet=resolved,
            resolution_source=source,
            confidence=0.9,
        )

    return base
