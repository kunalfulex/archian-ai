"""
Create and manage reverse-leg migration_flows rows for bidirectional sync.
See docs/BIDIRECTIONAL_SYNC_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from db.connection import database
from utils.soql_parse import flatten_soql_select_item_to_extracted_key, sql_select_result_column_keys
from utils.soql_salesforce_field_validation import build_salesforce_mapping_select_items

from services.reverse_migration_rules_compiler import (
    ENV_ENABLE_REVERSE_RULES_COMPILER,
    ReverseMigrationRulesCompileResult,
    compile_reverse_child_migration_rules,
    reverse_migration_rules_compiler_enabled,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReverseLegRequest:
    parent_flow_id: str
    user_id: str
    parent_flow: Dict[str, Any]
    parent_schema: Dict[str, Any]
    parent_mappings: List[Dict[str, Any]]


@dataclass
class ReverseSourcePlan:
    source_type: str
    connector_id: str
    source_query: Optional[str]
    table_or_object: Optional[str]
    cursor_field: Optional[str]
    destination_type_for_child: str
    destination_connector_ref_for_child: str
    # Per-row extract keys aligned to parent field_mappings order (SOQL items or parsed SQL SELECT labels).
    source_keys_by_index: List[str]
    validation_notes: Dict[str, Any]


@dataclass
class ReverseDestinationPlan:
    destination_type: str
    connector_ref: str
    table_or_object: Optional[str]
    metadata: Dict[str, Any]


@dataclass
class ReverseMappingPlan:
    mappings: List[Dict[str, Any]]
    stats: Dict[str, Any]


@dataclass
class ReverseCompilerPlan:
    source: ReverseSourcePlan
    destination: ReverseDestinationPlan
    mapping: ReverseMappingPlan
    migration_rules_compile: Optional[ReverseMigrationRulesCompileResult] = None


def _salesforce_type_is_incremental_cursor(dtype: str) -> bool:
    t = (dtype or "").strip().lower()
    return t in {"date", "datetime"}


async def _resolve_salesforce_source_cursor_field(
    user_id: str,
    org_id: str,
    object_api_name: str,
    candidate: Optional[str],
) -> str:
    """
    Validate/normalize an incremental cursor for a Salesforce source object.
    Falls back to LastModifiedDate when candidate is missing/invalid/non-datetime.
    """
    obj = (object_api_name or "").strip()
    if not user_id or not org_id or not obj:
        return "LastModifiedDate"

    cand = (candidate or "").strip()
    if cand:
        row = await database.fetch_one(
            """
            SELECT field_api_name, data_type
            FROM salesforce_object_fields
            WHERE user_id = :uid AND org_id = :org
              AND object_api_name = :obj
              AND LOWER(field_api_name) = LOWER(:field)
            LIMIT 1
            """,
            {"uid": user_id, "org": org_id, "obj": obj, "field": cand},
        )
        if row:
            rd = dict(row)
            f_api = (rd.get("field_api_name") or "").strip()
            dtype = (rd.get("data_type") or "").strip()
            if f_api and _salesforce_type_is_incremental_cursor(dtype):
                return f_api

    # Prefer canonical LastModifiedDate if metadata is available for this object/org.
    lm = await database.fetch_one(
        """
        SELECT field_api_name
        FROM salesforce_object_fields
        WHERE user_id = :uid AND org_id = :org
          AND object_api_name = :obj
          AND LOWER(field_api_name) = LOWER('LastModifiedDate')
        LIMIT 1
        """,
        {"uid": user_id, "org": org_id, "obj": obj},
    )
    if lm:
        lmd = (dict(lm).get("field_api_name") or "").strip()
        if lmd:
            return lmd
    return "LastModifiedDate"


def _supports_bidirectional_pair(schema_type: str, dest_type: str) -> bool:
    """Bidirectional v1: Salesforce and SQL only (any mix); no file or Google Sheets."""
    st = (schema_type or "").lower()
    dt = (dest_type or "").lower()
    if st in ("file", "gsheets") or dt in ("file", "google_sheets"):
        return False
    return st in ("salesforce", "sql") and dt in ("salesforce", "sql")


async def _token_id_for_org(user_id: str, org_id: str) -> Optional[str]:
    row = await database.fetch_one(
        """
        SELECT id::text AS id
        FROM salesforce_tokens
        WHERE user_id = :user_id AND org_id = :org_id
        ORDER BY issued_at DESC
        LIMIT 1
        """,
        {"user_id": user_id, "org_id": org_id},
    )
    return row["id"] if row else None


def _build_sf_template_soql(object_api: str, field_rows: List[Dict[str, Any]], cursor_field: str) -> str:
    """Legacy flat SOQL builder (no lookup_of). Prefer _build_reverse_soql_from_mappings for reverse legs."""
    cols = {"Id"}
    for r in field_rows:
        sf = (r.get("salesforce_field") or "").strip()
        if sf:
            cols.add(sf)
    cur = (cursor_field or "LastModifiedDate").strip() or "LastModifiedDate"
    cols.add(cur)
    ordered = sorted(cols)
    return f"SELECT {', '.join(ordered)} FROM {object_api}"


def _root_salesforce_object_for_mappings(field_rows: List[Dict[str, Any]], fallback: str) -> str:
    """FROM object for SOQL: lookup_of base (e.g. Contact) or single salesforce_object across rows."""
    bases: Set[str] = set()
    for r in field_rows or []:
        lo = (r.get("lookup_of") or "").strip()
        if lo and "." in lo:
            bases.add(lo.split(".", 1)[0].strip())
    if len(bases) == 1:
        return next(iter(bases))
    objs = [
        (r.get("salesforce_object") or "").strip()
        for r in field_rows or []
        if (r.get("salesforce_object") or "").strip()
    ]
    if objs and all(o.lower() == objs[0].lower() for o in objs):
        return objs[0]
    return (fallback or "Contact").strip() or "Contact"


async def _build_reverse_soql_from_mappings(
    user_id: str,
    org_id: str,
    from_object: str,
    field_rows: List[Dict[str, Any]],
    cursor_field: str,
) -> str:
    """
    Build SOQL for reverse extract using the same rules as mapping save: direct fields + lookup_of paths.
    org_id must be the Salesforce org that will run the query (reverse extract source).

    For direct rows (no lookup_of), SOQL validation requires extracted_field_name to match the
    Salesforce field API name (same as _build_sql_to_sf_reverse_soql_and_source_keys); parent
    mappings often use extracted keys (e.g. from extraction rules) that differ from API names.
    """
    if not field_rows:
        raise ValueError("Cannot build reverse SOQL: flow has no field mappings.")
    from_obj = (from_object or "Contact").strip() or "Contact"
    query_rows: List[Dict[str, Any]] = []
    for r in field_rows:
        d = dict(r)
        lo = (d.get("lookup_of") or "").strip()
        sf_field = (d.get("salesforce_field") or "").strip()
        if not lo:
            d["extracted_field_name"] = sf_field
        query_rows.append(d)

    mappings = [
        SimpleNamespace(
            extracted_field_name=(r.get("extracted_field_name") or "").strip(),
            salesforce_object=(r.get("salesforce_object") or "").strip(),
            salesforce_field=(r.get("salesforce_field") or "").strip(),
            lookup_of=(r.get("lookup_of") or "").strip() or None,
        )
        for r in query_rows
    ]
    items, err = await build_salesforce_mapping_select_items(user_id, org_id, from_obj, mappings)
    if err:
        raise ValueError(
            f"Cannot build reverse SOQL from mappings (sync Salesforce metadata for this org if needed): {err}"
        )
    cur = (cursor_field or "LastModifiedDate").strip() or "LastModifiedDate"
    ordered: List[str] = []
    seen_lower: Set[str] = set()

    def add_col(c: str) -> None:
        t = (c or "").strip()
        if not t:
            return
        tl = t.lower()
        if tl not in seen_lower:
            seen_lower.add(tl)
            ordered.append(t)

    add_col("Id")
    for it in items:
        add_col(it)
    add_col(cur)
    return f"SELECT {', '.join(ordered)} FROM {from_obj}"


def _sql_select_column_expr(name: str) -> str:
    """
    Column (or simple identifier) fragment for generated SELECT lists.
    Unquoted so MySQL/MSSQL/Postgres accept typical API/column names; strip stray quotes from inputs.
    """
    return (name or "").strip().replace('"', "")


def _build_sql_template(table: str, field_rows: List[Dict[str, Any]], cursor_field: Optional[str]) -> str:
    cols: List[str] = []
    seen: Set[str] = set()

    for r in field_rows:
        c = (r.get("extracted_field_name") or "").strip()
        if c and c not in seen:
            seen.add(c)
            cols.append(_sql_select_column_expr(c))
    cur = (cursor_field or "").strip()
    if cur and cur not in seen:
        seen.add(cur)
        cols.append(_sql_select_column_expr(cur))
    if not cols:
        cols = ["*"]
    col_sql = ", ".join(cols)
    tn = (table or "unknown_table").strip() or "unknown_table"
    return f"SELECT {col_sql} FROM {tn}"


def _sql_table_from_mappings(field_rows: List[Dict[str, Any]], fallback: Optional[str]) -> str:
    for r in field_rows or []:
        t = (r.get("salesforce_object") or "").strip()
        if t:
            return t
    return (fallback or "unknown_table").strip() or "unknown_table"


def _sf_object_from_mappings(field_rows: List[Dict[str, Any]], fallback: Optional[str]) -> str:
    for r in field_rows or []:
        o = (r.get("salesforce_object") or "").strip()
        if o:
            return o
    return (fallback or "Contact").strip() or "Contact"


def _build_sql_template_from_dest_sql_columns(
    table: str, field_rows: List[Dict[str, Any]], cursor_field: Optional[str]
) -> str:
    """SQL SELECT columns from mapping salesforce_field (SQL column names when dest was SQL)."""
    synthetic = [{"extracted_field_name": (r.get("salesforce_field") or "").strip()} for r in field_rows or []]
    synthetic = [x for x in synthetic if x.get("extracted_field_name")]
    return _build_sql_template(table, synthetic, cursor_field)


async def _build_sql_to_sf_reverse_soql_and_source_keys(
    user_id: str,
    org_id: str,
    from_object: str,
    parent_field_rows: List[Dict[str, Any]],
    cursor_field: Optional[str],
) -> tuple[str, List[str]]:
    """
    Build reverse SOQL (SF extract source) from parent SQL→SF mappings and return expected
    extraction keys per mapping row index.

    For direct rows, SOQL validator requires extracted_field_name to match field API name, so this
    uses a query-only view of mappings where extracted_field_name mirrors salesforce_field.
    Lookup rows keep lookup_of semantics and produce relationship paths.
    """
    from_obj = (from_object or "Contact").strip() or "Contact"
    query_rows = []
    for r in parent_field_rows or []:
        d = dict(r)
        lo = (d.get("lookup_of") or "").strip()
        if not lo:
            d["extracted_field_name"] = (d.get("salesforce_field") or "").strip()
        query_rows.append(d)

    mappings = [
        SimpleNamespace(
            extracted_field_name=(r.get("extracted_field_name") or "").strip(),
            salesforce_object=(r.get("salesforce_object") or "").strip(),
            salesforce_field=(r.get("salesforce_field") or "").strip(),
            lookup_of=(r.get("lookup_of") or "").strip() or None,
        )
        for r in query_rows
    ]
    items, err = await build_salesforce_mapping_select_items(user_id, org_id, from_obj, mappings)
    if err:
        raise ValueError(
            f"Cannot build reverse SOQL from mappings (sync Salesforce metadata for this org if needed): {err}"
        )

    cur = (cursor_field or "LastModifiedDate").strip() or "LastModifiedDate"
    ordered: List[str] = []
    seen_lower: Set[str] = set()

    def add_col(c: str) -> None:
        t = (c or "").strip()
        if not t:
            return
        tl = t.lower()
        if tl not in seen_lower:
            seen_lower.add(tl)
            ordered.append(t)

    add_col("Id")
    for it in items:
        add_col(it)
    add_col(cur)
    sql = f"SELECT {', '.join(ordered)} FROM {from_obj}"
    src_keys = [flatten_soql_select_item_to_extracted_key(it) for it in items]
    return sql, src_keys


def _parse_migration_rules_dict(migration_rules: Any) -> Optional[Dict[str, Any]]:
    if migration_rules is None:
        return None
    if isinstance(migration_rules, dict):
        return migration_rules
    if isinstance(migration_rules, str):
        try:
            parsed = json.loads(migration_rules)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _transformation_fields_from_migration_rules(migration_rules: Any) -> List[Dict[str, Any]]:
    d = _parse_migration_rules_dict(migration_rules)
    if not d:
        return []
    fields_out: List[Dict[str, Any]] = []
    er = d.get("extraction_rules")
    if isinstance(er, dict):
        tr = er.get("transformation_rules")
        if isinstance(tr, dict):
            fields = tr.get("fields")
            if isinstance(fields, list):
                fields_out.extend(fields)
    try:
        from utils.migration_rules_utils import get_mapping_transformation

        mt = get_mapping_transformation(d)
        if isinstance(mt, dict):
            map_fields = mt.get("fields")
            if isinstance(map_fields, list):
                fields_out.extend(map_fields)
    except Exception:
        pass
    return fields_out


def _build_extraction_output_to_source_physical_map(migration_rules: Any) -> Dict[str, str]:
    """
    Map lower(output_field / post-transform key) -> physical source_field string from
    migration_rules transformation_rules (same semantics as file_handler.apply_transformations keep/rename).
    Used so SQL→SQL reverse-leg salesforce_field is a real table column, not the renamed extract key.
    """
    out: Dict[str, str] = {}
    for rule in _transformation_fields_from_migration_rules(migration_rules):
        if not isinstance(rule, dict):
            continue
        action = (rule.get("action") or "keep").lower()
        if action in ("exclude", "drop"):
            continue

        src_val = rule.get("source_field", "")
        if isinstance(src_val, list):
            src_vals = [str(x).strip() for x in src_val if str(x).strip()]
        else:
            s = str(src_val).strip()
            src_vals = [s] if s else []

        def add_out_key(output_key: str, physical: str) -> None:
            ok = (output_key or "").strip()
            ph = (physical or "").strip()
            if not ok or not ph:
                return
            lo = ok.lower()
            if lo not in out:
                out[lo] = ph

        if action.startswith("split_") or action == "split_address":
            physical = src_vals[0] if src_vals else ""
            outs = rule.get("output_fields") or []
            if isinstance(outs, list):
                for o in outs:
                    add_out_key(str(o), physical)
            continue

        if action in ("merge", "combine"):
            physical = src_vals[0] if src_vals else ""
            of = rule.get("output_field")
            if of is not None and str(of).strip():
                add_out_key(str(of).strip(), physical)
            continue

        if not src_vals:
            continue
        physical = src_vals[0]
        out_f = rule.get("output_field", physical)
        if isinstance(out_f, list):
            out_f = out_f[0] if out_f else physical
        out_f = str(out_f).strip() or physical
        add_out_key(out_f, physical)

    return out


def _sql_schema_data_to_columns_map(schema_data_raw: Any) -> Dict[str, Dict[str, Any]]:
    """
    Parse sql_extracted_schemas.schema_data into column_name -> metadata.
    Same shape as db.extracted_schema._sql_schema_data_to_columns (avoid importing that module: circular).
    """
    columns: Dict[str, Dict[str, Any]] = {}
    if schema_data_raw is None:
        return columns
    if isinstance(schema_data_raw, str):
        try:
            schema_data_raw = json.loads(schema_data_raw)
        except json.JSONDecodeError:
            return columns
    if isinstance(schema_data_raw, dict) and "columns" in schema_data_raw:
        for col in schema_data_raw.get("columns") or []:
            if isinstance(col, dict):
                col_name = col.get("column_name", "")
                if col_name:
                    columns[str(col_name)] = {"data_type": col.get("data_type", "VARCHAR")}
    elif isinstance(schema_data_raw, list):
        for col in schema_data_raw:
            if isinstance(col, dict):
                col_name = col.get("column_name", "")
                if col_name:
                    columns[str(col_name)] = {"data_type": col.get("data_type", "VARCHAR")}
    return columns


def _lower_to_canonical_sql_columns(schema_cols: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Map lower(column) -> canonical spelling from sql_extracted_schemas."""
    out: Dict[str, str] = {}
    for k in schema_cols:
        if not k:
            continue
        low = k.lower()
        if low not in out:
            out[low] = k
    return out


def _ordered_sql_dest_columns_from_mappings(field_rows: List[Dict[str, Any]]) -> List[str]:
    seen_lower: Set[str] = set()
    out: List[str] = []
    for r in field_rows or []:
        c = (r.get("salesforce_field") or "").strip()
        if not c:
            continue
        low = c.lower()
        if low in seen_lower:
            continue
        seen_lower.add(low)
        out.append(c)
    return out


def _reverse_sql_select_label(
    logical_alias: str,
    physical_col: str,
    seen_alias_lower: Set[str],
) -> str:
    """
    SELECT result label for one mapping row. Prefer logical (extract) alias; when multiple
    rows share the same logical key (split/combine), fall back to physical column name.
    """
    logical = (logical_alias or physical_col or "").strip()
    physical = (physical_col or logical).strip()
    if not logical and not physical:
        return ""
    if logical:
        low = logical.lower()
        if low not in seen_alias_lower:
            seen_alias_lower.add(low)
            return logical
    if physical:
        plow = physical.lower()
        if plow not in seen_alias_lower:
            seen_alias_lower.add(plow)
            return physical
    base = logical or physical
    n = 2
    while True:
        candidate = f"{base}_{n}"
        cl = candidate.lower()
        if cl not in seen_alias_lower:
            seen_alias_lower.add(cl)
            return candidate
        n += 1


def _ordered_sql_dest_table_columns_from_mappings(
    field_rows: List[Dict[str, Any]],
) -> List[Tuple[str, str, str]]:
    """
    Ordered projection list from mappings as tuples:
    (table_name, physical_column, extracted_alias). One entry per mapping row.
    """
    out: List[Tuple[str, str, str]] = []
    seen_phys: Set[str] = set()
    for r in field_rows or []:
        tbl = (r.get("salesforce_object") or "").strip()
        col = (r.get("salesforce_field") or "").strip()
        alias = (r.get("extracted_field_name") or "").strip() or col
        if not tbl or not col:
            continue
        k = f"{tbl.lower()}::{col.lower()}"
        if k in seen_phys:
            continue
        seen_phys.add(k)
        out.append((tbl, col, alias))
    return out


def _sql_reverse_keys_for_simple_field_rows(
    field_rows: List[Dict[str, Any]],
    lower_to_canon: Dict[str, str],
) -> List[str]:
    """Per-row SELECT labels for single-table reverse extract (no JOIN builder)."""
    seen: Set[str] = set()
    keys: List[str] = []
    for r in field_rows or []:
        col = (r.get("salesforce_field") or "").strip()
        logical = (r.get("extracted_field_name") or "").strip() or col
        canon = lower_to_canon.get(col.lower(), col) if col else logical
        keys.append(_reverse_sql_select_label(logical, canon, seen))
    return keys


def _sql_reverse_select_keys_for_field_rows(
    field_rows: List[Dict[str, Any]],
    table_meta: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Result column labels for each mapping row (same order as field_rows)."""
    seen: Set[str] = set()
    keys: List[str] = []
    for r in field_rows or []:
        tbl = (r.get("salesforce_object") or "").strip()
        col = (r.get("salesforce_field") or "").strip()
        logical = (r.get("extracted_field_name") or "").strip() or col
        if not tbl or not col or tbl not in table_meta:
            keys.append("")
            continue
        canon = table_meta[tbl]["col_map"].get(col.lower(), col)
        keys.append(_reverse_sql_select_label(logical, canon, seen))
    return keys


def _sql_schema_data_to_foreign_keys(schema_data_raw: Any) -> List[Dict[str, str]]:
    """
    Parse sql_extracted_schemas.schema_data foreign keys into normalized shape.
    """
    if schema_data_raw is None:
        return []
    raw = schema_data_raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, dict):
        return []
    fks = raw.get("foreign_keys")
    if not isinstance(fks, list):
        return []
    out: List[Dict[str, str]] = []
    for fk in fks:
        if not isinstance(fk, dict):
            continue
        col = (fk.get("column_name") or fk.get("column") or "").strip()
        ref_tbl = (fk.get("referenced_table") or fk.get("foreign_table_name") or "").strip()
        ref_col = (fk.get("referenced_column") or fk.get("foreign_column_name") or "").strip()
        if not col or not ref_tbl or not ref_col:
            continue
        out.append(
            {
                "column_name": col,
                "referenced_table": ref_tbl,
                "referenced_column": ref_col,
            }
        )
    return out


async def _build_sql_reverse_extract_join_query_from_mappings(
    user_id: str,
    connector_id: str,
    field_rows: List[Dict[str, Any]],
    table_hint: Optional[str],
    cursor_field: Optional[str],
    prefer_migration_flow_id: Optional[str],
) -> tuple[Optional[str], Optional[str], List[str]]:
    """
    Build reverse SQL extract query with lookup/FK-aware LEFT JOINs across mapped destination tables.
    Uses destination mapping table/column pairs (`salesforce_object`/`salesforce_field`).
    """
    mapped = _ordered_sql_dest_table_columns_from_mappings(field_rows)
    if not mapped:
        return None, None, []
    tables_in_order: List[str] = []
    for tbl, _, _ in mapped:
        if tbl not in tables_in_order:
            tables_in_order.append(tbl)
    if not tables_in_order:
        return None, None, []

    hint = (table_hint or "").strip()
    if hint and any(t.lower() == hint.lower() for t in tables_in_order):
        primary_table = next(t for t in tables_in_order if t.lower() == hint.lower())
    else:
        counts: Dict[str, int] = {}
        for t, _, _ in mapped:
            counts[t] = counts.get(t, 0) + 1
        primary_table = max(tables_in_order, key=lambda t: counts.get(t, 0))

    table_meta: Dict[str, Dict[str, Any]] = {}
    for t in tables_in_order:
        ses = await _fetch_sql_extracted_schema_row(
            user_id, connector_id, t, prefer_migration_flow_id=prefer_migration_flow_id
        )
        if not ses:
            continue
        actual_table = (str(ses.get("table_name") or "").strip() or t).strip() or t
        schema_data = ses.get("schema_data")
        col_map = _lower_to_canonical_sql_columns(_sql_schema_data_to_columns_map(schema_data))
        fks = _sql_schema_data_to_foreign_keys(schema_data)
        table_meta[t] = {
            "actual": actual_table,
            "col_map": col_map,
            "fks": fks,
        }

    if primary_table not in table_meta:
        return None, None, []

    joined: List[str] = [primary_table]
    joins: List[str] = []
    pending = [t for t in tables_in_order if t != primary_table and t in table_meta]

    while pending:
        progressed = False
        for candidate in list(pending):
            cmeta = table_meta[candidate]
            cond = None
            for jt in joined:
                jmeta = table_meta[jt]
                # candidate -> joined
                for fk in cmeta.get("fks", []):
                    if (fk.get("referenced_table") or "").strip().lower() == jt.lower():
                        c_col = cmeta["col_map"].get((fk.get("column_name") or "").strip().lower(), fk.get("column_name") or "")
                        j_col = jmeta["col_map"].get((fk.get("referenced_column") or "").strip().lower(), fk.get("referenced_column") or "")
                        if c_col and j_col:
                            cond = f"{cmeta['actual']}.{_sql_select_column_expr(c_col)} = {jmeta['actual']}.{_sql_select_column_expr(j_col)}"
                            break
                if cond:
                    break
                # joined -> candidate
                for fk in jmeta.get("fks", []):
                    if (fk.get("referenced_table") or "").strip().lower() == candidate.lower():
                        j_col = jmeta["col_map"].get((fk.get("column_name") or "").strip().lower(), fk.get("column_name") or "")
                        c_col = cmeta["col_map"].get((fk.get("referenced_column") or "").strip().lower(), fk.get("referenced_column") or "")
                        if c_col and j_col:
                            cond = f"{jmeta['actual']}.{_sql_select_column_expr(j_col)} = {cmeta['actual']}.{_sql_select_column_expr(c_col)}"
                            break
                if cond:
                    break
            if cond:
                joins.append(f"LEFT JOIN {cmeta['actual']} ON {cond}")
                joined.append(candidate)
                pending.remove(candidate)
                progressed = True
        if not progressed:
            break

    select_exprs: List[str] = []
    seen_alias: Set[str] = set()
    for t, col, alias in mapped:
        if t not in joined:
            continue
        meta = table_meta[t]
        canon_col = meta["col_map"].get(col.lower(), col)
        logical = (alias or canon_col or col).strip()
        out_alias = _reverse_sql_select_label(logical, canon_col, seen_alias)
        if not out_alias:
            continue
        base = f"{meta['actual']}.{_sql_select_column_expr(canon_col)}"
        if out_alias.lower() != canon_col.lower():
            select_exprs.append(f"{base} AS {_sql_select_column_expr(out_alias)}")
        else:
            select_exprs.append(base)

    cur = (cursor_field or "").strip()
    if cur and primary_table in table_meta:
        pmeta = table_meta[primary_table]
        ccur = pmeta["col_map"].get(cur.lower(), cur)
        if ccur and cur.lower() not in seen_alias:
            select_exprs.append(f"{pmeta['actual']}.{_sql_select_column_expr(ccur)}")

    if not select_exprs:
        return None, None, []

    from_clause = table_meta[primary_table]["actual"]
    join_sql = f" {' '.join(joins)}" if joins else ""
    sql = f"SELECT {', '.join(select_exprs)} FROM {from_clause}{join_sql}"
    keys_by_row = _sql_reverse_select_keys_for_field_rows(field_rows, table_meta)
    return sql, table_meta[primary_table]["actual"], keys_by_row


def _resolve_select_columns_with_schema(
    lower_to_canon: Dict[str, str],
    mapping_columns_ordered: List[str],
    cursor_field: Optional[str],
) -> List[str]:
    """Use sql_extracted_schemas canonical names; fall back to mapping spelling if unknown."""
    out: List[str] = []
    seen_lower: Set[str] = set()

    def add_col(name: str) -> None:
        t = (name or "").strip()
        if not t:
            return
        low = t.lower()
        if low in seen_lower:
            return
        canon = lower_to_canon.get(low, t)
        seen_lower.add(low)
        out.append(canon)

    for c in mapping_columns_ordered:
        add_col(c)
    if cursor_field:
        add_col(cursor_field.strip())
    return out


def _compose_sql_select(table: str, column_names: List[str]) -> str:
    """Build SELECT col1, col2, ... FROM table (unquoted identifiers; dedupe by lower)."""

    cols: List[str] = []
    seen: Set[str] = set()
    for c in column_names:
        t = (c or "").strip()
        if not t:
            continue
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        cols.append(_sql_select_column_expr(t))
    if not cols:
        cols = ["*"]
    col_sql = ", ".join(cols)
    tn = (table or "unknown_table").strip() or "unknown_table"
    return f"SELECT {col_sql} FROM {tn}"


async def _fetch_sql_extracted_schema_row(
    user_id: str,
    connector_id: str,
    table_name: str,
    prefer_migration_flow_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Load table_name + schema_data from sql_extracted_schemas for this connector/table.
    Prefer a row linked to prefer_migration_flow_id (parent flow), then newest extracted_at.
    """
    if not user_id or not connector_id or not (table_name or "").strip():
        return None
    fid = (prefer_migration_flow_id or "").strip()
    row = await database.fetch_one(
        """
        SELECT table_name, schema_data
        FROM sql_extracted_schemas
        WHERE user_id = CAST(:uid AS uuid)
          AND connector_id = CAST(:cid AS uuid)
          AND LOWER(TRIM(table_name::text)) = LOWER(TRIM(:tbl))
        ORDER BY (migration_flow_id = :fid) DESC NULLS LAST, extracted_at DESC NULLS LAST
        LIMIT 1
        """,
        {
            "uid": str(user_id).strip(),
            "cid": str(connector_id).strip(),
            "tbl": str(table_name).strip(),
            "fid": fid,
        },
    )
    return dict(row) if row else None


def _parse_sql_lookup_of_table_column(lookup_of: str) -> Optional[tuple[str, str]]:
    """Split SQL lookup_of 'table.column' (first dot only)."""
    s = (lookup_of or "").strip()
    if not s or "." not in s:
        return None
    base, col = s.split(".", 1)
    base, col = base.strip(), col.strip()
    if not base or not col:
        return None
    return base, col


def _build_sql_selected_field_to_table_map(sql_query: Optional[str]) -> Dict[str, str]:
    """
    Best-effort SQL lineage map:
    extracted/select field key (lower) -> source table name from SELECT ... FROM ... JOIN ...
    """
    q = (sql_query or "").strip()
    if not q:
        return {}
    try:
        from utils.soql_parse import split_select_and_suffix, raw_select_parts
    except Exception:
        return {}

    select_clause, suffix = split_select_and_suffix(q)
    if not select_clause or not suffix:
        return {}

    alias_to_table: Dict[str, str] = {}
    table_order: List[str] = []
    join_pat = re.compile(
        r"(?is)\b(?:from|join)\s+([a-zA-Z_][\w\.\"`]*)"
        r"(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))?"
    )
    reserved = {"on", "where", "group", "order", "limit", "left", "right", "inner", "outer", "full", "join"}

    for m in join_pat.finditer(suffix):
        tbl_raw = (m.group(1) or "").strip().replace('"', "").replace("`", "")
        if "." in tbl_raw:
            tbl_raw = tbl_raw.split(".")[-1]
        al = (m.group(2) or "").strip()
        if al.lower() in reserved:
            al = ""
        if not tbl_raw:
            continue
        if tbl_raw not in table_order:
            table_order.append(tbl_raw)
        alias_to_table[tbl_raw.lower()] = tbl_raw
        if al:
            alias_to_table[al.lower()] = tbl_raw

    out: Dict[str, str] = {}
    for item in raw_select_parts(select_clause):
        expr = item.strip()
        if not expr:
            continue

        alias_match = re.search(r"(?is)\s+AS\s+([a-zA-Z_][\w]*)\s*$", expr)
        if alias_match:
            out_key = alias_match.group(1).strip()
            expr_core = expr[: alias_match.start()].strip()
        else:
            tokens = expr.split()
            if len(tokens) >= 2 and re.match(r"^[a-zA-Z_][\w]*$", tokens[-1]) and "." in tokens[-2]:
                out_key = tokens[-1].strip()
                expr_core = " ".join(tokens[:-1]).strip()
            else:
                expr_core = expr
                out_key = ""

        expr_core = expr_core.strip().replace('"', "").replace("`", "")
        src_table = ""
        src_col = ""
        mcol = re.match(r"(?is)^([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)$", expr_core)
        if mcol:
            ref = mcol.group(1).strip().lower()
            src_col = mcol.group(2).strip()
            src_table = alias_to_table.get(ref, "")
        elif len(table_order) == 1 and re.match(r"^[a-zA-Z_][\w]*$", expr_core):
            src_col = expr_core
            src_table = table_order[0]
        else:
            continue

        if not src_table:
            continue
        key = (out_key or src_col).strip().lower()
        if key and key not in out:
            out[key] = src_table
        if src_col and src_col.strip().lower() not in out:
            out[src_col.strip().lower()] = src_table
    return out


def _pick_fk_local_column_for_referenced_table(
    fks: List[Dict[str, Any]], referenced_table: str, preferred_col: Optional[str] = None
) -> Optional[str]:
    """Given FK rows for one table, return local column_name whose referenced_table matches (case-insensitive)."""
    rt = (referenced_table or "").strip().lower()
    if not rt:
        return None
    matches: List[str] = []
    for fk in fks or []:
        if not isinstance(fk, dict):
            continue
        ref = (fk.get("referenced_table") or "").strip().lower()
        if ref != rt:
            continue
        c = (fk.get("column_name") or "").strip()
        if c:
            matches.append(c)
    if not matches:
        return None
    uniq: List[str] = []
    for c in matches:
        if c not in uniq:
            uniq.append(c)
    pref = (preferred_col or "").strip().lower()
    if pref:
        for c in uniq:
            if c.lower() == pref:
                return c
    if len(uniq) == 1:
        return uniq[0]
    id_ends = [c for c in uniq if c.endswith("Id")]
    if len(id_ends) == 1:
        return id_ends[0]
    return sorted(uniq, key=lambda x: (len(x), x))[0]


async def _realign_parent_sql_lookup_of_for_sql_sql_reverse(
    user_id: str,
    parent_flow_id: str,
    parent_src_tbl: str,
    parent_dest_tbl: str,
    parent_dest_conn: str,
    lookup_of_parent: str,
) -> Optional[str]:
    """
    Parent convention: lookup_of = '<parent_extract_table>.<fk_col>' (FK on extract table toward related rows).

    Reverse leg extracts from parent *destination* table, so relationship hints must be re-anchored to that table:
    '<parent_dest_table>.<fk_on_dest_referencing_parent_src>' using schema_data.foreign_keys with live fallback.
    """
    from uuid import UUID

    parsed = _parse_sql_lookup_of_table_column(lookup_of_parent)
    if not parsed:
        return None
    base_tbl, col_hint = parsed
    uid_raw = str(user_id).strip()
    ps = (parent_src_tbl or "").strip()
    pd = (parent_dest_tbl or "").strip()
    dc = (parent_dest_conn or "").strip()
    if not ps or not pd or not dc:
        return None
    try:
        uid = UUID(uid_raw)
    except (ValueError, TypeError):
        return None

    dest_row = await _fetch_sql_extracted_schema_row(uid_raw, dc, pd, str(parent_flow_id).strip())
    if not dest_row:
        return None
    dest_canon = (str(dest_row.get("table_name") or "").strip() or pd).strip() or pd
    dest_sd = dest_row.get("schema_data")

    from db import extracted_schema as ext_schema  # noqa: WPS433 — avoid import cycle at module load

    fks_dest = await ext_schema._sql_foreign_keys_with_runtime_fallback(
        uid, dc, dest_canon, dest_sd
    )
    dest_col_map = _lower_to_canonical_sql_columns(_sql_schema_data_to_columns_map(dest_sd))

    bl = base_tbl.lower()
    fk_on_dest: Optional[str] = None
    if bl == ps.lower():
        fk_on_dest = _pick_fk_local_column_for_referenced_table(fks_dest, ps, col_hint)
    elif bl == pd.lower() or bl == dest_canon.lower():
        hinted = (col_hint or "").strip().lower()
        for fk in fks_dest or []:
            if not isinstance(fk, dict):
                continue
            cl = (fk.get("column_name") or "").strip()
            ref = (fk.get("referenced_table") or "").strip().lower()
            if cl.lower() == hinted and ref == ps.lower():
                fk_on_dest = cl
                break
        if not fk_on_dest:
            fk_on_dest = _pick_fk_local_column_for_referenced_table(fks_dest, ps, col_hint)
    else:
        return None

    if not fk_on_dest:
        return None
    fk_canon = dest_col_map.get(fk_on_dest.lower(), fk_on_dest)
    return f"{dest_canon}.{fk_canon}"


async def _finalize_sql_sql_reverse_field_mappings(
    user_id: str,
    parent_flow_id: str,
    parent_sd: Dict[str, Any],
    parent_pd: Dict[str, Any],
    parent_fm_list: List[Dict[str, Any]],
    inverted: List[Dict[str, Any]],
    migration_rules: Any,
) -> None:
    """
    After generic reverse mapping invert for SQL→SQL reverse: fix asymmetric semantics.

    Parent rows pair (extracted_field_name = post-extract / logical key, salesforce_field = dest physical col).
    A blind swap puts the logical key into salesforce_field (INSERT column) and the dest physical name into
    extracted_field_name (row lookup key) — wrong when extraction rules rename DB columns to display keys.

    For each reverse row (aligned with parent by index):
    - salesforce_object := parent source table (reverse leg loads into parent extract table).
    - extracted_field_name := parent's extracted_field_name (logical key after extraction on reverse source).
    - salesforce_field := physical column on reverse destination (parent source): inverse of transformation
      output_field → source_field, then sql_extracted_schemas spelling on parent source connector.
    - lookup_of := re-anchored to parent destination table using FK metadata toward parent source when parent
      used SQL relationship hints (source_table.fk_column); cleared if FK metadata cannot resolve a reverse hint.
    """
    src_conn = str(parent_sd.get("connector_id") or "").strip()
    dest_conn = str(parent_pd.get("org_id") or "").strip()
    out_to_src = _build_extraction_output_to_source_physical_map(migration_rules)
    field_to_source_table = _build_sql_selected_field_to_table_map(parent_sd.get("sql_query"))

    dest_tbl = _sql_table_from_mappings(
        parent_fm_list,
        str(parent_sd.get("table_name") or "").strip() or None,
    )
    src_tbl = (str(parent_sd.get("table_name") or "").strip()) or dest_tbl

    # Cache canonical column spellings per potential reverse destination table.
    table_candidates: List[str] = [src_tbl]
    for t in field_to_source_table.values():
        if t and t not in table_candidates:
            table_candidates.append(t)
    table_to_canon: Dict[str, Dict[str, str]] = {}
    table_to_actual: Dict[str, str] = {}
    default_lookup_by_parent_table: Dict[str, str] = {}
    if src_conn:
        for tbl in table_candidates:
            srow = await _fetch_sql_extracted_schema_row(
                str(user_id).strip(), src_conn, tbl, str(parent_flow_id).strip()
            )
            if not srow:
                continue
            schema_data = srow.get("schema_data")
            canon = _lower_to_canonical_sql_columns(_sql_schema_data_to_columns_map(srow.get("schema_data")))
            actual_tbl = (str(srow.get("table_name") or "").strip() or tbl).strip() or tbl
            table_to_canon[tbl.lower()] = canon
            table_to_actual[tbl.lower()] = actual_tbl

            # Build default lookup_of hint per referenced parent table:
            # parent_table -> "child_table.child_fk_column"
            for fk in _sql_schema_data_to_foreign_keys(schema_data):
                ref_tbl = (fk.get("referenced_table") or "").strip()
                child_col = (fk.get("column_name") or "").strip()
                if not ref_tbl or not child_col:
                    continue
                child_col_canon = canon.get(child_col.lower(), child_col)
                rk = ref_tbl.lower()
                if rk not in default_lookup_by_parent_table:
                    default_lookup_by_parent_table[rk] = f"{actual_tbl}.{child_col_canon}"

    n = min(len(inverted), len(parent_fm_list))
    for i in range(n):
        inv = inverted[i]
        pr = parent_fm_list[i]

        logical = (pr.get("extracted_field_name") or "").strip()
        parent_sf = (pr.get("salesforce_field") or "").strip()
        inferred_tbl = (
            field_to_source_table.get(logical.lower())
            or field_to_source_table.get(parent_sf.lower())
            or src_tbl
        )
        tbl_key = inferred_tbl.lower()
        reverse_dest_object = table_to_actual.get(tbl_key, inferred_tbl)
        src_canon = table_to_canon.get(tbl_key, {})

        inv["salesforce_object"] = reverse_dest_object

        inv["extracted_field_name"] = logical

        low = logical.lower()
        phys = out_to_src.get(low)
        if not phys:
            phys = src_canon.get(low)
        if not phys:
            phys = src_canon.get(parent_sf.lower(), parent_sf)
        if not phys:
            phys = parent_sf
        if not phys:
            phys = (inv.get("salesforce_field") or "").strip()

        if src_canon and phys:
            inv["salesforce_field"] = src_canon.get(phys.lower(), phys)
        else:
            inv["salesforce_field"] = phys

        lo_parent = (pr.get("lookup_of") or "").strip()
        if lo_parent:
            new_lo = await _realign_parent_sql_lookup_of_for_sql_sql_reverse(
                user_id,
                parent_flow_id,
                reverse_dest_object,
                (pr.get("salesforce_object") or dest_tbl or "").strip() or dest_tbl,
                dest_conn,
                lo_parent,
            )
            if new_lo:
                inv["lookup_of"] = new_lo
            else:
                inv["lookup_of"] = default_lookup_by_parent_table.get(reverse_dest_object.lower())
        else:
            inv["lookup_of"] = default_lookup_by_parent_table.get(reverse_dest_object.lower())


def _trim_sql_select_keys_to_mapping_rows(
    keys: List[str],
    mapping_row_count: int,
    cursor_field: Optional[str],
) -> List[str]:
    """
    Child extract SQL may append an incremental cursor column not represented in field_mappings.
    Drop trailing keys that match the cursor when SELECT arity exceeds mapping row count.
    """
    if not keys or mapping_row_count <= 0:
        return keys
    if len(keys) <= mapping_row_count:
        return keys
    cur_l = (cursor_field or "").strip().lower()
    out = list(keys)
    while len(out) > mapping_row_count and cur_l and out[-1].strip().lower() == cur_l:
        out.pop()
    if len(out) > mapping_row_count:
        out = out[:mapping_row_count]
    return out


def _sql_select_keys_for_mapping_row_count(
    sql_query: Optional[str],
    cursor_field: Optional[str],
    mapping_row_count: int,
) -> List[str]:
    """Ordered result labels from the compiled reverse SQL, trimmed to mapping width."""
    q = (sql_query or "").strip()
    if not q or mapping_row_count <= 0:
        return []
    keys = sql_select_result_column_keys(q)
    if not keys:
        return []
    return _trim_sql_select_keys_to_mapping_rows(keys, mapping_row_count, cursor_field)


async def _build_sql_reverse_extract_from_sql_extracted_schemas(
    user_id: str,
    connector_id: str,
    table_hint: str,
    field_rows: List[Dict[str, Any]],
    cursor_field: Optional[str],
    prefer_migration_flow_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """
    When reverse extract source is SQL, build SELECT using table + column spellings from
    sql_extracted_schemas (schema_data), with mapping salesforce_field ordering / inclusion.
    Returns (sql, resolved_table_name) or (None, None) if no cached schema — caller falls back.
    """
    tbl = (table_hint or "").strip()
    if not tbl:
        tbl = _sql_table_from_mappings(field_rows, None)
    if not tbl:
        return None, None
    ses = await _fetch_sql_extracted_schema_row(
        user_id, connector_id, tbl, prefer_migration_flow_id=prefer_migration_flow_id
    )
    if not ses:
        return None, None
    actual_table = (str(ses.get("table_name") or "").strip() or tbl).strip() or tbl
    schema_cols = _sql_schema_data_to_columns_map(ses.get("schema_data"))
    if not schema_cols:
        return None, None
    lower_to_canon = _lower_to_canonical_sql_columns(schema_cols)
    ordered = _ordered_sql_dest_columns_from_mappings(field_rows)
    if not ordered:
        return None, None
    resolved = _resolve_select_columns_with_schema(lower_to_canon, ordered, cursor_field)
    if not resolved:
        return None, None
    return _compose_sql_select(actual_table, resolved), actual_table


async def _validate_compiler_generated_source_query(
    user_id: str,
    source_type: str,
    connector_id: str,
    source_query: Optional[str],
) -> Dict[str, Any]:
    """
    Reuse the same query structure + smoke-test path as mapping save endpoint.
    """
    notes: Dict[str, Any] = {"validated": False, "errors": []}
    sq = (source_query or "").strip()
    if not sq:
        notes["errors"].append("Generated reverse source query is empty.")
        return notes
    try:
        from utils.soql_parse import (
            validate_select_query_for_mapping,
            validate_soql_from_clause_for_mapping,
        )
        from db import extracted_schema as ext_schema  # local import to avoid module cycle

        s_type = (source_type or "").strip().lower()
        struct_err = validate_select_query_for_mapping(sq)
        if struct_err:
            notes["errors"].append(struct_err)
            return notes
        if s_type == "salesforce":
            from_err = validate_soql_from_clause_for_mapping(sq)
            if from_err:
                notes["errors"].append(from_err)
                return notes
            smoke = await ext_schema._soql_smoke_test(user_id, connector_id, sq)
        else:
            smoke = await ext_schema._sql_query_smoke_test(user_id, connector_id, sq)
        if smoke:
            mapped = ext_schema._map_smoke_error_to_query_validation(smoke)
            notes["errors"].append(mapped.get("message") or "Reverse query smoke test failed.")
            if mapped.get("technical"):
                notes["errors"].append(mapped["technical"])
            return notes
        notes["validated"] = True
        return notes
    except Exception as e:
        notes["errors"].append(f"Reverse query validation error: {e}")
        return notes


async def _load_reverse_leg_request(parent_flow_id: str, user_id: str) -> ReverseLegRequest:
    uid = str(user_id).strip()
    pid = str(parent_flow_id).strip()
    parent = await database.fetch_one(
        """
        SELECT id, user_id, org_id, migration_name, destination_type, migration_rules,
               bidirectional_sync_enabled, parent_flow_id, reverse_flow_id,
               incremental_sync_enabled, incremental_cursor_field
        FROM migration_flows
        WHERE id = :fid AND user_id = :uid
        """,
        {"fid": pid, "uid": uid},
    )
    if not parent:
        raise ValueError("Flow not found")
    pd = dict(parent)
    if pd.get("parent_flow_id"):
        raise ValueError("Cannot create a reverse leg from a reverse-leg flow; open the primary flow instead.")
    schema = await database.fetch_one(
        """
        SELECT extracted_schema_type, connector_id, sql_query, table_name, extracted_schema, user_prompt,
               document_type, file_key, openai_file_id, approved_by_user, destination_type
        FROM extracted_schemas
        WHERE migration_flow_id = :fid
        LIMIT 1
        """,
        {"fid": pid},
    )
    if not schema:
        raise ValueError("Flow has no extracted schema")
    fm_rows = await database.fetch_all(
        """
        SELECT extracted_field_name, salesforce_object, salesforce_field, lookup_of,
               is_duplicate_check, is_required
        FROM field_mappings
        WHERE migration_flow_id = :fid
        ORDER BY created_at
        """,
        {"fid": pid},
    )
    return ReverseLegRequest(
        parent_flow_id=pid,
        user_id=uid,
        parent_flow=pd,
        parent_schema=dict(schema),
        parent_mappings=[dict(x) for x in fm_rows or []],
    )


async def _generate_source_plan_sf_to_sf(request: ReverseLegRequest) -> ReverseSourcePlan:
    uid = request.user_id
    pd = request.parent_flow
    sd = request.parent_schema
    fm_list = request.parent_mappings
    table_name = sd.get("table_name")
    cursor_field = (pd.get("incremental_cursor_field") or "").strip() or None

    src_token = str(sd.get("connector_id") or "").strip()
    dest_org = str(pd.get("org_id") or "").strip()
    if not src_token or not dest_org:
        raise ValueError("Salesforce flow is missing source token or destination org.")
    src_row = await database.fetch_one(
        "SELECT org_id::text AS org_id FROM salesforce_tokens WHERE user_id = :uid AND id::text = :tid LIMIT 1",
        {"uid": uid, "tid": src_token},
    )
    if not src_row:
        raise ValueError("Source Salesforce token not found.")
    src_org = str(src_row["org_id"])
    new_connector = await _token_id_for_org(uid, dest_org)
    if not new_connector:
        raise ValueError("Could not resolve Salesforce token for destination org (reverse source).")
    from_obj = str(table_name or "Contact").strip() or "Contact"
    cur = await _resolve_salesforce_source_cursor_field(uid, dest_org, from_obj, cursor_field)
    soql = await _build_reverse_soql_from_mappings(uid, dest_org, from_obj, fm_list, cur)
    return ReverseSourcePlan(
        source_type="salesforce",
        connector_id=new_connector,
        source_query=soql,
        table_or_object=from_obj,
        cursor_field=cur,
        destination_type_for_child="salesforce",
        destination_connector_ref_for_child=src_org,
        source_keys_by_index=[],
        validation_notes={},
    )


async def _generate_source_plan_sql_to_sql(request: ReverseLegRequest) -> ReverseSourcePlan:
    uid = request.user_id
    pid = request.parent_flow_id
    pd = request.parent_flow
    sd = request.parent_schema
    fm_list = request.parent_mappings
    table_name = sd.get("table_name")
    cursor_field = (pd.get("incremental_cursor_field") or "").strip() or None

    src_conn = str(sd.get("connector_id") or "").strip()
    dest_conn = str(pd.get("org_id") or "").strip()
    if not src_conn or not dest_conn:
        raise ValueError("SQL flow is missing source or destination connector id.")
    sql_tbl = _sql_table_from_mappings(fm_list, str(table_name or "") if table_name else None)
    norm_cursor = cursor_field
    if norm_cursor:
        drec = await _fetch_sql_extracted_schema_row(uid, dest_conn, sql_tbl, pid)
        if drec:
            dmap = _lower_to_canonical_sql_columns(_sql_schema_data_to_columns_map(drec.get("schema_data")))
            if dmap:
                norm_cursor = dmap.get(norm_cursor.strip().lower(), norm_cursor)
    built_join_sql, join_tbl_out, join_keys = await _build_sql_reverse_extract_join_query_from_mappings(
        uid, dest_conn, fm_list, sql_tbl, norm_cursor, prefer_migration_flow_id=pid
    )
    built_sql, tbl_out = await _build_sql_reverse_extract_from_sql_extracted_schemas(
        uid, dest_conn, sql_tbl, fm_list, norm_cursor, prefer_migration_flow_id=pid
    )
    new_sql = built_join_sql or built_sql or _build_sql_template_from_dest_sql_columns(sql_tbl, fm_list, norm_cursor)
    resolved_tbl = join_tbl_out or tbl_out or sql_tbl
    return ReverseSourcePlan(
        source_type="sql",
        connector_id=dest_conn,
        source_query=new_sql,
        table_or_object=resolved_tbl,
        cursor_field=norm_cursor,
        destination_type_for_child="sql",
        destination_connector_ref_for_child=src_conn,
        source_keys_by_index=[],
        validation_notes={},
    )


async def _generate_source_plan_sf_to_sql(request: ReverseLegRequest) -> ReverseSourcePlan:
    uid = request.user_id
    pid = request.parent_flow_id
    pd = request.parent_flow
    sd = request.parent_schema
    fm_list = request.parent_mappings
    table_name = sd.get("table_name")
    cursor_field = (pd.get("incremental_cursor_field") or "").strip() or None

    src_token = str(sd.get("connector_id") or "").strip()
    dest_sql_conn = str(pd.get("org_id") or "").strip()
    if not src_token or not dest_sql_conn:
        raise ValueError("Flow is missing Salesforce source token or SQL destination connector id.")
    src_row = await database.fetch_one(
        "SELECT org_id::text AS org_id FROM salesforce_tokens WHERE user_id = :uid AND id::text = :tid LIMIT 1",
        {"uid": uid, "tid": src_token},
    )
    if not src_row:
        raise ValueError("Source Salesforce token not found.")
    src_org = str(src_row["org_id"])
    sql_tbl = _sql_table_from_mappings(fm_list, str(table_name or "") if table_name else None)
    built_join_sql, join_tbl_out, join_keys = await _build_sql_reverse_extract_join_query_from_mappings(
        uid, dest_sql_conn, fm_list, sql_tbl, cursor_field, prefer_migration_flow_id=pid
    )
    built_sql, tbl_out = await _build_sql_reverse_extract_from_sql_extracted_schemas(
        uid, dest_sql_conn, sql_tbl, fm_list, cursor_field, prefer_migration_flow_id=pid
    )
    new_sql = built_join_sql or built_sql or _build_sql_template_from_dest_sql_columns(sql_tbl, fm_list, cursor_field)
    resolved_tbl = join_tbl_out or tbl_out or sql_tbl
    sql_keys = join_keys if join_keys and any(k.strip() for k in join_keys) else []
    if not sql_keys:
        ses = await _fetch_sql_extracted_schema_row(uid, dest_sql_conn, resolved_tbl, pid)
        if ses:
            lower_to_canon = _lower_to_canonical_sql_columns(
                _sql_schema_data_to_columns_map(ses.get("schema_data"))
            )
            sql_keys = _sql_reverse_keys_for_simple_field_rows(fm_list, lower_to_canon)
    if not sql_keys:
        sql_keys = _sql_select_keys_for_mapping_row_count(new_sql, cursor_field, len(fm_list))
    return ReverseSourcePlan(
        source_type="sql",
        connector_id=dest_sql_conn,
        source_query=new_sql,
        table_or_object=resolved_tbl,
        cursor_field=cursor_field,
        destination_type_for_child="salesforce",
        destination_connector_ref_for_child=src_org,
        source_keys_by_index=sql_keys,
        validation_notes={},
    )


async def _generate_source_plan_sql_to_sf(request: ReverseLegRequest) -> ReverseSourcePlan:
    uid = request.user_id
    pd = request.parent_flow
    sd = request.parent_schema
    fm_list = request.parent_mappings
    cursor_field = (pd.get("incremental_cursor_field") or "").strip() or None

    src_sql_conn = str(sd.get("connector_id") or "").strip()
    dest_sf_org = str(pd.get("org_id") or "").strip()
    if not src_sql_conn or not dest_sf_org:
        raise ValueError("Flow is missing SQL source connector id or Salesforce destination org.")
    new_connector = await _token_id_for_org(uid, dest_sf_org)
    if not new_connector:
        raise ValueError("Could not resolve Salesforce token for destination org (reverse extract source).")
    from_obj = _root_salesforce_object_for_mappings(fm_list, "Contact")
    cur = await _resolve_salesforce_source_cursor_field(uid, dest_sf_org, from_obj, cursor_field)
    new_sql, source_keys = await _build_sql_to_sf_reverse_soql_and_source_keys(
        uid, dest_sf_org, from_obj, fm_list, cur
    )
    return ReverseSourcePlan(
        source_type="salesforce",
        connector_id=new_connector,
        source_query=new_sql,
        table_or_object=from_obj,
        cursor_field=cur,
        destination_type_for_child="sql",
        destination_connector_ref_for_child=src_sql_conn,
        source_keys_by_index=source_keys,
        validation_notes={},
    )


async def _generate_reverse_source_plan(request: ReverseLegRequest) -> ReverseSourcePlan:
    pd = request.parent_flow
    sd = request.parent_schema
    schema_type = (sd.get("extracted_schema_type") or "file").lower()
    dest_type = (pd.get("destination_type") or "salesforce").lower()
    if not _supports_bidirectional_pair(schema_type, dest_type):
        raise ValueError(
            "Bidirectional reverse leg is only supported for Salesforce and SQL flows "
            "(Salesforce↔Salesforce, SQL↔SQL, or Salesforce↔SQL)."
        )
    key = (schema_type, dest_type)
    generators: Dict[Tuple[str, str], Callable[[ReverseLegRequest], Awaitable[ReverseSourcePlan]]] = {
        ("salesforce", "salesforce"): _generate_source_plan_sf_to_sf,
        ("sql", "sql"): _generate_source_plan_sql_to_sql,
        ("salesforce", "sql"): _generate_source_plan_sf_to_sql,
        ("sql", "salesforce"): _generate_source_plan_sql_to_sf,
    }
    fn = generators.get(key)
    if not fn:
        raise ValueError("Unsupported bidirectional source/destination combination.")
    return await fn(request)


def _generate_reverse_destination_plan(source_plan: ReverseSourcePlan) -> ReverseDestinationPlan:
    return ReverseDestinationPlan(
        destination_type=source_plan.destination_type_for_child,
        connector_ref=source_plan.destination_connector_ref_for_child,
        table_or_object=source_plan.table_or_object,
        metadata={},
    )


async def _generate_reverse_mapping_plan(
    request: ReverseLegRequest,
    source_plan: ReverseSourcePlan,
) -> ReverseMappingPlan:
    from services import bidirectional_reverse_mapping as brm

    return await brm.generate_reverse_mapping_plan(request, source_plan)


def _reverse_leg_mapping_pairs_for_log(mappings: List[Dict[str, Any]], limit: int = 12) -> str:
    parts: List[str] = []
    for r in (mappings or [])[:limit]:
        ex = (r.get("extracted_field_name") or "").strip()
        obj = (r.get("salesforce_object") or "").strip()
        sf = (r.get("salesforce_field") or "").strip()
        parts.append(f"{ex!r} -> {obj}.{sf}" if obj else f"{ex!r} -> {sf!r}")
    if len(mappings or []) > limit:
        parts.append(f"... +{len(mappings) - limit} more")
    return "; ".join(parts) if parts else "(none)"


async def _compile_reverse_leg_plan(request: ReverseLegRequest) -> ReverseCompilerPlan:
    source_plan = await _generate_reverse_source_plan(request)
    source_plan.validation_notes = await _validate_compiler_generated_source_query(
        request.user_id,
        source_plan.source_type,
        source_plan.connector_id,
        source_plan.source_query,
    )
    if source_plan.validation_notes.get("errors"):
        err0 = source_plan.validation_notes["errors"][0]
        raise ValueError(err0)
    destination_plan = _generate_reverse_destination_plan(source_plan)
    mapping_plan = await _generate_reverse_mapping_plan(request, source_plan)
    mr_compile = compile_reverse_child_migration_rules(
        request.parent_flow.get("migration_rules"),
        request.parent_mappings,
        mapping_plan.mappings,
    )
    q = (source_plan.source_query or "").strip()
    q_preview = q if len(q) <= 280 else q[:277] + "..."
    keys = source_plan.source_keys_by_index or []
    logger.info(
        "[REVERSE LEG] compiled plan parent_flow_id=%s source_type=%s dest_type=%s "
        "source_query=%r extract_keys_by_row=%s mappings=%s",
        request.parent_flow_id,
        source_plan.source_type,
        destination_plan.destination_type,
        q_preview,
        keys,
        _reverse_leg_mapping_pairs_for_log(mapping_plan.mappings),
    )
    if mr_compile.warnings:
        logger.info(
            "[REVERSE LEG] migration_rules compiler warnings parent_flow_id=%s: %s",
            request.parent_flow_id,
            mr_compile.warnings,
        )
    return ReverseCompilerPlan(
        source=source_plan,
        destination=destination_plan,
        mapping=mapping_plan,
        migration_rules_compile=mr_compile,
    )


async def _persist_compiled_reverse_leg(
    request: ReverseLegRequest,
    compiled: ReverseCompilerPlan,
) -> Dict[str, Any]:
    uid = request.user_id
    pid = request.parent_flow_id
    pd = request.parent_flow
    sd = request.parent_schema
    old_reverse = pd.get("reverse_flow_id")
    if old_reverse:
        await delete_reverse_leg_only(str(old_reverse), uid, clear_parent_ref=False)
        await database.execute(
            "UPDATE migration_flows SET reverse_flow_id = NULL WHERE id = :pid AND user_id = :uid",
            {"pid": pid, "uid": uid},
        )

    child_id = str(uuid4())
    child_schema_id = str(uuid4())
    now = datetime.utcnow()
    base_name = (pd.get("migration_name") or "Flow").strip()[:200]
    child_name = f"{base_name} (reverse)"
    dup = await database.fetch_one(
        "SELECT id FROM migration_flows WHERE user_id = :uid AND LOWER(TRIM(migration_name)) = LOWER(:name) LIMIT 1",
        {"uid": uid, "name": child_name},
    )
    if dup:
        child_name = f"{child_name} {str(uuid4())[:8]}"

    mrc = compiled.migration_rules_compile
    if mrc and mrc.migration_rules is not None:
        migration_rules_json = json.dumps(mrc.migration_rules)
        logger.debug(
            "reverse_leg persist parent_flow_id=%s: migration_rules source=compiled stats=%s warnings=%d",
            pid,
            mrc.stats,
            len(mrc.warnings or []),
        )
    else:
        mr = pd.get("migration_rules")
        if mr is None:
            migration_rules_json = None
        elif isinstance(mr, str):
            migration_rules_json = mr
        else:
            migration_rules_json = json.dumps(mr)
        compile_meta = (
            {"stats": mrc.stats, "warning_count": len(mrc.warnings or [])} if mrc else None
        )
        logger.debug(
            "reverse_leg persist parent_flow_id=%s: migration_rules source=parent_copy compile_meta=%s",
            pid,
            compile_meta,
        )

    ext_raw = sd.get("extracted_schema")
    if isinstance(ext_raw, dict):
        extracted_schema_json = json.dumps(ext_raw)
    elif isinstance(ext_raw, str):
        extracted_schema_json = ext_raw
    else:
        extracted_schema_json = json.dumps(ext_raw) if ext_raw is not None else "{}"

    async with database.transaction():
        await database.execute(
            """
            INSERT INTO migration_flows (
                id, user_id, org_id, migration_name, destination_type, migration_rules,
                created_at, updated_at,
                schedule_enabled, schedule_frequency, schedule_config, next_run_at, last_run_at, schedule_timezone,
                parent_flow_id, bidirectional_sync_enabled, reverse_flow_id,
                incremental_sync_enabled, incremental_cursor_field
            ) VALUES (
                :id, :user_id, :org_id, :name, :destination_type, :migration_rules,
                :created_at, :updated_at,
                FALSE, NULL, NULL, NULL, NULL, NULL,
                :parent_flow_id, FALSE, NULL,
                TRUE, :cursor_field
            )
            """,
            {
                "id": child_id,
                "user_id": uid,
                "org_id": compiled.destination.connector_ref,
                "name": child_name,
                "destination_type": compiled.destination.destination_type,
                "migration_rules": migration_rules_json,
                "created_at": now,
                "updated_at": now,
                "parent_flow_id": pid,
                "cursor_field": compiled.source.cursor_field,
            },
        )
        await database.execute(
            """
            INSERT INTO extracted_schemas (
                id, user_id, document_type, file_key, openai_file_id, extracted_schema, user_prompt,
                approved_by_user, destination_type, created_at, migration_flow_id,
                extracted_schema_type, connector_id, sql_query, table_name
            ) VALUES (
                :id, :user_id, :doc_type, :file_key, :file_id, :schema, :user_prompt,
                :approved, :destination_type, :created_at, :flow_id,
                :extracted_schema_type, :connector_id, :sql_query, :table_name
            )
            """,
            {
                "id": child_schema_id,
                "user_id": uid,
                "doc_type": sd.get("document_type"),
                "file_key": sd.get("file_key"),
                "file_id": sd.get("openai_file_id"),
                "schema": extracted_schema_json,
                "user_prompt": sd.get("user_prompt"),
                "approved": True if sd.get("approved_by_user") is None else bool(sd.get("approved_by_user")),
                "destination_type": compiled.destination.destination_type,
                "created_at": now,
                "flow_id": child_id,
                "extracted_schema_type": compiled.source.source_type,
                "connector_id": compiled.source.connector_id,
                "sql_query": compiled.source.source_query,
                "table_name": compiled.source.table_or_object,
            },
        )
        for r in compiled.mapping.mappings:
            await database.execute(
                """
                INSERT INTO field_mappings (
                    id, migration_flow_id, extracted_field_name, salesforce_object, salesforce_field,
                    lookup_of, is_duplicate_check, is_required, created_at
                ) VALUES (
                    :id, :flow_id, :field_name, :object, :field, :lookup,
                    :is_duplicate_check, :is_required, :created_at
                )
                """,
                {
                    "id": str(uuid4()),
                    "flow_id": child_id,
                    "field_name": r.get("extracted_field_name"),
                    "object": r.get("salesforce_object"),
                    "field": r.get("salesforce_field"),
                    "lookup": r.get("lookup_of"),
                    "is_duplicate_check": bool(r.get("is_duplicate_check")),
                    "is_required": bool(r.get("is_required")),
                    "created_at": now,
                },
            )
        await database.execute(
            """
            UPDATE migration_flows
            SET reverse_flow_id = CAST(:cid AS uuid),
                incremental_sync_enabled = TRUE,
                updated_at = :now
            WHERE id = CAST(:pid AS uuid) AND user_id = :uid
            """,
            {"cid": child_id, "pid": pid, "uid": uid, "now": now},
        )

    return {
        "status": "success",
        "reverse_flow_id": child_id,
        "migration_name": child_name,
        "compiler_version": "v2",
        "compiler_stats": {
            "mapping_rows": compiled.mapping.stats.get("rows"),
            "lookup_rows": compiled.mapping.stats.get("lookup_rows"),
            "query_validated": compiled.source.validation_notes.get("validated", False),
            **(
                {
                    "migration_rules_compiler": (
                        compiled.migration_rules_compile.stats
                        if compiled.migration_rules_compile
                        else None
                    ),
                    "migration_rules_warnings": (
                        compiled.migration_rules_compile.warnings
                        if compiled.migration_rules_compile
                        else []
                    ),
                }
            ),
        },
    }


async def create_reverse_leg_flow(parent_flow_id: str, user_id: str) -> Dict[str, Any]:
    """
    Create (or replace) the reverse-leg child flow: compile source query, destination, and
    field mappings from the primary flow, then persist in one transaction.
    """
    logger.info(
        "[REVERSE LEG] create started parent_flow_id=%s user_id=%s (logs only on Recreate reverse leg, not on Run flow)",
        parent_flow_id,
        user_id,
    )
    request = await _load_reverse_leg_request(parent_flow_id, user_id)
    compiled = await _compile_reverse_leg_plan(request)
    result = await _persist_compiled_reverse_leg(request, compiled)
    mrc = compiled.migration_rules_compile
    persisted = "compiled_child" if (mrc and mrc.migration_rules is not None) else "parent_copy"
    env_raw = os.environ.get(ENV_ENABLE_REVERSE_RULES_COMPILER, "")
    # Unset is intentional default-ON; avoid logging "<unset>" which reads like "disabled".
    env_desc = (
        f"set to {env_raw!r} (compiler off only when value is 0/false/no/off)"
        if env_raw.strip()
        else "not set in process environment (compiler ON by default)"
    )
    compiler_active = reverse_migration_rules_compiler_enabled()
    logger.info(
        "[REVERSE LEG] completed parent_flow_id=%s reverse_flow_id=%s %s: %s "
        "compiler_active=%s migration_rules_persisted=%s migration_rules_compiler_stats=%s "
        "migration_rules_warnings_n=%d",
        parent_flow_id,
        result.get("reverse_flow_id"),
        ENV_ENABLE_REVERSE_RULES_COMPILER,
        env_desc,
        compiler_active,
        persisted,
        (result.get("compiler_stats") or {}).get("migration_rules_compiler"),
        len((result.get("compiler_stats") or {}).get("migration_rules_warnings") or []),
    )
    return result


async def delete_reverse_leg_only(flow_id: str, user_id: str, clear_parent_ref: bool = True) -> None:
    """Delete a reverse-leg flow row and its schema/mappings. Optionally clear parent.reverse_flow_id."""
    uid = str(user_id).strip()
    fid = str(flow_id).strip()
    row = await database.fetch_one(
        """
        SELECT id, parent_flow_id FROM migration_flows
        WHERE id = :fid AND user_id = :uid
        """,
        {"fid": fid, "uid": uid},
    )
    if not row:
        return
    parent_id = row["parent_flow_id"] if row else None
    await database.execute("DELETE FROM field_mappings WHERE migration_flow_id = :fid", {"fid": fid})
    await database.execute("DELETE FROM extracted_schemas WHERE migration_flow_id = :fid", {"fid": fid})
    await database.execute("DELETE FROM migration_flows WHERE id = :fid AND user_id = :uid", {"fid": fid, "uid": uid})
    if clear_parent_ref and parent_id:
        await database.execute(
            """
            UPDATE migration_flows
            SET reverse_flow_id = NULL, bidirectional_sync_enabled = FALSE, updated_at = NOW()
            WHERE id = :pid AND user_id = :uid
            """,
            {"pid": str(parent_id), "uid": uid},
        )


async def prepare_delete_flow_bidirectional(flow_id: str, user_id: str) -> None:
    """
    Before deleting a migration_flow row: if parent with reverse child, delete child first.
    If deleting child, clear parent.reverse_flow_id first (parent_flow_id FK may CASCADE on parent delete).
    """
    uid = str(user_id).strip()
    fid = str(flow_id).strip()
    row = await database.fetch_one(
        """
        SELECT id, reverse_flow_id, parent_flow_id
        FROM migration_flows
        WHERE id = :fid AND user_id = :uid
        """,
        {"fid": fid, "uid": uid},
    )
    if not row:
        return
    rd = dict(row)
    rev = rd.get("reverse_flow_id")
    par = rd.get("parent_flow_id")
    if rev:
        await delete_reverse_leg_only(str(rev), uid, clear_parent_ref=False)
        await database.execute(
            "UPDATE migration_flows SET reverse_flow_id = NULL WHERE id = :fid AND user_id = :uid",
            {"fid": fid, "uid": uid},
        )
    if par:
        await database.execute(
            """
            UPDATE migration_flows
            SET reverse_flow_id = NULL, updated_at = NOW()
            WHERE id = :pid AND user_id = :uid
            """,
            {"pid": str(par), "uid": uid},
        )
