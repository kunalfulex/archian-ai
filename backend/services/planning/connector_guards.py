"""Canonical connector-type validation for classification and planning."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.connectors.registry import connector_aliases, connector_types

UNRESOLVED_CONNECTOR = "unknown"


def canonicalize_connector(value: Optional[str]) -> str:
    """Map aliases to known types; coerce empty/invalid LLM output to unresolved."""
    raw = (value or "").strip().lower()
    if not raw:
        return UNRESOLVED_CONNECTOR
    normalized = connector_aliases().get(raw, raw)
    if normalized in connector_types():
        return normalized
    return UNRESOLVED_CONNECTOR


def is_known_connector(value: Optional[str]) -> bool:
    return canonicalize_connector(value) in connector_types()


def is_unresolved_connector(value: Optional[str]) -> bool:
    return not is_known_connector(value)


def is_unknown_resource_name(value: Optional[str]) -> bool:
    """True when a connector display name / resource name is missing."""
    v = (value or "").strip().lower()
    return not v or v == UNRESOLVED_CONNECTOR


def destination_connector_structurally_pinned(classification: Dict[str, Any]) -> bool:
    """
    True when destination has concrete instance pins without a user clar flag.

    Covers migrate-to-existing workbook (spreadsheet_id), connector ids, and
    destination_same_as_source when the source endpoint is already resolved.
    """
    from services.planning.schemas import normalize_connector_type

    dest = normalize_connector_type(classification.get("destination") or "")
    if dest in ("unknown", ""):
        return False
    if (
        (classification.get("destination_connector_id") or "").strip()
        or (classification.get("google_sheets_connection_id") or "").strip()
    ):
        return True
    if dest == "google_sheets" and (classification.get("destination_spreadsheet_id") or "").strip():
        return True
    if classification.get("destination_same_as_source"):
        if not classification_source_endpoint_resolved(classification):
            return False
        name = (
            (classification.get("destination_name") or "").strip()
            or (classification.get("source_name") or "").strip()
        )
        if name and not is_unknown_resource_name(name):
            return True
        if (classification.get("source_spreadsheet_id") or "").strip():
            return True
    return False


def sanitize_classification_connectors(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize source/destination and nested connector_type fields after LLM output."""
    out = dict(classification or {})
    for key in ("source", "destination"):
        if key in out:
            out[key] = canonicalize_connector(out.get(key))
    for list_key in ("extract_sources", "planner_additional_sources"):
        items = out.get(list_key)
        if not isinstance(items, list):
            continue
        normalized: list[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if row.get("connector_type") is not None:
                row["connector_type"] = canonicalize_connector(str(row.get("connector_type")))
            normalized.append(row)
        out[list_key] = normalized
    return out


def _goal_operations(classification: Dict[str, Any]) -> List[str]:
    from services.planning.plan_scope import read_plan_scope

    goal = read_plan_scope(classification)
    if isinstance(goal, dict) and goal.get("operations"):
        return [str(o).lower() for o in goal.get("operations") or []]
    return [str(o).lower() for o in (classification.get("operations") or [])]


def _source_connector_required(
    classification: Dict[str, Any],
    *,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    """Delegate to op_requirements when scope is available; else derive from goal ops."""
    from services.planning.op_requirements import (
        derive_plan_requirements,
        read_plan_requirements,
        requirements_need_source_connector,
    )

    if pipeline_tokens is not None:
        if not pipeline_tokens:
            return False
        if pipeline_tokens == ["data_ops"] and classification.get("active_job_id"):
            return False
        reqs = derive_plan_requirements(pipeline_tokens, classification=classification)
        return requirements_need_source_connector(reqs)

    if classification.get("_plan_requirements") or classification.get("_pipeline_tokens"):
        return requirements_need_source_connector(read_plan_requirements(classification))

    ops = _goal_operations(classification)
    if ops:
        from services.planning.plan_scope import read_plan_scope, resolve_pipeline_tokens_from_goal

        goal = read_plan_scope(classification) or {"operations": ops}
        tokens = resolve_pipeline_tokens_from_goal(classification, goal)
        if tokens:
            reqs = derive_plan_requirements(
                tokens,
                classification=classification,
                parsed_goal=goal,
            )
            return requirements_need_source_connector(reqs)

    intent = (classification.get("intent") or "").lower()
    mode = (classification.get("mode") or "").lower()
    if intent in ("general",) or classification.get("conversational_turn"):
        return False
    if intent == "info" and mode not in ("extract", "migrate", "map"):
        return False
    if intent in ("extract", "migrate", "migrate_data", "map_data", "metadata_deploy"):
        return True
    if mode in ("extract", "migrate", "map"):
        return True
    if any(o in ops for o in ("extract", "migrate", "map", "data_ops")):
        return True
    return False


def classification_needs_source_endpoint(
    classification: Dict[str, Any],
    *,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    """True when planning should require a resolved extract source connector."""
    return _source_connector_required(classification, pipeline_tokens=pipeline_tokens)


_GENERIC_SOURCE_NAMES = frozenset(
    {
        "sql database",
        "salesforce org",
        "google sheets",
        "spreadsheet",
        "workbook",
    }
)


def classification_user_requested_destination(classification: Dict[str, Any]) -> bool:
    """True when the user named a migrate/map target — not a classifier default alone."""
    intent = (classification.get("intent") or "").lower()
    mode = (classification.get("mode") or "").lower()
    ops = [str(o).lower() for o in (classification.get("operations") or [])]

    if intent in ("migrate", "migrate_data", "map_data", "create", "upsert", "metadata_deploy"):
        return True
    if mode in ("migrate", "map"):
        return True
    if any(o in ops for o in ("migrate", "map")):
        return True

    dest = canonicalize_connector(classification.get("destination"))
    if dest == UNRESOLVED_CONNECTOR or not is_known_connector(dest):
        return False

    if (classification.get("destination_object_api_name") or "").strip():
        return True
    if (classification.get("destination_sheet_name") or "").strip():
        return True
    if classification.get("planner_destination_confirmed"):
        return True
    if classification.get("_destination_endpoint_clarified"):
        return True
    if classification.get("destination_same_as_source"):
        return True

    name = (classification.get("destination_name") or "").strip()
    if is_unknown_resource_name(name) or name.lower() in _GENERIC_SOURCE_NAMES:
        return False
    return bool(name)


def _destination_connector_required(
    classification: Dict[str, Any],
    *,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    from services.planning.op_requirements import (
        derive_plan_requirements,
        read_plan_requirements,
        requirements_need_destination_connector,
    )

    if pipeline_tokens is not None:
        reqs = derive_plan_requirements(pipeline_tokens, classification=classification)
        return requirements_need_destination_connector(reqs)

    if classification.get("_plan_requirements") or classification.get("_pipeline_tokens"):
        return requirements_need_destination_connector(read_plan_requirements(classification))

    intent = (classification.get("intent") or "").lower()
    mode = (classification.get("mode") or "").lower()
    if intent in ("general",) or classification.get("conversational_turn"):
        return False
    if intent == "info" and mode not in ("migrate", "map"):
        return False
    session_action = (classification.get("session_action") or "").strip()
    if session_action == "fresh_extract":
        return False

    ops = _goal_operations(classification)
    if ops:
        from services.planning.plan_scope import read_plan_scope, resolve_pipeline_tokens_from_goal

        goal = read_plan_scope(classification) or {"operations": ops}
        tokens = resolve_pipeline_tokens_from_goal(classification, goal)
        if tokens:
            reqs = derive_plan_requirements(
                tokens,
                classification=classification,
                parsed_goal=goal,
            )
            return requirements_need_destination_connector(reqs)

    if intent in ("migrate", "migrate_data", "map_data", "create", "upsert"):
        return True
    if mode in ("migrate", "map"):
        return True
    if any(o in ops for o in ("migrate", "map")):
        return True
    src = canonicalize_connector(classification.get("source"))
    dest = canonicalize_connector(classification.get("destination"))
    if is_known_connector(src) and is_known_connector(dest) and src != dest:
        return classification_user_requested_destination(classification)
    return False


def classification_needs_destination_endpoint(
    classification: Dict[str, Any],
    *,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    """True when planning should require a resolved migrate/map destination connector."""
    return _destination_connector_required(classification, pipeline_tokens=pipeline_tokens)


def classification_destination_endpoint_resolved(
    classification: Dict[str, Any],
    *,
    catalog: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when migrate destination has a validated, authoritatively confirmed connector instance."""
    if not classification_needs_destination_endpoint(classification):
        return True

    from services.planning.destination_catalog_validate import (
        destination_connector_slot_satisfied,
        read_needs_destination,
    )

    if not read_needs_destination(classification, parsed_goal):
        return True

    if classification.get("destination_same_as_source"):
        dest = canonicalize_connector(classification.get("destination"))
        if not is_known_connector(dest):
            return False
        from services.planning.destination_catalog_validate import (
            destination_connector_slot_satisfied,
        )

        if not destination_connector_structurally_pinned(classification):
            from services.planning.destination_catalog_validate import (
                destination_connector_authoritatively_confirmed,
            )

            if not destination_connector_authoritatively_confirmed(classification):
                return False
        if catalog is not None:
            return destination_connector_slot_satisfied(
                classification, catalog, parsed_goal=parsed_goal
            )
        name = (
            (classification.get("destination_name") or "").strip()
            or (classification.get("source_name") or "").strip()
        )
        return bool(
            (classification.get("destination_connector_id") or "").strip()
            or (classification.get("source_connector_id") or "").strip()
            or (classification.get("destination_spreadsheet_id") or "").strip()
            or (classification.get("source_spreadsheet_id") or "").strip()
            or (name and not is_unknown_resource_name(name))
        )

    if catalog is not None:
        return destination_connector_slot_satisfied(
            classification, catalog, parsed_goal=parsed_goal
        )

    from services.planning.destination_catalog_validate import (
        destination_connector_authoritatively_confirmed,
    )

    dest = canonicalize_connector(classification.get("destination"))
    if not is_known_connector(dest):
        return False
    return destination_connector_authoritatively_confirmed(classification)


def classification_source_endpoint_resolved(
    classification: Dict[str, Any],
    *,
    pipeline_tokens: Optional[List[str]] = None,
    catalog: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    True when the extract source has a concrete connector instance (not just a type label).

    Prevents entity/field planning while the user still owes pick_sql_connector / pick_salesforce_org.
    When pipeline_tokens is set (e.g. PLAN_SCOPE validation), source need is derived from those ops
    rather than classifier intent alone — so mis-scoped extract on create-only flows is not treated as ready.
    """
    if not classification_needs_source_endpoint(classification, pipeline_tokens=pipeline_tokens):
        return True
    if classification.get("_source_endpoint_clarified"):
        src_early = canonicalize_connector(classification.get("source"))
        if src_early in ("google_sheets", "gsheets"):
            if catalog is not None:
                from services.planning.google_sheets_source import (
                    gsheets_workbook_source_resolved,
                )

                return gsheets_workbook_source_resolved(classification, catalog)
            if (classification.get("source_spreadsheet_id") or "").strip():
                return True
            name = (classification.get("source_name") or "").strip()
            return bool(name) and not is_unknown_resource_name(name)
        return True
    src = canonicalize_connector(classification.get("source"))
    if not is_known_connector(src):
        return False
    if src == "file":
        from services.file_extraction.pending_upload import is_file_source

        if is_file_source(classification):
            return True
        name = (classification.get("source_name") or "").strip()
        return bool(name) and not is_unknown_resource_name(name)
    if src in ("google_sheets", "gsheets"):
        if catalog is not None:
            from services.planning.google_sheets_source import (
                gsheets_workbook_source_resolved,
            )

            return gsheets_workbook_source_resolved(classification, catalog)
        if (classification.get("source_spreadsheet_id") or "").strip():
            return True
        name = (classification.get("source_name") or "").strip()
        return bool(name) and not is_unknown_resource_name(name)
    if (classification.get("source_connector_id") or "").strip():
        return True
    name = (classification.get("source_name") or "").strip()
    if is_unknown_resource_name(name):
        return False
    if name.lower() in _GENERIC_SOURCE_NAMES:
        return False
    return True

# --- Migrate destination guards (merged P4.9) ---

"""Guards: do not create a new spreadsheet when migrating to Salesforce/SQL."""

from typing import Any, Dict, Optional, Tuple

_EXTERNAL_DEST = frozenset({"salesforce", "sql"})


def prompt_explicitly_creates_spreadsheet(
    prompt: Optional[str],
    goal: Optional[Dict[str, Any]] = None,
) -> bool:
    g = dict(goal or {})
    ops = [str(o).lower() for o in (g.get("operations") or [])]
    return "create" in ops


def migrate_to_external_destination_requested(
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
    goal: Optional[Dict[str, Any]] = None,
) -> bool:
    del prompt
    dest = (classification.get("destination") or "").lower()
    if dest not in _EXTERNAL_DEST:
        return False
    dest_name = (classification.get("destination_name") or "").strip()
    if not dest_name or dest_name.lower() == "unknown":
        return False

    g = dict(goal or {})
    ops = [str(o).lower() for o in (g.get("operations") or classification.get("operations") or [])]
    if "migrate" in ops:
        return True
    if classification.get("needs_destination"):
        return True
    return False


def blocks_spreadsheet_create_for_route(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    prompt: Optional[str] = None,
) -> bool:
    """True when create-spreadsheet template must not run (external migrate without explicit new sheet)."""
    if prompt_explicitly_creates_spreadsheet(prompt, goal):
        return False
    return migrate_to_external_destination_requested(classification, prompt, goal)


def apply_migrate_destination_guards(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    prompt: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Clear planner_create_spreadsheet / create_destination when user migrates to SF/SQL
    without explicitly asking to create a new Google Sheet.
    """
    out = dict(classification or {})
    parsed = dict(goal or {})
    if not blocks_spreadsheet_create_for_route(out, parsed, prompt):
        return out, parsed

    out["planner_create_spreadsheet"] = False
    out.pop("planner_new_spreadsheet_name", None)
    parsed["create_destination"] = False
    return out, parsed

# --- Resource validator (merged P4.9) ---

"""Ensure resolved resource names exist in the user catalog."""

from typing import Any, Dict, Optional

from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import normalize_classification_connectors


def _in_catalog(name: Optional[str], names: list) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return True
    return any((x or "").strip().lower() == n for x in names)


def validate_classification_resources(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    if (classification or {}).get("_endpoints_resolved"):
        return normalize_classification_connectors(classification or {})
    out = normalize_classification_connectors(classification or {})
    src = (out.get("source") or "").lower()
    dest = (out.get("destination") or "").lower()

    if src == "sql" and out.get("source_name"):
        if not _in_catalog(out.get("source_name"), catalog.sql_names):
            out["source_name"] = None
    if dest == "sql" and out.get("destination_name"):
        if not _in_catalog(out.get("destination_name"), catalog.sql_names):
            out["destination_name"] = None

    if src == "salesforce" and out.get("source_name"):
        if not _in_catalog(out.get("source_name"), catalog.salesforce_names):
            out["source_name"] = None
    if dest == "salesforce" and out.get("destination_name"):
        if not _in_catalog(out.get("destination_name"), catalog.salesforce_names):
            out["destination_name"] = None

    if src in ("google_sheets", "gsheets"):
        from services.planning.google_sheets_source import (
            split_gsheets_connector_and_workbook_validation_source,
        )

        out = split_gsheets_connector_and_workbook_validation_source(out, catalog)
    if dest in ("google_sheets", "gsheets"):
        from services.planning.google_sheets_destination import (
            split_gsheets_connector_and_workbook_validation,
        )

        out = split_gsheets_connector_and_workbook_validation(out, catalog)

    return out

