"""Canonical slot-fill state on plans — durable confirmation across metadata clar resumes."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.schemas import ExecutionPlan, normalize_connector_type

SLOT_DESTINATION_CONNECTOR = "destination_connector"
SLOT_DESTINATION_ENTITY = "destination_entity"
SLOT_SOURCE_CONNECTOR = "source_connector"
SLOT_SOURCE_ENTITY = "source_entity"
SLOT_CREATE_TITLE = "create_title"

_INSTANCE_DESTINATION_CLAR_TYPES = frozenset(
    {
        "pick_google_account",
        "pick_salesforce_org",
        "pick_sql_connector",
        "pick_tab",
    }
)

_ENTITY_CLAR_TYPES = frozenset(
    {
        "pick_workbook",
        "pick_workbook_ambiguous",
        "pick_tab",
        "pick_destination_entity",
        "pick_source_entity",
        "pick_source_workbook",
    }
)

_CLAR_TO_SLOT: Dict[str, str] = {
    "pick_source_connector": SLOT_SOURCE_CONNECTOR,
    "pick_google_account": SLOT_DESTINATION_CONNECTOR,
    "pick_salesforce_org": SLOT_DESTINATION_CONNECTOR,
    "pick_sql_connector": SLOT_DESTINATION_CONNECTOR,
    "pick_destination_connector": SLOT_DESTINATION_CONNECTOR,
    "pick_workbook": SLOT_DESTINATION_ENTITY,
    "pick_workbook_ambiguous": SLOT_DESTINATION_ENTITY,
    "pick_source_workbook": SLOT_SOURCE_ENTITY,
    "pick_tab": SLOT_DESTINATION_ENTITY,
    "pick_destination_entity": SLOT_DESTINATION_ENTITY,
    "pick_source_entity": SLOT_SOURCE_ENTITY,
    "create_workbook_name": SLOT_CREATE_TITLE,
}


def _destination_connector_fill_is_concrete(
    classification: Dict[str, Any],
    clarification_type: str,
    *,
    option_id: str = "",
) -> bool:
    """Type-only connector picks must not produce durable destination slot fills."""
    ctype = (clarification_type or "").strip()
    conn_id = (
        (classification.get("destination_connector_id") or "").strip()
        or (classification.get("google_sheets_connection_id") or "").strip()
    )
    if conn_id:
        return True
    if (classification.get("destination_spreadsheet_id") or "").strip():
        return True
    if classification.get("_destination_endpoint_clarified") or classification.get(
        "_destination_connector_validated"
    ):
        return True
    if ctype in _INSTANCE_DESTINATION_CLAR_TYPES:
        return True
    if ctype == "pick_destination_connector":
        opt = (option_id or "").strip()
        if opt.startswith(("dest_type_", "src_type_", "label_destination_", "label_source_")):
            return False
        return bool(opt)
    return False


def _entity_slot_fill_from_classification(
    classification: Dict[str, Any],
    *,
    role: str,
) -> Optional[Dict[str, Any]]:
    """Build a durable entity slot fill from classification pins after clar apply."""
    from services.planning.entity_pins import pinned_entity_key

    prefix = "source" if role == "source" else "destination"
    ct = normalize_connector_type(classification.get(prefix) or "")
    if ct in ("unknown", ""):
        return None

    entity_key = (classification.get(f"{prefix}_object_api_name") or "").strip()
    if not entity_key:
        entity_key = pinned_entity_key(classification, role=role, connector_type=ct)  # type: ignore[arg-type]
    if ct in ("google_sheets", "gsheets"):
        sid = (classification.get(f"{prefix}_spreadsheet_id") or "").strip()
        tab = (classification.get(f"{prefix}_sheet_name") or "").strip()
        if sid and tab and not entity_key:
            entity_key = f"{sid}::{tab}"
        elif sid and "::" not in (entity_key or "") and tab:
            entity_key = f"{sid}::{tab}"

    confirmed = bool(classification.get(f"planner_{prefix}_entity_confirmed"))
    if not entity_key and not confirmed:
        return None

    entry: Dict[str, Any] = {
        "confirmed": True,
        "connector_type": ct,
        "source": "user_clarification",
    }
    if entity_key:
        entry["entity_key"] = entity_key
    display = (classification.get(f"{prefix}_name") or "").strip()
    if display:
        entry["display_name"] = display
    if ct in ("google_sheets", "gsheets"):
        sid = (classification.get(f"{prefix}_spreadsheet_id") or "").strip()
        tab = (classification.get(f"{prefix}_sheet_name") or "").strip()
        if sid:
            entry["spreadsheet_id"] = sid
        if tab:
            entry["sheet_name"] = tab
        if sid and tab:
            entry["entity_key"] = f"{sid}::{tab}"
    elif ct == "sql":
        table = (
            (classification.get(f"{prefix}_table_name") or classification.get(f"{prefix}_table") or "")
            .strip()
        )
        if table:
            entry["entity_key"] = table
    return entry


def _apply_entity_slot_fill_to_classification(
    classification: Dict[str, Any],
    fill: Dict[str, Any],
    *,
    role: str,
) -> Dict[str, Any]:
    """Project a durable entity slot fill onto legacy classification pins."""
    from services.planning.entity_pins import write_pinned_entity_key
    from services.planning.planning_checkpoint import should_restore_pin

    out = dict(classification or {})
    if not fill.get("confirmed"):
        return out

    ct = normalize_connector_type(
        fill.get("connector_type")
        or out.get("source" if role == "source" else "destination")
        or ""
    )
    prefix = "source" if role == "source" else "destination"
    if ct and ct not in ("unknown", ""):
        out[prefix] = ct

    entity_key = (fill.get("entity_key") or "").strip()
    display = (fill.get("display_name") or "").strip()
    if display and should_restore_pin(display, out.get(f"{prefix}_name")):
        out[f"{prefix}_name"] = display

    if ct in ("google_sheets", "gsheets"):
        sid = (fill.get("spreadsheet_id") or "").strip()
        tab = (fill.get("sheet_name") or "").strip()
        if entity_key and "::" in entity_key:
            sid_part, tab_part = entity_key.split("::", 1)
            sid = sid or sid_part
            tab = tab or tab_part
        if sid and should_restore_pin(sid, out.get(f"{prefix}_spreadsheet_id")):
            out[f"{prefix}_spreadsheet_id"] = sid
        if tab and should_restore_pin(tab, out.get(f"{prefix}_sheet_name")):
            out[f"{prefix}_sheet_name"] = tab
        composite = f"{sid}::{tab}" if sid and tab else entity_key
        if composite and should_restore_pin(composite, out.get(f"{prefix}_object_api_name")):
            out[f"{prefix}_object_api_name"] = composite
    elif entity_key:
        out = write_pinned_entity_key(out, role=role, connector_type=ct, entity_key=entity_key)

    if entity_key or fill.get("confirmed"):
        out[f"planner_{prefix}_entity_confirmed"] = True
    if role == "destination" and ct in ("google_sheets", "gsheets"):
        out["planner_workbook_picker_done"] = True
        out["planner_destination_confirmed"] = True
        out["_destination_endpoint_clarified"] = True
    if role == "source" and ct in ("google_sheets", "gsheets"):
        out["planner_source_workbook_picker_done"] = True
    return out


def read_slot_fills(
    classification: Optional[Dict[str, Any]] = None,
    plan: Optional[ExecutionPlan] = None,
) -> Dict[str, Any]:
    """Merge slot fills from classification mirror and plan metadata."""
    out: Dict[str, Any] = {}
    if classification:
        raw = classification.get("_slot_fills")
        if isinstance(raw, dict):
            out.update(raw)
    if plan and isinstance(plan.metadata, dict):
        raw = plan.metadata.get("slot_fills")
        if isinstance(raw, dict):
            out = {**out, **raw}
    return out


def write_slot_fills_to_plan(plan: ExecutionPlan, fills: Dict[str, Any]) -> ExecutionPlan:
    plan = plan.model_copy(deep=True)
    plan.metadata = dict(plan.metadata or {})
    plan.metadata["slot_fills"] = dict(fills or {})
    return plan


def record_clarification_slot_fill(
    fills: Dict[str, Any],
    clarification_type: str,
    *,
    classification: Dict[str, Any],
    option_id: str = "",
) -> Dict[str, Any]:
    """Record a user clarification answer into slot_fills."""
    ctype = (clarification_type or "").strip()
    slot = _CLAR_TO_SLOT.get(ctype)
    if not slot:
        return fills

    out = dict(fills or {})
    if slot == SLOT_SOURCE_CONNECTOR:
        conn_id = (classification.get("source_connector_id") or "").strip()
        if not (
            conn_id
            or classification.get("_source_endpoint_clarified")
            or classification.get("_source_connector_validated")
        ):
            return fills
        entry: Dict[str, Any] = {
            "confirmed": True,
            "clarification_type": ctype,
            "source": "user_clarification",
            "connector_type": (classification.get("source") or "").strip(),
        }
        if conn_id:
            entry["connector_id"] = conn_id
        if option_id:
            entry["option_id"] = option_id
        out[SLOT_SOURCE_CONNECTOR] = entry
    elif slot == SLOT_DESTINATION_CONNECTOR:
        if not _destination_connector_fill_is_concrete(
            classification, ctype, option_id=option_id
        ):
            return fills
        conn_id = (
            (classification.get("destination_connector_id") or "").strip()
            or (classification.get("google_sheets_connection_id") or "").strip()
            or (classification.get("source_connector_id") or "").strip()
        )
        entry = {
            "confirmed": True,
            "clarification_type": ctype,
            "source": "user_clarification",
            "connector_type": (classification.get("destination") or classification.get("source") or "").strip(),
        }
        if conn_id:
            entry["connector_id"] = conn_id
        if option_id:
            entry["option_id"] = option_id
        out[SLOT_DESTINATION_CONNECTOR] = entry
    elif slot in (SLOT_DESTINATION_ENTITY, SLOT_SOURCE_ENTITY):
        role = "destination" if slot == SLOT_DESTINATION_ENTITY else "source"
        entry = _entity_slot_fill_from_classification(classification, role=role)
        if not entry:
            return fills
        entry["clarification_type"] = ctype
        entry["source"] = "user_clarification"
        if option_id:
            entry["option_id"] = option_id
        out[slot] = entry
    elif slot == SLOT_CREATE_TITLE:
        title = (classification.get("planner_new_spreadsheet_name") or "").strip()
        if title:
            out[SLOT_CREATE_TITLE] = {
                "confirmed": True,
                "title": title,
                "clarification_type": ctype,
                "source": "user_clarification",
                "option_id": option_id or None,
            }
    return out


def record_authoritative_connector_slot_fills(
    classification: Dict[str, Any],
    *,
    plan: Optional[ExecutionPlan] = None,
) -> Dict[str, Any]:
    """Record catalog/LLM-confirmed connector slots for metadata clarification resumes."""
    from services.planning.connector_guards import classification_source_endpoint_resolved
    from services.planning.destination_catalog_validate import (
        destination_connector_authoritatively_confirmed,
        read_needs_destination,
    )
    from services.planning.plan_scope import read_plan_scope
    from services.planning.source_catalog_validate import read_needs_source

    fills = read_slot_fills(classification, plan)
    goal = read_plan_scope(classification)
    out = dict(fills or {})

    if read_needs_source(classification, goal) and classification_source_endpoint_resolved(
        classification
    ):
        conn_id = (classification.get("source_connector_id") or "").strip()
        entry: Dict[str, Any] = {
            "confirmed": True,
            "clarification_type": "authoritative_bind",
            "source": "authoritative_bind",
            "connector_type": (classification.get("source") or "").strip(),
        }
        if conn_id:
            entry["connector_id"] = conn_id
        out[SLOT_SOURCE_CONNECTOR] = entry

    if read_needs_destination(classification, goal) and destination_connector_authoritatively_confirmed(
        classification
    ):
        conn_id = (
            (classification.get("destination_connector_id") or "").strip()
            or (classification.get("google_sheets_connection_id") or "").strip()
        )
        entry = {
            "confirmed": True,
            "clarification_type": "authoritative_bind",
            "source": "authoritative_bind",
            "connector_type": (classification.get("destination") or "").strip(),
        }
        if conn_id:
            entry["connector_id"] = conn_id
        out[SLOT_DESTINATION_CONNECTOR] = entry

    return out


def sync_slot_fills_from_plan_clarifications(
    plan: ExecutionPlan,
    classification: Dict[str, Any],
) -> tuple[Dict[str, Any], ExecutionPlan]:
    """
    Rebuild slot fills from clarifications already marked selected on the plan.

    Ensures multi-step resumes retain account pick when only the title is submitted.
    """
    fills = read_slot_fills(classification, plan)
    for clar in plan.clarifications:
        if clar.required and not clar.selected_option_id:
            continue
        fills = record_clarification_slot_fill(
            fills,
            clar.clarification_type,
            classification=classification,
            option_id=(clar.selected_option_id or "").strip(),
        )
    plan = write_slot_fills_to_plan(plan, fills)
    return fills, plan


def restore_slot_fills(
    classification: Dict[str, Any],
    plan: ExecutionPlan,
    prior_classification: Optional[Dict[str, Any]] = None,
    prior_plan: Optional[ExecutionPlan] = None,
) -> tuple[Dict[str, Any], ExecutionPlan]:
    """Merge slot fills from prior pending so later metadata resumes keep earlier picks."""
    prior_fills = read_slot_fills(prior_classification, prior_plan)
    current_fills = read_slot_fills(classification, plan)
    merged = {**prior_fills, **current_fills}
    plan = write_slot_fills_to_plan(plan, merged)
    return merged, plan


def hydrate_classification_from_slot_fills(
    classification: Dict[str, Any],
    *,
    plan: Optional[ExecutionPlan] = None,
) -> Dict[str, Any]:
    """Restore classification pins from durable slot fills before kernel resume."""
    out = dict(classification or {})
    fills = read_slot_fills(out, plan)

    dest_conn = fills.get(SLOT_DESTINATION_CONNECTOR) or {}
    if dest_conn.get("confirmed"):
        conn_id = (dest_conn.get("connector_id") or "").strip()
        ct = normalize_connector_type(dest_conn.get("connector_type") or out.get("destination") or "")
        if ct and ct not in ("unknown", ""):
            out["destination"] = ct
        if conn_id:
            out["destination_connector_id"] = conn_id
            if ct in ("google_sheets", "gsheets"):
                out["google_sheets_connection_id"] = conn_id

    dest_entity = fills.get(SLOT_DESTINATION_ENTITY) or {}
    if dest_entity.get("confirmed"):
        out = _apply_entity_slot_fill_to_classification(out, dest_entity, role="destination")

    src_entity = fills.get(SLOT_SOURCE_ENTITY) or {}
    if src_entity.get("confirmed"):
        out = _apply_entity_slot_fill_to_classification(out, src_entity, role="source")

    title = fills.get(SLOT_CREATE_TITLE) or {}
    if title.get("confirmed") and (title.get("title") or "").strip():
        out["planner_spreadsheet_title_confirmed"] = True
        out["planner_new_spreadsheet_name"] = (title.get("title") or "").strip()
        out["planner_destination_entity_confirmed"] = True
        if not out.get("destination_name"):
            out["destination_name"] = out["planner_new_spreadsheet_name"]

    if fills:
        out["_slot_fills"] = fills
    return out


def sync_authoritative_flags_from_slot_fills(
    classification: Dict[str, Any],
    *,
    plan: Optional[ExecutionPlan] = None,
) -> Dict[str, Any]:
    """Derive legacy confirm flags from durable slot fills."""
    out = hydrate_classification_from_slot_fills(classification, plan=plan)
    fills = read_slot_fills(out, plan)
    src = fills.get(SLOT_SOURCE_CONNECTOR) or {}
    if src.get("confirmed") and (
        src.get("connector_id") or src.get("source") == "authoritative_bind"
    ):
        out["_source_endpoint_clarified"] = True
        out["_source_connector_validated"] = True
    dest = fills.get(SLOT_DESTINATION_CONNECTOR) or {}
    if dest.get("confirmed") and (
        dest.get("connector_id")
        or dest.get("clarification_type") in _INSTANCE_DESTINATION_CLAR_TYPES
    ):
        out["_destination_endpoint_clarified"] = True
        out["_destination_connector_validated"] = True
    dest_entity = fills.get(SLOT_DESTINATION_ENTITY) or {}
    if dest_entity.get("confirmed") and (dest_entity.get("entity_key") or "").strip():
        out["planner_destination_entity_confirmed"] = True
    out["_slot_fills"] = fills
    return out


def slot_fills_destination_connector_confirmed(
    classification: Optional[Dict[str, Any]] = None,
    plan: Optional[ExecutionPlan] = None,
) -> bool:
    fills = read_slot_fills(classification, plan)
    dest = fills.get(SLOT_DESTINATION_CONNECTOR) or {}
    if not dest.get("confirmed"):
        return False
    if dest.get("connector_id"):
        return True
    return (dest.get("clarification_type") or "") in _INSTANCE_DESTINATION_CLAR_TYPES


def slot_fills_destination_entity_confirmed(
    classification: Optional[Dict[str, Any]] = None,
    plan: Optional[ExecutionPlan] = None,
) -> bool:
    fills = read_slot_fills(classification, plan)
    ent = fills.get(SLOT_DESTINATION_ENTITY) or {}
    return bool(ent.get("confirmed") and (ent.get("entity_key") or "").strip())


def mirror_slot_fills_to_classification(
    classification: Dict[str, Any],
    plan: ExecutionPlan,
) -> Dict[str, Any]:
    """Keep classification._slot_fills in sync with plan.metadata for planner_pending."""
    out = sync_authoritative_flags_from_slot_fills(classification, plan=plan)
    if isinstance(plan.metadata, dict) and plan.metadata.get("slot_fills"):
        out["_slot_fills"] = dict(plan.metadata["slot_fills"])
    return out
