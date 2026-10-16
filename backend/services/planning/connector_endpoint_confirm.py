"""Confirm connector instance names (org/DB) were user-specified, not LLM-inferred."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from services.planning.connector_guards import (
    _GENERIC_SOURCE_NAMES,
    canonicalize_connector,
    classification_needs_destination_endpoint,
    classification_needs_source_endpoint,
    is_known_connector,
    is_unknown_resource_name,
)
from services.planning.resource_catalog import ResourceCatalog


def source_connector_authoritatively_confirmed(classification: Dict[str, Any]) -> bool:
    """True when source instance pins came from user clarification or validated resolution."""
    from services.planning.source_catalog_validate import (
        source_connector_authoritatively_confirmed as _confirmed,
    )

    return _confirmed(classification)


def connector_instance_name_explicit_in_prompt(
    prompt: Optional[str],
    instance_name: Optional[str],
    *,
    connector_type: str,
) -> bool:
    """True when the user's message names this connector instance (not only the type)."""
    name = (instance_name or "").strip()
    pl = (prompt or "").strip()
    if not name or not pl:
        return False

    ct = canonicalize_connector(connector_type)
    pl_lower = pl.lower()
    name_lower = name.lower()

    if name_lower in pl_lower:
        return True

    if ct == "salesforce":
        from services.planning.resource_resolver import normalize_salesforce_phrase

        norm_name = normalize_salesforce_phrase(name).lower()
        norm_prompt = normalize_salesforce_phrase(pl).lower()
        if norm_name and norm_name in norm_prompt:
            return True
        if re.search(rf"\bto\s+{re.escape(name_lower)}\b", pl_lower):
            return True
        if re.search(rf"\bto\s+{re.escape(norm_name)}\b", pl_lower) and norm_name:
            return True
    elif ct == "sql":
        from services.planning.resource_resolver import normalize_sql_connection_phrase

        norm_name = normalize_sql_connection_phrase(name).lower()
        norm_prompt = normalize_sql_connection_phrase(pl).lower()
        if norm_name and norm_name in norm_prompt:
            return True
        if re.search(rf"\bto\s+{re.escape(name_lower)}\b", pl_lower):
            return True

    return False


def _name_in_catalog(name: str, catalog: ResourceCatalog, connector_type: str) -> bool:
    ct = canonicalize_connector(connector_type)
    needle = name.strip().lower()
    if not needle:
        return False
    if ct == "salesforce":
        return any((n or "").strip().lower() == needle for n in catalog.salesforce_names)
    if ct == "sql":
        return any((n or "").strip().lower() == needle for n in catalog.sql_names)
    if ct == "google_sheets":
        return any((n or "").strip().lower() == needle for n in catalog.spreadsheet_names)
    return False


def _clear_source_instance_pins(out: Dict[str, Any]) -> Dict[str, Any]:
    if (out.get("source_name") or "").strip():
        out["source_name"] = None
        out.pop("source_connector_id", None)
    return out


def _clear_destination_instance_pins(out: Dict[str, Any], dest: str) -> Dict[str, Any]:
    if (out.get("destination_name") or "").strip():
        out["destination_name"] = None
        out.pop("destination_connector_id", None)
        if dest == "salesforce":
            out.pop("destination_object_api_name", None)
        elif dest == "sql":
            out.pop("destination_table_name", None)
            out.pop("destination_table", None)
    return out


def sanitize_unconfirmed_source_endpoint_catalog(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Drop unvalidated source instance pins (catalog + authoritative flags only)."""
    out = dict(classification or {})
    if not classification_needs_source_endpoint(out):
        return out
    if source_connector_authoritatively_confirmed(out):
        return out

    src = canonicalize_connector(out.get("source"))
    if not is_known_connector(src):
        return out

    if src == "salesforce" and len(catalog.salesforce_connections) > 1:
        return _clear_source_instance_pins(out)
    if src == "sql" and len(catalog.sql_connections) > 1:
        return _clear_source_instance_pins(out)
    return out


def sanitize_unconfirmed_destination_endpoint_catalog(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Drop unvalidated destination instance pins (catalog + authoritative flags only)."""
    from services.planning.destination_catalog_validate import (
        destination_connector_authoritatively_confirmed,
        destination_connector_slot_satisfied,
        read_needs_destination,
        strip_unvalidated_destination_confirm_flags,
    )
    from services.planning.plan_scope import read_plan_scope

    out = strip_unvalidated_destination_confirm_flags(dict(classification or {}))
    if not classification_needs_destination_endpoint(out):
        return out

    goal = read_plan_scope(out) or None
    if destination_connector_slot_satisfied(out, catalog, parsed_goal=goal):
        return out
    if not read_needs_destination(out, goal):
        return out
    if destination_connector_authoritatively_confirmed(out):
        return out

    dest = canonicalize_connector(out.get("destination"))
    if not is_known_connector(dest):
        return out

    if dest == "salesforce" and len(catalog.salesforce_connections) > 1:
        return _clear_destination_instance_pins(out, dest)
    if dest == "sql" and len(catalog.sql_connections) > 1:
        return _clear_destination_instance_pins(out, dest)
    return out


def sanitize_unconfirmed_connector_endpoints_catalog(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Sanitize source/destination instance names using catalog validation only."""
    out = sanitize_unconfirmed_destination_endpoint_catalog(classification, catalog)
    return sanitize_unconfirmed_source_endpoint_catalog(out, catalog)


def source_endpoint_is_confirmed(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> bool:
    """True when extract source connector instance is user-chosen or explicitly named."""
    if not classification_needs_source_endpoint(classification):
        return True
    if classification.get("_source_endpoint_clarified"):
        return True
    src = canonicalize_connector(classification.get("source"))
    if not is_known_connector(src):
        return False
    if src == "file":
        name = (classification.get("source_name") or "").strip()
        return bool(name) and not is_unknown_resource_name(name)
    if (classification.get("source_connector_id") or "").strip():
        return True
    name = (classification.get("source_name") or "").strip()
    if is_unknown_resource_name(name) or name.lower() in _GENERIC_SOURCE_NAMES:
        return False
    if not _name_in_catalog(name, catalog, src):
        return False
    return connector_instance_name_explicit_in_prompt(prompt, name, connector_type=src)


def destination_endpoint_is_confirmed(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> bool:
    """True when migrate/map destination connector instance is user-chosen or explicitly named."""
    if not classification_needs_destination_endpoint(classification):
        return True
    if classification.get("_destination_endpoint_clarified"):
        return True
    if classification.get("planner_destination_confirmed"):
        return True
    if classification.get("active_job_id"):
        from services.planning.plan_scope import continuation_pipeline_tokens, read_plan_scope

        tokens = continuation_pipeline_tokens(classification, read_plan_scope(classification))
        if "migrate" in tokens or "map" in tokens:
            progress = classification.get("_session_progress") or {}
            last = (
                classification.get("last_completed_step") or progress.get("last_completed_step") or ""
            ).lower()
            if last == "map" or progress.get("mapping_ready"):
                name = (classification.get("destination_name") or "").strip()
                dest = canonicalize_connector(classification.get("destination"))
                if (classification.get("destination_connector_id") or "").strip():
                    return True
                if name and _name_in_catalog(name, catalog, dest):
                    return True

    if classification.get("planner_create_spreadsheet"):
        return True

    dest = canonicalize_connector(classification.get("destination"))
    if not is_known_connector(dest):
        return False
    if (classification.get("destination_connector_id") or "").strip():
        return True

    name = (classification.get("destination_name") or "").strip()
    if is_unknown_resource_name(name) or name.lower() in _GENERIC_SOURCE_NAMES:
        return False
    if not _name_in_catalog(name, catalog, dest):
        return False
    return connector_instance_name_explicit_in_prompt(prompt, name, connector_type=dest)


def sanitize_unconfirmed_connector_endpoints(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Sanitize source and destination instance names before gap detection."""
    out = dict(classification or {})
    plan_route = (out.get("plan_route") or "").strip()
    if plan_route in ("continuation_path", "info_path", "general_path"):
        return out
    if out.get("_endpoints_resolved"):
        return out

    return sanitize_unconfirmed_connector_endpoints_catalog(out, catalog)
