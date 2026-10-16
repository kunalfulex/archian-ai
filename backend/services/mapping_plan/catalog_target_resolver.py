"""
Resolve mapping/migration **destination** targets from synced connector catalogs.

Mirrors source selection (select_relevant_salesforce_object / select_relevant_tables):
  1. Validate classifier/plan hint against DB catalog (exact label / api_name / table / tab).
  2. Match prompt text against catalog labels / api names (no hardcoded object lists).
  3. If still missing → LLM picks from catalog only.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from db.connection import database
from utils.salesforce_mapping_target import lookup_salesforce_object_in_org


async def _load_salesforce_org_objects(user_id: Any, org_id: Optional[str]) -> List[Dict[str, Any]]:
    if not org_id:
        return []
    rows = await database.fetch_all(
        """
        SELECT o.object_api_name, o.label, o.category, o.category_description
        FROM salesforce_objects o
        WHERE o.user_id = :user_id AND o.org_id = :org_id
          AND EXISTS (
            SELECT 1 FROM salesforce_object_fields f
            WHERE f.user_id = o.user_id
              AND f.org_id = o.org_id
              AND f.object_api_name = o.object_api_name
          )
        ORDER BY o.label
        """,
        values={"user_id": str(user_id), "org_id": org_id},
    )
    return [dict(r) for r in rows]


async def _load_sql_connector_tables(user_id: Any, connector_id: Optional[str]) -> List[Dict[str, Any]]:
    if not connector_id:
        return []
    rows = await database.fetch_all(
        """
        SELECT table_name, schema_data, ai_categorization, connector_id
        FROM sql_extracted_schemas
        WHERE user_id = :user_id AND connector_id = :connector_id
        ORDER BY table_name
        """,
        values={"user_id": str(user_id), "connector_id": str(connector_id)},
    )
    from services.connector_metadata.empty_catalog_cleanup import sql_schema_storable_column_count

    out: List[Dict[str, Any]] = []
    for r in rows or []:
        row = dict(r)
        if sql_schema_storable_column_count(row.get("schema_data")) > 0:
            out.append(row)
    return out


async def _load_google_sheet_tabs(
    user_id: Any,
    *,
    spreadsheet_name: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT s.spreadsheet_id, s.spreadsheet_name, s.sheet_name, s.google_sheets_connection_id
        FROM gsheets_extracted_schemas s
        WHERE s.user_id = CAST(:user_id AS uuid)
          AND (
            (
              CAST(:spreadsheet_id AS varchar) IS NOT NULL
              AND s.spreadsheet_id = CAST(:spreadsheet_id AS varchar)
            )
            OR (
              CAST(:spreadsheet_name AS varchar) IS NOT NULL
              AND LOWER(TRIM(s.spreadsheet_name)) = LOWER(TRIM(CAST(:spreadsheet_name AS varchar)))
            )
          )
        ORDER BY s.sheet_name
        """,
        values={
            "user_id": str(user_id),
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": spreadsheet_name,
        },
    )
    return [dict(r) for r in rows]


def _lookup_sql_table_in_catalog(tables: List[Dict[str, Any]], hint: str) -> Optional[str]:
    h = (hint or "").strip().lower()
    if not h:
        return None
    for row in tables:
        name = (row.get("table_name") or "").strip()
        if name and name.lower() == h:
            return name
    return None


def _lookup_sheet_tab_in_catalog(sheets: List[Dict[str, Any]], hint: str) -> Optional[str]:
    h = (hint or "").strip().lower()
    if not h:
        return None
    for row in sheets:
        tab = (row.get("sheet_name") or "").strip()
        if tab and tab.lower() == h:
            return tab
    return None


def _match_prompt_to_salesforce_catalog_object(
    prompt: str,
    catalog: List[Dict[str, Any]],
) -> Optional[str]:
    """
    Find a catalog object whose label or api_name appears in the user prompt.

    Uses only synced catalog rows — no hardcoded standard-object keyword lists.
    """
    text = (prompt or "").strip()
    if not text or not catalog:
        return None
    has_dest_ctx = bool(re.search(r"\b(?:map|migrat|remap|to)\b", text, re.I))
    if not has_dest_ctx:
        return None

    candidates: List[Tuple[int, str]] = []
    for row in catalog:
        api = (row.get("object_api_name") or "").strip()
        label = (row.get("label") or "").strip()
        if not api:
            continue

        if api and re.search(rf"\b{re.escape(api)}\b", text, re.I):
            candidates.append((len(api) + 5, api))

        if label:
            label_pat = re.escape(label)
            if re.search(rf"\b{label_pat}\s+(?:object|oject)\b", text, re.I):
                candidates.append((len(label) + 20, api))
            elif " " in label and label.lower() in text.lower():
                candidates.append((len(label) + 10, api))
            elif re.search(rf"\b{label_pat}\b", text, re.I):
                candidates.append((len(label), api))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


async def resolve_salesforce_destination_from_catalog(
    *,
    user_id: Any,
    org_id: Optional[str],
    prompt: Optional[str],
    hint: Optional[str] = None,
    conversation_history: Optional[list] = None,
) -> Optional[str]:
    """Validate hint against salesforce_objects, else LLM-select destination object."""
    catalog = await _load_salesforce_org_objects(user_id, org_id)
    if not catalog:
        return None

    snap = (hint or "").strip()
    if snap and snap.lower() not in ("unknown",):
        meta = await lookup_salesforce_object_in_org(user_id, org_id, snap)
        if meta:
            return str(meta.get("object_api_name") or snap)

    instruction = (prompt or "").strip()
    if snap and snap.lower() not in ("unknown",):
        instruction = f"{instruction}\nDestination object mentioned: {snap}".strip()

    matched = _match_prompt_to_salesforce_catalog_object(instruction, catalog)
    if matched:
        meta = await lookup_salesforce_object_in_org(user_id, org_id, matched)
        if meta:
            return str(meta.get("object_api_name") or matched)

    if not instruction:
        return None

    from handlers.salesforce_handler import select_relevant_salesforce_object

    selected = await select_relevant_salesforce_object(
        instruction,
        catalog,
        conversation_history,
        org_id,
        selection_role="destination",
    )
    if selected:
        return str(selected.get("object_api_name") or "")
    return None


async def resolve_sql_destination_table_from_catalog(
    *,
    user_id: Any,
    connector_id: Optional[str],
    prompt: Optional[str],
    hint: Optional[str] = None,
    conversation_history: Optional[list] = None,
) -> Optional[str]:
    """Validate hint against sql_extracted_schemas, else LLM-select destination table."""
    tables = await _load_sql_connector_tables(user_id, connector_id)
    if not tables:
        return None

    snap = (hint or "").strip()
    if snap and snap.lower() not in ("unknown",):
        matched = _lookup_sql_table_in_catalog(tables, snap)
        if matched:
            return matched

    instruction = (prompt or "").strip()
    if snap and snap.lower() not in ("unknown",):
        instruction = f"{instruction}\nDestination table mentioned: {snap}".strip()
    if not instruction:
        return None

    from handlers.sql_handler import select_relevant_tables

    connector_data = {"tables": tables, "connector": {"id": connector_id, "user_id": str(user_id)}}
    selected = await select_relevant_tables(
        instruction,
        connector_data,
        conversation_history,
        selection_role="destination",
        user_id=str(user_id),
    )
    if selected:
        return str(selected[0])
    return None


async def resolve_google_sheets_destination_from_catalog(
    *,
    user_id: Any,
    spreadsheet_name: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
    prompt: Optional[str],
    hint: Optional[str] = None,
    conversation_history: Optional[list] = None,
) -> Optional[str]:
    """Validate hint against gsheets_extracted_schemas tabs, else LLM-select destination tab."""
    sheets = await _load_google_sheet_tabs(
        user_id,
        spreadsheet_name=spreadsheet_name,
        spreadsheet_id=spreadsheet_id,
    )
    if not sheets:
        return None

    snap = (hint or "").strip()
    if snap and snap.lower() not in ("unknown",):
        matched = _lookup_sheet_tab_in_catalog(sheets, snap)
        if matched:
            return matched

    instruction = (prompt or "").strip()
    if snap and snap.lower() not in ("unknown",):
        instruction = f"{instruction}\nDestination sheet tab mentioned: {snap}".strip()
    if not instruction:
        return None

    tab = await _select_relevant_google_sheet_tab(
        instruction,
        sheets,
        conversation_history,
        user_id=str(user_id),
        spreadsheet_id=spreadsheet_id,
    )
    return tab


async def _select_relevant_google_sheet_tab(
    prompt: str,
    sheets: List[Dict[str, Any]],
    conversation_history: Optional[list] = None,
    *,
    user_id: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
) -> Optional[str]:
    """LLM stage: pick destination tab within a workbook from gsheets_extracted_schemas."""
    from services.labeling.catalog_helpers import fetch_semantics_map, primary_from_semantics

    semantics_map: Dict[str, Any] = {}
    conn_id = None
    for row in sheets:
        conn_id = row.get("google_sheets_connection_id") or conn_id
    if user_id and conn_id:
        semantics_map = await fetch_semantics_map(
            user_id=str(user_id),
            connector_type="google_sheets",
            connector_instance_id=str(conn_id),
        )

    summaries = []
    for row in sheets:
        tab = (row.get("sheet_name") or "").strip()
        if not tab:
            continue
        sid = (row.get("spreadsheet_id") or spreadsheet_id or "").strip()
        entity_key = f"{sid}::{tab}" if sid and tab else tab
        sem = semantics_map.get(entity_key) if semantics_map else None
        primary = primary_from_semantics(sem)
        summaries.append(
            {
                "sheet_name": tab,
                "spreadsheet_name": row.get("spreadsheet_name"),
                "document_type": primary.get("category") or tab,
                "document_description": primary.get("description"),
            }
        )
    if not summaries:
        return None

    conversation_context = ""
    if conversation_history:
        lines = []
        for msg in conversation_history[-4:]:
            if not isinstance(msg, dict):
                continue
            text = msg.get("text") or msg.get("content") or ""
            if text:
                lines.append(text[:500])
        if lines:
            conversation_context = "\n".join(lines)

    selection_prompt = f"""
You are Archian AI. Select the Google Sheets **destination tab** for field mapping and migration.

The user wants data written **TO** this tab — not the source tab they extract from.

**User request:**
"{prompt.strip()}"

**Available tabs in this workbook (sheet_name must match exactly):**
{json.dumps(summaries, indent=2)}

{f"**Recent conversation:**{chr(10)}{conversation_context}" if conversation_context else ""}

Return strict JSON:
{{
  "sheet_name": "Exact sheet_name from the list above",
  "needs_clarification": false,
  "clarification_message": ""
}}

Rules:
- Pick the tab named as the migration/mapping **destination**.
- Do NOT pick the source workbook/tab the user extracts FROM.
- sheet_name must be copied exactly from the catalog list.
"""

    from services.architect_agent import async_openai_call
    from services.llm import tasks as llm_tasks
    from utils.openai_helpers import parse_openai_json_response

    try:
        response = await async_openai_call(
            None,
            task=llm_tasks.GOOGLE_SHEETS_MAPPING,
            messages=[{"role": "user", "content": selection_prompt}],
            max_tokens=300,
            temperature=0.1,
        )
        raw = (response.choices[0].message.content or "").strip()
        selection = parse_openai_json_response(raw)
    except Exception as exc:
        print(f"⚠️ LLM Google Sheets destination tab selection failed: {exc}")
        return None

    if selection.get("needs_clarification"):
        return None
    api = (selection.get("sheet_name") or "").strip()
    if not api:
        return None
    for row in summaries:
        if str(row.get("sheet_name") or "") == api:
            return api
    return None
