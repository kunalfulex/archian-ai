"""Structured extract contract for SQL query generation.

Binding + column selection define SELECT; user prompt is context for WHERE/LIMIT only.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.planning.extract_field_scope import build_extract_field_scope, strict_extract_field_prompt_block


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for v in values:
        s = (v or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _parse_sql_select_columns(query: str) -> List[str]:
    if not query:
        return []
    m = re.search(r"\bSELECT\s+(.*?)\s+FROM\b", query, re.I | re.DOTALL)
    if not m:
        return []
    chunk = m.group(1).strip()
    if chunk == "*":
        return ["*"]
    cols: List[str] = []
    for part in chunk.split(","):
        token = part.strip()
        if not token:
            continue
        if " AS " in token.upper():
            token = re.split(r"\s+AS\s+", token, flags=re.I)[-1].strip()
        if "." in token:
            token = token.split(".")[-1].strip()
        token = token.strip('"').strip("'").strip("`")
        if token:
            cols.append(token)
    return cols


def _rebuild_sql_select(query: str, columns: Sequence[str]) -> str:
    if not query or not columns:
        return query
    quoted = ", ".join(f'"{c}"' if re.search(r"[^a-zA-Z0-9_]", c) else c for c in columns)
    return re.sub(
        r"\bSELECT\s+.*?\s+FROM\b",
        f"SELECT {quoted} FROM",
        query,
        count=1,
        flags=re.I | re.DOTALL,
    )


def build_sql_extract_contract(
    table_result: Dict[str, Any],
    *,
    extract_field_scope: Optional[Dict[str, Any]] = None,
    migration_intent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build authoritative SQL extract contract from planning + column selection."""
    field_scope = dict(extract_field_scope or table_result.get("extract_field_scope") or {})
    if migration_intent and isinstance(migration_intent, dict):
        merged = build_extract_field_scope(migration_intent)
        for key, val in merged.items():
            if key not in field_scope or not field_scope.get(key):
                field_scope[key] = val

    primary_table = (table_result.get("primary_table") or "").strip()
    table_names = _dedupe_preserve_order(table_result.get("table_names") or [primary_table])
    selected_columns = _dedupe_preserve_order(table_result.get("selected_columns") or [])
    column_metadata = table_result.get("column_metadata") or []
    fk_relationships = table_result.get("fk_relationships") or []

    allowed_by_table: Dict[str, List[str]] = {}
    for col in column_metadata:
        if not isinstance(col, dict):
            continue
        tbl = (col.get("table_name") or primary_table or "").strip()
        name = (col.get("column_name") or col.get("name") or "").strip()
        if tbl and name:
            allowed_by_table.setdefault(tbl, []).append(name)

    if not selected_columns and column_metadata:
        selected_columns = _dedupe_preserve_order(
            (c.get("column_name") or c.get("name") or "")
            for c in column_metadata
            if isinstance(c, dict)
        )

    return {
        "primary_table": primary_table or (table_names[0] if table_names else None),
        "table_names": table_names,
        "selected_columns": selected_columns,
        "allowed_select_columns": list(selected_columns),
        "allowed_by_table": allowed_by_table,
        "pinned_related_entity_keys": _dedupe_preserve_order(field_scope.get("parent_entity_keys") or []),
        "include_related_data": bool(field_scope.get("include_related_data")),
        "fk_relationships": fk_relationships,
        "field_scope": field_scope,
        "filters_hint": table_result.get("filters"),
    }


def sql_authority_prompt_block(contract: Dict[str, Any]) -> str:
    primary = contract.get("primary_table") or "Unknown"
    tables = contract.get("table_names") or []
    selected = contract.get("allowed_select_columns") or []
    filters = contract.get("filters_hint") or "None"
    pinned = contract.get("pinned_related_entity_keys") or []
    fks = contract.get("fk_relationships") or []
    scope = contract.get("field_scope") or {}
    strict_block = strict_extract_field_prompt_block(scope)
    fk_block = sql_fk_prompt_block(fks)
    join_note = ""
    if len(tables) > 1:
        join_note = (
            "- **Multiple tables:** Build explicit JOINs between allowed tables using FK relationships below.\n"
            f"- Anchor FROM table: {primary}\n"
        )

    return f"""
**EXTRACT CONTRACT (AUTHORITATIVE — overrides user text for tables and SELECT columns):**
- Primary table: {primary}
- Allowed tables: {json.dumps(tables)}
- SELECT columns — use ONLY these (no additions):
{json.dumps(selected, indent=2)}
- Suggested filters from field selection: {filters}
- Pinned related tables: {json.dumps(pinned)}
{join_note}{fk_block}**USER PROMPT ROLE (NON-AUTHORITATIVE for SELECT):**
User message is context ONLY for WHERE, LIMIT, ORDER BY, and JOIN structure. Do NOT add columns because the user mentioned them.
{strict_block}
"""


def enforce_sql_select_contract(
    query: str,
    allowed_select_columns: Sequence[str],
) -> Tuple[str, List[str], List[str]]:
    if not query or not allowed_select_columns:
        return query, _parse_sql_select_columns(query), []

    allowed_set = {c.strip().lower() for c in allowed_select_columns if c and str(c).strip()}
    if not allowed_set:
        return query, _parse_sql_select_columns(query), []

    parsed = _parse_sql_select_columns(query)
    if not parsed or parsed == ["*"]:
        cols = list(allowed_select_columns)
        return _rebuild_sql_select(query, cols), cols, []

    kept: List[str] = []
    removed: List[str] = []
    for col in parsed:
        if col.lower() in allowed_set:
            kept.append(col)
        else:
            removed.append(col)

    if not removed:
        return query, kept, removed
    if not kept:
        kept = list(allowed_select_columns)
    return _rebuild_sql_select(query, kept), kept, removed


def validate_sql_referenced_columns(
    referenced_columns: Sequence[str],
    table_schemas: Dict[str, Any],
    table_names: Sequence[str],
) -> Tuple[bool, List[str], List[str]]:
    available: Set[str] = set()
    for tbl in table_names:
        schema = table_schemas.get(tbl) or {}
        if isinstance(schema, dict):
            available.update(str(k) for k in schema.keys())
    invalid = [c for c in referenced_columns if (c or "").strip() and c not in available]
    return len(invalid) == 0, invalid, sorted(available)


def determine_sql_columns_from_scope(
    table_names: Sequence[str],
    table_schemas: Dict[str, Any],
    *,
    extract_field_scope: Optional[Dict[str, Any]] = None,
    migration_intent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pick authorized columns from bind scope + schema (no LLM)."""
    from services.planning.extract_field_clarification import _is_descriptive_column

    field_scope = dict(extract_field_scope or {})
    if migration_intent and isinstance(migration_intent, dict):
        merged = build_extract_field_scope(migration_intent)
        for key, val in merged.items():
            if key not in field_scope or not field_scope.get(key):
                field_scope[key] = val

    primary = (table_names[0] if table_names else "").strip()
    hints = _dedupe_preserve_order(field_scope.get("field_hints") or [])
    related_hints = _dedupe_preserve_order(field_scope.get("related_field_hints") or [])
    include_related = bool(field_scope.get("include_related_data"))
    scope = (field_scope.get("extract_field_scope") or field_scope.get("scope") or "unspecified").strip().lower()

    column_metadata: List[Dict[str, Any]] = []
    selected: List[str] = []

    for tbl in table_names:
        schema = table_schemas.get(tbl) or {}
        if not isinstance(schema, dict):
            continue
        col_names = list(schema.keys())
        is_primary = tbl == primary
        table_hints = hints if is_primary else (related_hints if include_related else [])
        if scope == "explicit" and table_hints:
            hint_lower = {h.lower() for h in table_hints}
            for col in col_names:
                if col.lower() in hint_lower or any(h in col.lower() for h in hint_lower):
                    selected.append(col)
                    column_metadata.append({"table_name": tbl, "column_name": col, "name": col})
        elif scope == "all_descriptive":
            for col in col_names:
                if _is_descriptive_column(col, connector_type="sql"):
                    selected.append(col)
                    column_metadata.append({"table_name": tbl, "column_name": col, "name": col})
        else:
            for col in col_names:
                if _is_descriptive_column(col, connector_type="sql"):
                    selected.append(col)
                    column_metadata.append({"table_name": tbl, "column_name": col, "name": col})

    selected = _dedupe_preserve_order(selected)
    if not selected and primary:
        schema = table_schemas.get(primary) or {}
        if isinstance(schema, dict):
            selected = list(schema.keys())[:40]
            column_metadata = [
                {"table_name": primary, "column_name": c, "name": c} for c in selected
            ]

    return {
        "primary_table": primary,
        "table_names": list(table_names),
        "selected_columns": selected,
        "column_metadata": column_metadata,
        "extract_field_scope": field_scope,
    }


def _table_names_from_connector(connector_tables: Sequence[Any]) -> Dict[str, str]:
    """Map lowercase table name -> canonical name from connector catalog."""
    out: Dict[str, str] = {}
    for table in connector_tables or []:
        if isinstance(table, dict):
            table_dict = table
        elif hasattr(table, "keys") and callable(getattr(table, "keys", None)):
            try:
                table_dict = dict(table)
            except (TypeError, ValueError):
                continue
        else:
            continue
        name = (table_dict.get("table_name") or "").strip()
        if name:
            out[name.lower()] = name
    return out


def filter_tables_to_catalog(
    table_names: Sequence[str],
    connector_data: Dict[str, Any],
) -> List[str]:
    """Keep only tables that exist on the connector; preserve plan order."""
    catalog = _table_names_from_connector(connector_data.get("tables") or [])
    out: List[str] = []
    for raw in table_names or []:
        key = (raw or "").strip()
        if not key:
            continue
        canonical = catalog.get(key.lower())
        if canonical and canonical not in out:
            out.append(canonical)
    return out


def resolve_sql_extract_tables(
    *,
    table_hint: Optional[str] = None,
    related_entity_keys: Optional[Sequence[str]] = None,
    migration_intent: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Resolve authoritative SQL tables from plan contract (no LLM).

    Anchor = source_entity_key / table_hint. Join partners come from FK-related
    migration_intent keys (driving, parent) and related_entity_keys.
    """
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    anchor = (table_hint or intent.get("driving_entity_key") or "").strip()
    if anchor.lower() in ("unknown",):
        anchor = ""

    tables: List[str] = []

    def _add(name: Optional[str]) -> None:
        n = (name or "").strip()
        if not n or n.lower() == "unknown":
            return
        if not any(n.lower() == t.lower() for t in tables):
            tables.append(n)

    if anchor:
        _add(anchor)

    if intent.get("include_related_data"):
        for key in (
            intent.get("driving_entity_key"),
            intent.get("parent_entity_key"),
            *(intent.get("join_entity_keys") or []),
            *(related_entity_keys or []),
        ):
            _add(key)
    else:
        for key in related_entity_keys or []:
            k = (key or "").strip()
            if k and anchor and k.lower() != anchor.lower():
                _add(k)

    if not tables and anchor:
        return [anchor]
    return tables


def _parse_schema_data_raw(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}


def collect_sql_fk_relationships(
    table_names: Sequence[str],
    connector_tables: Sequence[Any],
) -> List[Dict[str, Any]]:
    """Collect FK edges between selected tables from sql_extracted_schemas."""
    from db.extracted_schema import _sql_schema_data_to_foreign_keys

    allowed = {(t or "").strip().lower() for t in table_names if (t or "").strip()}
    if len(allowed) < 2:
        return []

    by_name = _table_names_from_connector(connector_tables)
    relationships: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for table in connector_tables or []:
        if isinstance(table, dict):
            table_dict = table
        elif hasattr(table, "keys") and callable(getattr(table, "keys", None)):
            try:
                table_dict = dict(table)
            except (TypeError, ValueError):
                continue
        else:
            continue
        name = (table_dict.get("table_name") or "").strip()
        if not name or name.lower() not in allowed:
            continue
        canonical = by_name.get(name.lower(), name)
        schema_data = _parse_schema_data_raw(table_dict.get("schema_data"))
        for fk in _sql_schema_data_to_foreign_keys(schema_data):
            parent = (fk.get("referenced_table") or "").strip()
            col = (fk.get("column_name") or "").strip()
            rcol = (fk.get("referenced_column") or "id").strip() or "id"
            if not parent or not col:
                continue
            if parent.lower() not in allowed:
                continue
            parent_canonical = by_name.get(parent.lower(), parent)
            edge_key = f"{canonical.lower()}:{col.lower()}:{parent_canonical.lower()}"
            if edge_key in seen:
                continue
            seen.add(edge_key)
            relationships.append(
                {
                    "child_table": canonical,
                    "parent_table": parent_canonical,
                    "column": col,
                    "referenced_column": rcol,
                }
            )
    return relationships


def sql_fk_prompt_block(fk_relationships: Sequence[Dict[str, Any]]) -> str:
    if not fk_relationships:
        return ""
    lines = []
    for fk in fk_relationships:
        if not isinstance(fk, dict):
            continue
        child = fk.get("child_table") or "?"
        parent = fk.get("parent_table") or "?"
        col = fk.get("column") or "?"
        rcol = fk.get("referenced_column") or "id"
        lines.append(f"  - {child}.{col} → {parent}.{rcol}")
    if not lines:
        return ""
    return (
        "**FK RELATIONSHIPS (use for JOIN ON — do not guess join keys):**\n"
        + "\n".join(lines)
        + "\n"
    )


def user_context_prompt_block(prompt: str, conversation_context: str = "") -> str:
    ctx = conversation_context.strip()
    conv_block = f"\n{ctx}\n" if ctx else ""
    return f"""
**User context (WHERE / LIMIT / ORDER BY / JOIN structure only — NOT for SELECT column selection):**
"{prompt}"
{conv_block}
"""
