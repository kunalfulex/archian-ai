"""Connector-aware catalog loading and phrase grounding for migration intent."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from services.labeling.catalog_helpers import (
    list_entities_for_connector,
    resolve_connector_instance_id,
)
from services.planning.schemas import normalize_connector_type

CatalogEntry = Dict[str, str]
SideRole = Literal["source", "destination"]


async def _semantics_catalog_entries(
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
    *,
    spreadsheet_id: Optional[str] = None,
) -> List[CatalogEntry]:
    rows = await list_entities_for_connector(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
        spreadsheet_id=spreadsheet_id,
    )
    entries: List[CatalogEntry] = []
    for row in rows:
        key = (row.get("entity_key") or "").strip()
        if not key:
            continue
        label = (row.get("label") or key).strip()
        entries.append({"entity_key": key, "api_name": key, "label": label})
    return entries


async def _structural_catalog_entries_sql(
    user_id: str,
    connector_id: str,
) -> List[CatalogEntry]:
    from services.mapping_plan.catalog_target_resolver import _load_sql_connector_tables

    entries: List[CatalogEntry] = []
    rows = await _load_sql_connector_tables(user_id, connector_id)
    for row in rows:
        name = (row.get("table_name") or "").strip()
        if not name:
            continue
        entries.append({"entity_key": name, "api_name": name, "label": name})
    return entries


async def _structural_catalog_entries_salesforce(
    user_id: str,
    org_id: str,
) -> List[CatalogEntry]:
    from services.planning.migration_intent import _load_salesforce_catalog_summary

    rows = await _load_salesforce_catalog_summary(user_id, org_id)
    return [
        {
            "entity_key": r["api_name"],
            "api_name": r["api_name"],
            "label": r.get("label") or r["api_name"],
        }
        for r in rows
        if r.get("api_name")
    ]


async def _structural_catalog_entries_gsheets(
    user_id: str,
    *,
    spreadsheet_name: Optional[str],
    spreadsheet_id: Optional[str],
    classification: Optional[Dict[str, Any]] = None,
    catalog_connection_id: Optional[str] = None,
) -> List[CatalogEntry]:
    from services.mapping_plan.catalog_target_resolver import _load_google_sheet_tabs

    entries: List[CatalogEntry] = []
    rows = await _load_google_sheet_tabs(
        user_id,
        spreadsheet_name=spreadsheet_name,
        spreadsheet_id=spreadsheet_id,
    )
    for row in rows:
        sid = (row.get("spreadsheet_id") or "").strip()
        tab = (row.get("sheet_name") or "").strip()
        if not sid or not tab:
            continue
        key = f"{sid}::{tab}"
        wb_name = (row.get("spreadsheet_name") or "").strip()
        label = wb_name if wb_name else tab
        if wb_name and tab and wb_name.lower() != tab.lower():
            label = f"{wb_name} ({tab})"
        entries.append({"entity_key": key, "api_name": key, "label": label})
    return entries


def scoped_side_text(
    parsed_goal: Optional[Dict[str, Any]],
    *,
    side: SideRole,
    phrase: str,
    cross_system: bool,
) -> str:
    """Text used for catalog matching — avoid full cross-connector prompt when scoped."""
    goal = parsed_goal or {}
    scoped = (
        (goal.get("source_phrase") or "").strip()
        if side == "source"
        else (goal.get("destination_phrase") or "").strip()
    )
    if scoped:
        return scoped
    if cross_system:
        return (phrase or "").strip()
    return (phrase or "").strip()


async def load_side_catalog_summary(
    user_id: str,
    classification: Dict[str, Any],
    *,
    role: SideRole,
) -> Tuple[List[CatalogEntry], str, Dict[str, Any]]:
    """
    Load the full structural catalog for source or destination side.

    All synced entities are returned for retrieval scoring. Semantics (category,
    synonyms, description) are joined at score time via ``fetch_semantics_map`` in
    ``retrieve_entity_shortlist`` — not used to limit which entities exist.
    """
    ct = normalize_connector_type(
        classification.get("source" if role == "source" else "destination") or ""
    )
    ctx: Dict[str, Any] = {"connector_type": ct, "role": role}
    entries: List[CatalogEntry] = []

    if ct == "salesforce":
        from utils.destination_connector_resolve import resolve_salesforce_token_record

        name_key = "source_name" if role == "source" else "destination_name"
        id_key = "source_connector_id" if role == "source" else "destination_connector_id"
        tr = await resolve_salesforce_token_record(
            user_id,
            destination_name=classification.get(name_key),
            token_id=classification.get(id_key),
        )
        org_id = tr.get("org_id") if tr else None
        ctx["org_id"] = org_id
        if org_id:
            await resolve_connector_instance_id(
                user_id, ct, ctx, classification, role=role
            )
            entries = await _structural_catalog_entries_salesforce(user_id, org_id)
        return entries, ct, ctx

    if ct == "sql":
        from utils.destination_connector_resolve import resolve_sql_connector_record

        name_key = "source_name" if role == "source" else "destination_name"
        tr = await resolve_sql_connector_record(
            user_id, destination_name=classification.get(name_key)
        )
        connector_id = str(tr.get("id")) if tr and tr.get("id") else None
        if role == "source":
            connector_id = (
                connector_id
                or (classification.get("source_connector_id") or "").strip()
                or None
            )
        else:
            connector_id = (
                connector_id
                or (classification.get("destination_connector_id") or "").strip()
                or None
            )
        ctx["connector_id"] = connector_id
        if connector_id:
            await resolve_connector_instance_id(
                user_id, ct, ctx, classification, role=role
            )
            entries = await _structural_catalog_entries_sql(user_id, connector_id)
        return entries, ct, ctx

    if ct == "google_sheets":
        sid_key = "source_spreadsheet_id" if role == "source" else "destination_spreadsheet_id"
        name_key = "source_name" if role == "source" else "destination_name"
        spreadsheet_id = (classification.get(sid_key) or "").strip() or None
        spreadsheet_name = (classification.get(name_key) or "").strip() or None
        ctx["spreadsheet_id"] = spreadsheet_id
        ctx["spreadsheet_name"] = spreadsheet_name
        inst_id = await resolve_connector_instance_id(
            user_id, ct, ctx, classification, role=role
        )
        if inst_id:
            ctx["connection_id"] = inst_id
        entries = await _structural_catalog_entries_gsheets(
            user_id,
            spreadsheet_name=spreadsheet_name,
            spreadsheet_id=spreadsheet_id,
            classification=classification,
            catalog_connection_id=(classification.get("google_sheets_connection_id") or "").strip()
            or (classification.get("source_connector_id") if role == "source" else None)
            or (classification.get("destination_connector_id") if role == "destination" else None),
        )
        return entries, ct, ctx

    return entries, ct, ctx


def label_for_catalog_key(catalog: List[CatalogEntry], entity_key: str) -> str:
    key_l = (entity_key or "").lower()
    for row in catalog:
        ek = (row.get("entity_key") or row.get("api_name") or "").strip()
        if ek.lower() == key_l:
            return row.get("label") or ek
    return entity_key


def resolve_phrase_to_catalog_key(
    phrase: str,
    catalog: List[CatalogEntry],
    connector_type: str,
    *,
    scoped_text: str,
    hints: List[str],
    cross_system: bool = False,
    allow_prompt_fallback: bool = True,
) -> Optional[Tuple[str, str]]:
    """Match a business phrase to a catalog entity_key + label."""
    from services.planning.migration_intent import (
        _normalize_entity_phrase,
        _resolve_phrase_to_key,
    )

    ct = normalize_connector_type(connector_type)
    if not phrase or not catalog:
        return None

    norm_catalog = [
        {"api_name": row["api_name"], "label": row.get("label") or row["api_name"]}
        for row in catalog
        if row.get("api_name")
    ]
    hint_list = [phrase] + [h for h in hints if h and h.lower() != phrase.lower()]

    if ct == "salesforce":
        matched = _resolve_phrase_to_key(
            phrase,
            norm_catalog,
            prompt=scoped_text or phrase,
            hints=hint_list,
        )
        if matched:
            return matched
        if not cross_system and allow_prompt_fallback and scoped_text != phrase:
            matched = _resolve_phrase_to_key(
                phrase,
                norm_catalog,
                prompt=scoped_text,
                hints=hint_list,
            )
            if matched:
                return matched
        return None

    from services.planning.entity_resolver import (
        _match_gsheets_catalog,
        _match_sql_catalog,
    )

    texts = [scoped_text or phrase]
    if not cross_system and allow_prompt_fallback:
        texts.append(phrase)

    if ct == "sql":
        sql_rows = [{"table_name": row["api_name"]} for row in catalog if row.get("api_name")]
        for text in texts:
            if not text:
                continue
            matched = _match_sql_catalog(text, sql_rows, hint_list)
            if matched:
                name, _ = matched
                return name, label_for_catalog_key(catalog, name)
        return _fuzzy_phrase_match(phrase, catalog)

    if ct == "google_sheets":
        sheet_rows = []
        for row in catalog:
            key = row.get("entity_key") or row.get("api_name") or ""
            if "::" not in key:
                continue
            sid, tab = key.split("::", 1)
            sheet_rows.append(
                {
                    "spreadsheet_id": sid,
                    "sheet_name": tab,
                    "spreadsheet_name": row.get("label") or tab,
                }
            )
        for text in texts:
            if not text:
                continue
            matched = _match_gsheets_catalog(text, sheet_rows, hint_list)
            if matched:
                entity_key, tab, label, _ = matched
                return entity_key, label or tab
        return _fuzzy_phrase_match(phrase, catalog)

    return None


def _fuzzy_phrase_match(
    phrase: str,
    catalog: List[CatalogEntry],
) -> Optional[Tuple[str, str]]:
    from services.planning.migration_intent import _normalize_entity_phrase

    norm = _normalize_entity_phrase(phrase)
    best: Optional[Tuple[str, str, int]] = None
    for row in catalog:
        key = (row.get("entity_key") or row.get("api_name") or "").strip()
        label = (row.get("label") or key).strip()
        if not key:
            continue
        for c in (
            key.lower(),
            label.lower(),
            _normalize_entity_phrase(label),
            _normalize_entity_phrase(key),
        ):
            if not c:
                continue
            if norm == c:
                return key, label
            if norm in c or c in norm:
                if len(c) < 5 and c in norm:
                    if not re.search(rf"\b{re.escape(c)}\b", norm):
                        continue
                if len(norm) < 5 and norm in c:
                    if not re.search(rf"\b{re.escape(norm)}\b", c):
                        continue
                score = len(c)
                if best is None or score > best[2]:
                    best = (key, label, score)
    if best:
        return best[0], best[1]
    return None


async def lookup_source_relationship(
    user_id: str,
    *,
    connector_type: str,
    context: Dict[str, Any],
    entity_a: str,
    entity_b: str,
) -> Optional[Tuple[str, str, str, str]]:
    """
    Return (child_key, parent_key, lookup_field_path, relationship_name) when
    entity_a and entity_b are related in the source catalog.
    """
    ct = normalize_connector_type(connector_type)
    if ct == "salesforce":
        from services.planning.migration_intent import _lookup_relationship_between

        org_id = context.get("org_id")
        return await _lookup_relationship_between(user_id, org_id, entity_a, entity_b)

    if ct == "sql":
        return await _lookup_sql_fk_between(
            user_id,
            context.get("connector_id"),
            entity_a,
            entity_b,
        )

    return None


async def _lookup_sql_fk_between(
    user_id: str,
    connector_id: Optional[str],
    entity_a: str,
    entity_b: str,
) -> Optional[Tuple[str, str, str, str]]:
    if not connector_id or not entity_a or not entity_b:
        return None
    from db.extracted_schema import _sql_schema_data_to_foreign_keys
    from services.mapping_plan.catalog_target_resolver import _load_sql_connector_tables

    tables = await _load_sql_connector_tables(user_id, str(connector_id))
    by_name = {(row.get("table_name") or "").strip().lower(): row for row in tables}

    def _fks_for(table: str) -> List[Dict[str, Any]]:
        row = by_name.get(table.lower())
        if not row:
            return []
        return _sql_schema_data_to_foreign_keys(row.get("schema_data"))

    a_l, b_l = entity_a.lower(), entity_b.lower()
    for child_name, row in by_name.items():
        if child_name not in (a_l, b_l):
            continue
        child = (row.get("table_name") or "").strip()
        for fk in _fks_for(child):
            parent = (fk.get("referenced_table") or "").strip()
            col = (fk.get("column_name") or "").strip()
            if not parent or not col:
                continue
            if parent.lower() in (a_l, b_l) and parent.lower() != child_name:
                return child, parent, col, col
    return None
