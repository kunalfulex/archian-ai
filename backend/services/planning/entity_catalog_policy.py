"""Catalog entity guards for vague user-data migrations (avoid metadata tables/objects)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.labeling.catalog_helpers import entity_class_from_semantics
from services.planning.extract_field_clarification import is_vague_extract_prompt

CatalogRow = Dict[str, str]

# Salesforce setup/metadata objects — not business "user data" unless explicitly requested.
SALESFORCE_METADATA_OBJECTS = frozenset(
    {
        "RecordType",
        "RecordTypeLocalization",
        "BusinessProcess",
        "FieldDefinition",
        "EntityDefinition",
        "Layout",
        "ValidationRule",
        "WorkflowRule",
        "FlexiPage",
        "GlobalValueSet",
        "PermissionSet",
        "Profile",
        "UserRole",
        "Group",
        "Queue",
    }
)

_SQL_METADATA_TABLE_MARKERS = (
    "record_type",
    "record_types",
    "salesforce_object_record_types",
    "object_record_types",
    "_metadata",
    "schema_migration",
    "google_spreadsheet",
    "gsheets_",
    "sql_extracted_schemas",
    "extracted_schemas",
    "sql_connectors",
    "migration_flows",
    "migration_flow",
    "export_tasks",
    "extraction_result",
    "chat_messages",
    "chat_conversation",
    "connector_entity_semantics",
    "connector_labeling",
    "connector_business_profiles",
    "intent_state",
    "plan_goal",
    "ai_categorization",
)

_GSHEETS_METADATA_TAB_MARKERS = (
    "user_google_spreadsheets",
    "google_spreadsheet",
    "google_spreadsheets",
    "gsheets_",
    "connector_entity",
    "spreadsheet_registry",
)

_SEMANTICS_METADATA_CATEGORY_MARKERS = (
    "system catalog",
    "system metadata",
    "infrastructure",
    "connector registry",
    "schema catalog",
    "metadata",
)

_USER_DATA_PROMPT_RE = re.compile(
    r"\b(?:user|users|customer|client|member)\s+data\b|\buser\s+table\b|\busers\s+table\b",
    re.I,
)
_RECORD_TYPE_PROMPT_RE = re.compile(r"\brecord\s+types?\b", re.I)

_SF_USER_DATA_OBJECT_PRIORITY = (
    "User",
    "Contact",
    "Lead",
    "PersonAccount",
    "Account",
)

_SQL_USER_DATA_TABLE_PRIORITY = (
    "user",
    "users",
    "app_user",
    "app_users",
    "application_user",
    "customer",
    "customers",
    "contact",
    "contacts",
)


def prompt_requests_record_type(prompt: Optional[str]) -> bool:
    return bool(_RECORD_TYPE_PROMPT_RE.search(prompt or ""))


def is_vague_user_data_migration_prompt(prompt: Optional[str]) -> bool:
    pl = (prompt or "").strip()
    if not pl:
        return False
    if prompt_requests_record_type(pl):
        return False
    if _USER_DATA_PROMPT_RE.search(pl):
        return True
    return is_vague_extract_prompt(pl)


def is_salesforce_metadata_object(api_name: Optional[str]) -> bool:
    api = (api_name or "").strip()
    if not api:
        return False
    if api in SALESFORCE_METADATA_OBJECTS:
        return True
    lowered = api.lower()
    return lowered.endswith("definition") or lowered.endswith("localization")


def is_sql_metadata_table_name(table_name: Optional[str]) -> bool:
    name = (table_name or "").strip().lower()
    if not name:
        return False
    return any(marker in name for marker in _SQL_METADATA_TABLE_MARKERS)


def is_gsheets_metadata_tab_name(tab_or_key: Optional[str]) -> bool:
    name = (tab_or_key or "").strip().lower()
    if not name:
        return False
    if "::" in name:
        name = name.split("::", 1)[-1]
    return any(marker in name for marker in _GSHEETS_METADATA_TAB_MARKERS)


def is_semantics_metadata_entity(semantics: Optional[Dict[str, Any]]) -> bool:
    ec = entity_class_from_semantics(semantics)
    if ec in ("metadata", "system", "infrastructure", "catalog"):
        return True
    if not semantics or not isinstance(semantics, dict):
        return False
    from services.labeling.catalog_helpers import primary_from_semantics

    primary = primary_from_semantics(semantics)
    category = (primary.get("category") or "").strip().lower()
    if category:
        return any(m in category for m in _SEMANTICS_METADATA_CATEGORY_MARKERS)
    return False


def is_metadata_entity(
    entity_key: Optional[str],
    *,
    connector_type: str,
    semantics: Optional[Dict[str, Any]] = None,
) -> bool:
    if is_semantics_metadata_entity(semantics):
        return True
    key = (entity_key or "").strip()
    if not key:
        return False
    ct = (connector_type or "").strip().lower()
    if ct == "salesforce":
        return is_salesforce_metadata_object(key)
    if ct == "sql":
        return is_sql_metadata_table_name(key)
    if ct in ("google_sheets", "gsheets"):
        return is_gsheets_metadata_tab_name(key)
    return False


def _catalog_rows(catalog: Sequence[CatalogRow]) -> List[CatalogRow]:
    return [
        {
            "api_name": (row.get("api_name") or row.get("entity_key") or "").strip(),
            "label": (row.get("label") or row.get("api_name") or row.get("entity_key") or "").strip(),
        }
        for row in catalog
        if (row.get("api_name") or row.get("entity_key"))
    ]


def prefer_user_data_entity_from_catalog(
    *,
    prompt: Optional[str],
    connector_type: str,
    catalog: Sequence[CatalogRow],
    current_key: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """
    When the user asked for vague "user data", pick a business user table/object
    instead of metadata (e.g. Salesforce RecordType).
    """
    if not is_vague_user_data_migration_prompt(prompt):
        return None
    rows = _catalog_rows(catalog)
    if not rows:
        return None

    ct = (connector_type or "").strip().lower()
    priorities = _SQL_USER_DATA_TABLE_PRIORITY if ct == "sql" else _SF_USER_DATA_OBJECT_PRIORITY

    api_by_lower = {r["api_name"].lower(): r for r in rows}
    for preferred in priorities:
        hit = api_by_lower.get(preferred.lower())
        if hit:
            return hit["api_name"], hit["label"] or hit["api_name"]

    for row in rows:
        api = row["api_name"]
        label = row["label"] or api
        if is_metadata_entity(api, connector_type=ct):
            continue
        combined = f"{api} {label}".lower()
        if any(token in combined for token in ("user", "customer", "contact", "member", "client")):
            if "record_type" in combined or "record type" in combined:
                continue
            return api, label

    return None


def is_user_data_priority_entity(entity_key: Optional[str], *, connector_type: str) -> bool:
    key = (entity_key or "").strip().lower()
    if not key:
        return False
    ct = (connector_type or "").strip().lower()
    priorities = _SQL_USER_DATA_TABLE_PRIORITY if ct == "sql" else _SF_USER_DATA_OBJECT_PRIORITY
    return key in {p.lower() for p in priorities}


def should_replace_metadata_driving_entity(
    *,
    prompt: Optional[str],
    connector_type: str,
    entity_key: Optional[str],
    semantics: Optional[Dict[str, Any]] = None,
) -> bool:
    if not entity_key or prompt_requests_record_type(prompt or ""):
        return False
    if not is_vague_user_data_migration_prompt(prompt):
        return False
    return is_metadata_entity(entity_key, connector_type=connector_type, semantics=semantics)
