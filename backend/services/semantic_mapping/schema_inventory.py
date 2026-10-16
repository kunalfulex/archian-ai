"""Load per-entity schema columns and categorization from CMA caches (not DAA quality reports)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from db.connection import database
from services.enterprise_graph.connector_ref import assessment_connector_ref
from services.enterprise_graph.inventory import (
    SourceEntity,
    _entity_label,
    _parse_json,
    normalize_entity_name,
)
from services.enterprise_graph.models import GraphMember
from services.semantic_mapping.entity_filters import should_exclude_from_ega_modeling

_MAX_COLUMNS = 60

# DAA quality_report top-level keys that must never become warehouse/linker columns
_QUALITY_ARTIFACT_NAMES = frozenset(
    {
        "by_field",
        "percentages",
        "total_missing",
        "summary",
        "duplicates",
        "missing_values",
        "format_issues",
        "inconsistencies",
    }
)


@dataclass
class SchemaColumn:
    name: str
    data_type: str = "STRING"
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "nullable": self.nullable,
            "is_primary_key": self.is_primary_key,
            "is_foreign_key": self.is_foreign_key,
        }


@dataclass
class EntityCategorization:
    category: str = ""
    category_description: str = ""
    object_name: str = ""
    confidence_score: float = 0.0


@dataclass
class EntitySchemaRecord:
    """CMA-backed metadata for one assessable entity."""

    label: str
    categorization: EntityCategorization = field(default_factory=EntityCategorization)
    columns: List[SchemaColumn] = field(default_factory=list)


def _is_quality_artifact_column(name: str) -> bool:
    norm = (name or "").strip().lower()
    if not norm or norm in _QUALITY_ARTIFACT_NAMES:
        return True
    if norm.startswith("case_inconsistency"):
        return True
    if norm.startswith("fuzzy_duplicates"):
        return True
    if norm.startswith("invalid_dates") or norm.startswith("invalid_emails"):
        return True
    return False


def _normalize_column_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", (name or "").strip()).strip("_").lower()


def _parse_column_dict(c: Dict[str, Any]) -> Optional[SchemaColumn]:
    raw_name = c.get("column_name") or c.get("name") or c.get("field") or c.get("api_name")
    if not raw_name:
        return None
    name = _normalize_column_name(str(raw_name))
    if not name or _is_quality_artifact_column(name):
        return None
    dtype = str(c.get("data_type") or c.get("type") or "STRING").upper()
    return SchemaColumn(
        name=name,
        data_type=dtype,
        nullable=bool(c.get("is_nullable", c.get("nullable", True))),
        is_primary_key=bool(c.get("is_primary_key") or c.get("primary_key")),
        is_foreign_key=bool(c.get("is_foreign_key") or c.get("foreign_key")),
    )


def columns_from_schema_payload(payload: Any) -> List[SchemaColumn]:
    """Parse column list from sql schema_data, assessment schema_columns, or header_schema."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        if "columns" in payload and isinstance(payload["columns"], list):
            return columns_from_schema_payload(payload["columns"])
        # Google Sheets header_schema: { "ColA": {...}, ... }
        out: List[SchemaColumn] = []
        for key, meta in payload.items():
            if isinstance(meta, dict):
                col = _parse_column_dict({"name": key, **meta})
            else:
                col = _parse_column_dict({"name": key})
            if col:
                out.append(col)
        return _dedupe_columns(out)
    if not isinstance(payload, list):
        return []
    out: List[SchemaColumn] = []
    for item in payload:
        if isinstance(item, str):
            name = _normalize_column_name(item)
            if name and not _is_quality_artifact_column(name):
                out.append(SchemaColumn(name=name))
        elif isinstance(item, dict):
            col = _parse_column_dict(item)
            if col:
                out.append(col)
    return _dedupe_columns(out)


def _dedupe_columns(cols: List[SchemaColumn]) -> List[SchemaColumn]:
    seen: Set[str] = set()
    out: List[SchemaColumn] = []
    for c in cols:
        if c.name in seen:
            continue
        seen.add(c.name)
        out.append(c)
        if len(out) >= _MAX_COLUMNS:
            break
    return out


def _parse_ai_categorization(raw: Any) -> EntityCategorization:
    data = _parse_json(raw) if raw is not None else {}
    if not isinstance(data, dict):
        return EntityCategorization()
    category = str(
        data.get("document_type") or data.get("category") or data.get("object_name") or ""
    ).strip()
    desc = str(data.get("document_description") or data.get("category_description") or "").strip()
    object_name = str(data.get("object_name") or "").strip()
    try:
        conf = float(data.get("confidence_score") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return EntityCategorization(
        category=category,
        category_description=desc,
        object_name=object_name,
        confidence_score=conf,
    )


def _entity_keys_for_lookup(entity_id: str) -> Set[str]:
    """Match assessment entity_id to CMA table/object names."""
    keys: Set[str] = set()
    eid = (entity_id or "").strip()
    if not eid:
        return keys
    keys.add(eid)
    keys.add(eid.lower())
    if "." in eid:
        keys.add(eid.split(".")[-1])
    return {k.lower() for k in keys if k}


async def _load_sql_records(user_id: str, connector_id: str) -> Dict[str, EntitySchemaRecord]:
    rows = await database.fetch_all(
        """
        SELECT table_name, schema_data, ai_categorization
        FROM sql_extracted_schemas
        WHERE user_id = :user_id AND connector_id = :connector_id
          AND COALESCE(is_deleted, FALSE) = FALSE
        """,
        {"user_id": user_id, "connector_id": connector_id},
    )
    out: Dict[str, EntitySchemaRecord] = {}
    for row in rows or []:
        d = dict(row)
        table = str(d.get("table_name") or "").strip()
        if not table:
            continue
        schema = _parse_json(d.get("schema_data")) or {}
        cat = _parse_ai_categorization(d.get("ai_categorization"))
        cols = columns_from_schema_payload(schema)
        out[table.lower()] = EntitySchemaRecord(
            label=table,
            categorization=cat,
            columns=cols,
        )
    return out


async def _load_salesforce_records(user_id: str, org_id: str) -> Dict[str, EntitySchemaRecord]:
    rows = await database.fetch_all(
        """
        SELECT object_api_name, label, category, category_description
        FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
        """,
        {"user_id": user_id, "org_id": org_id},
    )
    field_rows = await database.fetch_all(
        """
        SELECT object_api_name, field_api_name, data_type, is_required
        FROM salesforce_object_fields
        WHERE user_id = :user_id AND org_id = :org_id
        ORDER BY object_api_name, field_api_name
        """,
        {"user_id": user_id, "org_id": org_id},
    )
    fields_by_object: Dict[str, List[SchemaColumn]] = {}
    for row in field_rows or []:
        d = dict(row)
        obj = str(d.get("object_api_name") or "").strip()
        fname = str(d.get("field_api_name") or "").strip()
        if not obj or not fname:
            continue
        is_required = d.get("is_required")
        is_nullable = not is_required if is_required is not None else True
        col = _parse_column_dict(
            {
                "name": fname,
                "data_type": d.get("data_type"),
                "is_nullable": is_nullable,
            }
        )
        if col:
            fields_by_object.setdefault(obj.lower(), []).append(col)

    out: Dict[str, EntitySchemaRecord] = {}
    for row in rows or []:
        d = dict(row)
        api = str(d.get("object_api_name") or "").strip()
        if not api:
            continue
        cat = EntityCategorization(
            category=str(d.get("category") or "").strip(),
            category_description=str(d.get("category_description") or "").strip(),
            object_name=api,
        )
        cols = _dedupe_columns(fields_by_object.get(api.lower(), []))
        out[api.lower()] = EntitySchemaRecord(
            label=str(d.get("label") or api),
            categorization=cat,
            columns=cols,
        )
    return out


async def _load_gsheets_records(user_id: str, connection_id: str) -> Dict[str, EntitySchemaRecord]:
    rows = await database.fetch_all(
        """
        SELECT spreadsheet_id, spreadsheet_name, sheet_name, header_schema, ai_categorization
        FROM gsheets_extracted_schemas
        WHERE user_id = :user_id AND google_sheets_connection_id = :conn_id
          AND COALESCE(is_deleted, FALSE) = FALSE
        """,
        {"user_id": user_id, "conn_id": connection_id},
    )
    out: Dict[str, EntitySchemaRecord] = {}
    for row in rows or []:
        d = dict(row)
        sheet = str(d.get("sheet_name") or "").strip()
        sid = str(d.get("spreadsheet_id") or "").strip()
        book = str(d.get("spreadsheet_name") or "").strip()
        if not sheet:
            continue
        entity_key = f"{sid}::{sheet}" if sid else (f"{book}::{sheet}" if book else sheet)
        header = _parse_json(d.get("header_schema")) or {}
        cat = _parse_ai_categorization(d.get("ai_categorization"))
        cols = columns_from_schema_payload(header)
        rec = EntitySchemaRecord(
            label=sheet if not book else f"{book} / {sheet}",
            categorization=cat,
            columns=cols,
        )
        out[entity_key.lower()] = rec
        if sid:
            out[f"{sid}::{sheet}".lower()] = rec
    return out


async def load_member_schema_index(
    user_id: str,
    member: GraphMember,
) -> Dict[str, EntitySchemaRecord]:
    """All CMA entities for a graph member keyed by lowercase lookup aliases."""
    ref = assessment_connector_ref(member.connector_type, member.connector_ref)
    ct = (member.connector_type or "").strip().lower()
    if ct == "sql":
        cid = str(ref.get("connector_id") or "")
        return await _load_sql_records(user_id, cid) if cid else {}
    if ct == "salesforce":
        org = str(ref.get("org_id") or "")
        return await _load_salesforce_records(user_id, org) if org else {}
    if ct == "google_sheets":
        cid = str(ref.get("google_sheets_connection_id") or ref.get("connection_id") or "")
        return await _load_gsheets_records(user_id, cid) if cid else {}
    return {}


def _lookup_record(index: Dict[str, EntitySchemaRecord], entity_id: str) -> Optional[EntitySchemaRecord]:
    for key in _entity_keys_for_lookup(entity_id):
        rec = index.get(key)
        if rec:
            return rec
    return None


def _columns_from_assessment_entity(ep: Dict[str, Any]) -> List[SchemaColumn]:
    """Fallback when CMA row missing: use assessment entity schema columns only."""
    return columns_from_schema_payload(ep.get("columns") or ep.get("schema_columns"))


async def build_schema_aware_inventory(
    user_id: str,
    members: List[GraphMember],
    member_results: List[Dict[str, Any]],
) -> List[SourceEntity]:
    """
    Build inventory from DAA entity list, enriched with CMA columns + categorization.
    Skips warehouse/lake members and metadata/system entities (semantics-first).
    """
    member_by_id = {m.id: m for m in members}
    schema_index_by_member: Dict[str, Dict[str, EntitySchemaRecord]] = {}

    from services.enterprise_graph.validation import connector_id_from_ref
    from services.labeling.catalog_helpers import fetch_semantics_map, primary_from_semantics

    semantics_by_member: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for m in members:
        role = (m.role or "").strip().lower()
        if role in ("warehouse", "lake"):
            continue
        schema_index_by_member[m.id] = await load_member_schema_index(user_id, m)
        inst = connector_id_from_ref(m.connector_type, m.connector_ref or {})
        if inst:
            semantics_by_member[m.id] = await fetch_semantics_map(
                user_id=user_id,
                connector_type=m.connector_type,
                connector_instance_id=inst,
            )
        else:
            semantics_by_member[m.id] = {}

    inventory: List[SourceEntity] = []

    for mr in member_results:
        if mr.get("error"):
            continue
        member = member_by_id.get(mr["member_id"])
        if not member or (member.role or "").strip().lower() in ("warehouse", "lake"):
            continue
        index = schema_index_by_member.get(member.id) or {}
        report = mr.get("report") or {}
        entities = _parse_json(report.get("entities")) or []
        if not isinstance(entities, list):
            continue

        for ep in entities:
            if not isinstance(ep, dict) or ep.get("error"):
                continue
            eid = str(ep.get("entity_id") or ep.get("label") or "")
            if not eid:
                continue
            meta = ep.get("meta") or {}
            record = _lookup_record(index, eid)
            label = record.label if record else _entity_label(eid, meta)
            norm = normalize_entity_name(label)

            cat = ""
            cat_desc = ""
            object_name = ""
            conf = 0.0
            if record:
                cat = record.categorization.category
                cat_desc = record.categorization.category_description
                object_name = record.categorization.object_name
                conf = record.categorization.confidence_score
            else:
                cat = str(ep.get("category") or meta.get("category") or "")
                cat_desc = str(ep.get("category_description") or meta.get("category_description") or "")

            sem = (semantics_by_member.get(member.id) or {}).get(eid)
            if should_exclude_from_ega_modeling(
                eid,
                connector_type=member.connector_type,
                semantics=sem,
            ):
                continue

            if sem:
                primary = primary_from_semantics(sem)
                sem_cat = (primary.get("category") or "").strip()
                sem_desc = (primary.get("description") or "").strip()
                if sem_cat and sem_cat.lower() not in ("unknown",):
                    cat = sem_cat
                    object_name = sem_cat
                if sem_desc:
                    cat_desc = sem_desc
                try:
                    conf = max(conf, float(primary.get("confidence") or 0))
                except (TypeError, ValueError):
                    pass

            link_norm = normalize_entity_name(object_name or cat or label)
            if link_norm:
                norm = link_norm

            cols = list(record.columns) if record else _columns_from_assessment_entity(ep)
            if not cols:
                cols = _columns_from_assessment_entity(ep)

            from services.enterprise_graph.inventory import _CRM_ALIASES  # noqa: PLC0415

            domain = _CRM_ALIASES.get(norm, "")
            tags = [domain] if domain else []
            if cat:
                tags.append(cat.lower())
            if sem and isinstance(sem.get("domains"), list):
                tags.extend(str(d).lower() for d in sem["domains"] if d)
            if sem and isinstance(sem.get("synonyms"), list):
                tags.extend(str(s).lower() for s in sem["synonyms"] if s)

            inventory.append(
                SourceEntity(
                    member_id=member.id,
                    connector_type=member.connector_type,
                    entity_id=eid,
                    label=label,
                    normalized_name=norm,
                    domain_tags=tags,
                    column_names=[c.name for c in cols],
                    schema_columns=[c.to_dict() for c in cols],
                    category=cat,
                    category_description=cat_desc,
                    object_name=object_name or norm,
                    categorization_confidence=conf,
                )
            )
    return inventory
