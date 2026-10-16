"""LLM prompts for entity semantics — one builder per connector type."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _sample_block(sample_data: Optional[List[Dict[str, Any]]]) -> str:
    if not sample_data:
        return ""
    return f"\nSample rows (analysis only, not stored):\n{json.dumps(sample_data[:3], indent=2, default=str)}\n"


def _shared_output_rules() -> str:
    return """
Shared rules:
- entity_class=metadata for infrastructure/catalog/registry — NOT business transactional data
- domains/processes/synonyms: short tags; multiple allowed
- descriptive_fields: only real column/header names from schema (max 20)
- Do not invent field mappings for migration
- Base conclusions on schema structure and sample data only
"""


def build_entity_labeling_prompt(
    *,
    connector_type: str,
    entity_key: str,
    entity_display_name: str,
    schema_summary: Dict[str, Any],
    sample_data: Optional[List[Dict[str, Any]]] = None,
) -> str:
    ct = (connector_type or "").strip().lower()
    if ct == "sql":
        return build_sql_entity_labeling_prompt(
            entity_key=entity_key,
            entity_display_name=entity_display_name,
            schema_summary=schema_summary,
            sample_data=sample_data,
        )
    if ct == "google_sheets":
        return build_gsheets_entity_labeling_prompt(
            entity_key=entity_key,
            entity_display_name=entity_display_name,
            schema_summary=schema_summary,
            sample_data=sample_data,
        )
    if ct == "salesforce":
        return build_salesforce_entity_labeling_prompt(
            entity_key=entity_key,
            entity_display_name=entity_display_name,
            schema_summary=schema_summary,
            sample_data=sample_data,
        )
    raise ValueError(f"Unsupported connector_type for labeling: {connector_type}")


def build_sql_entity_labeling_prompt(
    *,
    entity_key: str,
    entity_display_name: str,
    schema_summary: Dict[str, Any],
    sample_data: Optional[List[Dict[str, Any]]] = None,
) -> str:
    suggested = schema_summary.get("suggested_relationship_hints") or []
    return f"""You are Archian AI, a data catalog analyst. Label this SQL table for discovery and routing.

Entity key (exact table name): {entity_key}
Display name: {entity_display_name}

Structural schema (from sql_extracted_schemas.schema_data):
{json.dumps(schema_summary, indent=2, default=str)}
{_sample_block(sample_data)}
SQL rules:
- Tables like sql_extracted_schemas, extracted_schemas, sql_connectors, migration_flows, chat_*,
  connector_*, intent_*, plan_*, salesforce_object_fields, salesforce_tokens, canonical_entities
  are Edison SYSTEM CATALOG — set entity_class=metadata, category="System Catalog".
- Business tables (users, orders, customers): entity_class=business; synonyms users actually say.
- relationship_hints: COPY suggested_relationship_hints exactly when non-empty (from foreign_keys),
  including for entity_class=metadata — system tables still have real FKs.
  Each related_entity MUST be an exact table name from catalog_tables — never Salesforce API names
  (Account, User, Group, ParentRecordId, etc.).
- If suggested_relationship_hints is empty, set relationship_hints to [].
- Do NOT invent relationships not present in foreign_keys.

Return JSON only:
{{
  "primary": {{
    "category": "Customer Records",
    "description": "2-4 sentences. What this table contains and what it does NOT contain."
  }},
  "entity_class": "business|metadata|junction",
  "domains": ["finance", "operations"],
  "processes": ["billing"],
  "synonyms": ["user table", "customer records"],
  "descriptive_fields": ["email", "first_name"],
  "relationship_hints": {json.dumps(suggested if suggested else [], indent=2)},
  "confidence": 0.85
}}
{_shared_output_rules()}
"""


def build_salesforce_entity_labeling_prompt(
    *,
    entity_key: str,
    entity_display_name: str,
    schema_summary: Dict[str, Any],
    sample_data: Optional[List[Dict[str, Any]]] = None,
) -> str:
    suggested = schema_summary.get("suggested_relationship_hints") or []
    return f"""You are Archian AI, a data catalog analyst. Label this Salesforce object for discovery and routing.

Object API name (entity key): {entity_key}
Label: {entity_display_name}

Object schema (fields include is_lookup and lookup_object_api_name):
{json.dumps(schema_summary, indent=2, default=str)}
{_sample_block(sample_data)}
Salesforce rules:
- relationship_hints: COPY suggested_relationship_hints when non-empty (from lookup fields).
  related_entity MUST be lookup_object_api_name (API name), field MUST be field_api_name.
- Do NOT invent lookup targets not in the field list.
- Setup/metadata objects (RecordType, FieldDefinition, etc.): entity_class=metadata; still COPY
  suggested_relationship_hints when lookup fields exist.

Return JSON only:
{{
  "primary": {{
    "category": "Customer Contact Information",
    "description": "2-4 sentences."
  }},
  "entity_class": "business|metadata|junction",
  "domains": ["sales", "crm"],
  "processes": ["lead management"],
  "synonyms": ["contacts", "people"],
  "descriptive_fields": ["FirstName", "Email"],
  "relationship_hints": {json.dumps(suggested if suggested else [{"related_entity": "Account", "relationship": "lookup", "field": "AccountId"}], indent=2)},
  "confidence": 0.85
}}
{_shared_output_rules()}
"""


def build_gsheets_entity_labeling_prompt(
    *,
    entity_key: str,
    entity_display_name: str,
    schema_summary: Dict[str, Any],
    sample_data: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return f"""You are Archian AI, a data catalog analyst. Label this Google Sheets tab for discovery and routing.

Entity key (spreadsheet_id::sheet_name): {entity_key}
Display name: {entity_display_name}

Tab schema (headers with types; sibling tabs in same workbook when present):
{json.dumps(schema_summary, indent=2, default=str)}
{_sample_block(sample_data)}
Google Sheets rules:
- Registry/system tabs (user_google_spreadsheets, connector lists): entity_class=metadata, category="System Catalog".
- Business data tabs: entity_class=business with natural synonyms.
- relationship_hints: usually []. Sheets do not have database foreign keys.
  Only add a hint if sibling_tabs clearly shows another tab in the SAME workbook and a header
  is an ID referencing that tab. related_entity MUST be the sibling entity_key (spreadsheet_id::sheet_name).
- NEVER use Salesforce names (Account, ParentRecordId) or SQL table names unless they appear as headers.

Return JSON only:
{{
  "primary": {{
    "category": "Customer Contact List",
    "description": "2-4 sentences."
  }},
  "entity_class": "business|metadata|junction",
  "domains": ["sales"],
  "processes": ["data collection"],
  "synonyms": ["contact list", "customer sheet"],
  "descriptive_fields": ["First Name", "Email"],
  "relationship_hints": [],
  "confidence": 0.85
}}
{_shared_output_rules()}
"""
