"""Source connector validation — catalog membership only (no keyword routing)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import normalize_connector_type


def read_needs_source(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when PLAN_SCOPE / pipeline requires source connector details."""
    from services.file_extraction.pending_upload import is_file_source
    from services.planning.plan_scope import read_plan_scope

    if is_file_source(classification):
        return False

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    if goal.get("needs_source") is False:
        return False
    if goal.get("needs_source") is True:
        return True

    from services.planning.op_requirements import (
        derive_plan_requirements,
        read_plan_requirements,
        requirements_need_source_connector,
    )

    reqs = read_plan_requirements(classification, parsed_goal=goal or None)
    if requirements_need_source_connector(reqs):
        return True

    ops = [str(o).lower() for o in (goal.get("operations") or classification.get("operations") or [])]
    if ops:
        tokens = classification.get("_pipeline_tokens") or ops
        derived = derive_plan_requirements(
            list(tokens),
            classification=classification,
            parsed_goal=goal,
        )
        return requirements_need_source_connector(derived)

    from services.planning.connector_guards import (
        classification_needs_source_endpoint,
        is_unknown_resource_name,
    )

    source_phrase = (
        goal.get("source_phrase") or classification.get("source_phrase") or ""
    ).strip()
    if (
        source_phrase
        and is_unknown_resource_name(classification.get("source_name"))
        and classification_needs_source_endpoint(classification)
    ):
        return True
    return False


def _sf_org_in_catalog(name: str, catalog: ResourceCatalog) -> bool:
    needle = (name or "").strip().lower()
    return bool(needle) and any((n or "").strip().lower() == needle for n in catalog.salesforce_names)


def _sql_connection_in_catalog(name: str, catalog: ResourceCatalog) -> bool:
    needle = (name or "").strip().lower()
    return bool(needle) and any((n or "").strip().lower() == needle for n in catalog.sql_names)


def _gs_workbook_in_catalog(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    sid = (classification.get("source_spreadsheet_id") or "").strip()
    if sid:
        for row in catalog.google_sheet_rows or []:
            if (row.spreadsheet_id or "").strip() == sid:
                return True
        return False

    name = (classification.get("source_name") or "").strip()
    if not name:
        return False
    from services.planning.google_sheets_destination import is_gsheets_workbook_display_name

    return is_gsheets_workbook_display_name(name, catalog)


def google_sheets_source_account_slot_satisfied(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    """True when google_sheets_connection_id / source_connector_id pins a GS account."""
    from services.planning.google_sheets_source import _sync_source_google_connection_ids

    cls = _sync_source_google_connection_ids(dict(classification or {}), catalog)
    conns = catalog.google_sheets_connections or []
    if not conns:
        return True
    conn_id = (cls.get("google_sheets_connection_id") or "").strip()
    if not conn_id:
        return False
    return any(str(c.connection_id) == conn_id for c in conns if c.connection_id)


def needs_google_sheets_account_pick_source(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    """True when extract source Google Sheets needs account disambiguation."""
    src = normalize_connector_type(classification.get("source") or "")
    if src not in ("google_sheets", "gsheets"):
        return False
    return not google_sheets_source_account_slot_satisfied(classification, catalog)


def needs_google_sheets_source_clarification(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when PLAN_SCOPE requires a Google Sheets source connector clarification."""
    if not read_needs_source(classification, parsed_goal):
        return False
    src = normalize_connector_type(classification.get("source") or "")
    if src not in ("google_sheets", "gsheets"):
        return False
    if classification.get("planner_source_workbook_picker_done"):
        return False
    if source_connector_slot_satisfied(classification, catalog, parsed_goal=parsed_goal):
        return False
    return True


def ensure_google_sheets_source_connection_pin(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Pin the sole Google account when unambiguous (post-LLM / post-user-pick)."""
    from services.planning.google_sheets_source import _sync_source_google_connection_ids

    out = _sync_source_google_connection_ids(dict(classification or {}), catalog)
    if (out.get("google_sheets_connection_id") or "").strip():
        return out
    conns = [c for c in (catalog.google_sheets_connections or []) if c.connection_id]
    if len(conns) != 1:
        return out
    cid = str(conns[0].connection_id)
    out["google_sheets_connection_id"] = cid
    out["source_connector_id"] = cid
    if conns[0].name:
        out["source_account_name"] = conns[0].name
    return out


def validate_source_connector_pins(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    """True when source connector instance pins reference real catalog rows."""
    src = normalize_connector_type(classification.get("source") or "")
    if src in ("unknown", "", "file"):
        return False

    if src == "google_sheets":
        from services.planning.google_sheets_source import (
            normalize_google_sheets_source_pins,
        )

        cls = normalize_google_sheets_source_pins(dict(classification or {}), catalog)
        if catalog.google_sheets_connections and not google_sheets_source_account_slot_satisfied(
            cls, catalog
        ):
            if len(catalog.google_sheets_connections) == 1:
                cls = ensure_google_sheets_source_connection_pin(cls, catalog)
            if not google_sheets_source_account_slot_satisfied(cls, catalog):
                return False
        # Connector layer: Google account only; workbook/tab resolved at entity bind.
        return True

    if src == "salesforce":
        token = (classification.get("source_connector_id") or "").strip()
        if token:
            return any(str(c.token_id) == token for c in catalog.salesforce_connections or [])
        name = (classification.get("source_name") or "").strip()
        return _sf_org_in_catalog(name, catalog)

    if src == "sql":
        cid = (classification.get("source_connector_id") or "").strip()
        if cid:
            return any(str(c.connector_id) == cid for c in catalog.sql_connections or [])
        name = (classification.get("source_name") or "").strip()
        return _sql_connection_in_catalog(name, catalog)

    return False


def source_connector_authoritatively_confirmed(
    classification: Dict[str, Any],
) -> bool:
    """True when pins came from user clarification, validated LLM, or job inherit."""
    if classification.get("_source_endpoint_clarified"):
        return True
    if classification.get("_source_connector_validated"):
        return True
    if classification.get("_source_inherited_from_job"):
        return True
    return False


def source_connector_slot_satisfied(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """Source connector requirement satisfied: needs_source off, or validated authoritative pins."""
    if not read_needs_source(classification, parsed_goal):
        return True
    src = normalize_connector_type(classification.get("source") or "")
    if src in ("unknown", ""):
        return False
    if not source_connector_authoritatively_confirmed(classification):
        return False
    return validate_source_connector_pins(classification, catalog)


def strip_unvalidated_source_confirm_flags(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Remove source confirm flags when pins are not authoritatively validated."""
    out = dict(classification or {})
    if source_connector_authoritatively_confirmed(out):
        return out
    for key in (
        "planner_source_confirmed",
        "_source_endpoint_clarified",
        "_source_connector_validated",
    ):
        out.pop(key, None)
    return out


def clear_source_when_not_needed(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Remove source pins when PLAN_SCOPE says needs_source is false."""
    from services.file_extraction.pending_upload import is_file_source

    cls = dict(classification or {})
    if (cls.get("active_job_id") or "").strip():
        return cls
    if is_file_source(cls):
        return cls
    if read_needs_source(classification, parsed_goal):
        return cls
    out = cls
    for key in (
        "source",
        "source_name",
        "source_connector_id",
        "source_spreadsheet_id",
        "source_sheet_name",
        "source_phrase",
        "source_object_api_name",
        "source_table_name",
        "planner_source_confirmed",
        "_source_endpoint_clarified",
        "_source_connector_validated",
    ):
        out.pop(key, None)
    out["needs_source"] = False
    return out


def mark_source_validated_from_llm(
    classification: Dict[str, Any],
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    """Call after LLM pick passes catalog validation."""
    out = dict(classification or {})
    out["_source_connector_validated"] = True
    out["_source_connector_resolution"] = "llm"

    src = normalize_connector_type(out.get("source") or "")
    if src == "google_sheets" and catalog is not None:
        from services.planning.google_sheets_source import gsheets_workbook_source_resolved

        if not gsheets_workbook_source_resolved(out, catalog):
            return out

    out["planner_source_confirmed"] = True
    out["_source_endpoint_clarified"] = True
    return out
