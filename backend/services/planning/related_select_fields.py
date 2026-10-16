"""Resolve related_field_hints into authorized SELECT paths for extract contracts."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# Common user tokens → Salesforce API field names (parent object).
_SF_HINT_TO_API: Dict[str, List[str]] = {
    "name": ["Name"],
    "phone": ["Phone"],
    "website": ["Website"],
    "email": ["Email"],
    "industry": ["Industry"],
    "type": ["Type"],
    "title": ["Title"],
    "address": ["BillingStreet", "ShippingStreet", "MailingStreet"],
    "city": ["BillingCity", "MailingCity", "ShippingCity"],
    "state": ["BillingState", "MailingState", "ShippingState"],
    "country": ["BillingCountry", "MailingCountry", "ShippingCountry"],
    "zip": ["BillingPostalCode", "MailingPostalCode", "ShippingPostalCode"],
    "postal": ["BillingPostalCode", "MailingPostalCode", "ShippingPostalCode"],
}


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for v in values:
        s = (v or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _token_matches_sf_field(token: str, field_api: str, field_label: str = "") -> bool:
    tok = (token or "").strip().lower()
    if not tok:
        return False
    api_l = (field_api or "").strip().lower()
    label_l = (field_label or "").strip().lower()
    if tok in api_l or tok in label_l:
        return True
    for alias in _SF_HINT_TO_API.get(tok, []):
        if alias.lower() == api_l:
            return True
    return False


def _sf_parent_field_candidates(hints: Sequence[str]) -> List[str]:
    """Best-effort API names when catalog/DB is unavailable (sync contract build)."""
    out: List[str] = []
    for hint in hints:
        tok = (hint or "").strip().lower()
        if not tok:
            continue
        aliases = _SF_HINT_TO_API.get(tok, [])
        if aliases:
            out.extend(aliases)
        else:
            # Title-case single token as API guess (e.g. "rating" → "Rating")
            out.append(tok[:1].upper() + tok[1:] if tok else "")
    return _dedupe_preserve_order(out)


def resolve_sf_related_select_paths(
    migration_intent: Dict[str, Any],
    *,
    parent_fields: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[str], List[Dict[str, Any]], Dict[str, List[Dict[str, str]]]]:
    """
    Turn related_field_hints into SOQL relationship paths (e.g. Account.Name).

    Returns (paths, field_metadata_entries, lookup_relationships).
    """
    if not migration_intent.get("include_related_data"):
        return [], [], {}

    hints = [
        str(h).strip()
        for h in (migration_intent.get("related_field_hints") or [])
        if str(h).strip()
    ]
    if not hints:
        return [], [], {}

    rel_name = (
        (migration_intent.get("lookup_relationship_name") or "").strip()
        or (migration_intent.get("parent_entity_key") or "").strip()
        or (migration_intent.get("related_entity_key") or "").strip()
    )
    parent_obj = (
        (migration_intent.get("parent_entity_key") or "").strip()
        or (migration_intent.get("related_entity_key") or "").strip()
    )
    lookup_field = (migration_intent.get("lookup_field_path") or "").strip()
    if "." in lookup_field:
        lookup_field = lookup_field.split(".", 1)[-1].strip()

    if not rel_name:
        return [], [], {}

    matched_apis: List[str] = []
    if parent_fields:
        for row in parent_fields:
            api = (row.get("field_api_name") or row.get("api_name") or "").strip()
            label = (row.get("field_label") or row.get("label") or "").strip()
            if not api:
                continue
            if any(_token_matches_sf_field(h, api, label) for h in hints):
                matched_apis.append(api)
    else:
        matched_apis = _sf_parent_field_candidates(hints)

    matched_apis = _dedupe_preserve_order(matched_apis)
    paths = [f"{rel_name}.{api}" for api in matched_apis]

    metadata: List[Dict[str, Any]] = []
    for api in matched_apis:
        path = f"{rel_name}.{api}"
        metadata.append(
            {
                "api_name": path,
                "label": path,
                "is_relationship_field": True,
                "relationship_name": rel_name,
                "lookup_object": parent_obj or rel_name,
            }
        )

    lookup_relationships: Dict[str, List[Dict[str, str]]] = {}
    if parent_obj:
        entry: Dict[str, str] = {"relationship_name": rel_name}
        if lookup_field:
            entry["field_api_name"] = lookup_field
        lookup_relationships[parent_obj] = [entry]

    return paths, metadata, lookup_relationships


def merge_primary_and_related_fields(
    primary_fields: Sequence[str],
    related_paths: Sequence[str],
    *,
    ensure_id: bool = True,
    primary_object: Optional[str] = None,
) -> List[str]:
    """Merge primary + relationship SELECT fields; optionally prepend Id."""
    merged = _dedupe_preserve_order([*primary_fields, *related_paths])
    if not ensure_id or not primary_object:
        return merged
    if not any(f.lower() == "id" for f in merged):
        return ["Id", *merged]
    return merged


def resolve_sql_related_tables_from_intent(
    migration_intent: Dict[str, Any],
    classification: Dict[str, Any],
    *,
    primary_table: Optional[str] = None,
) -> List[str]:
    """Resolve JOIN table list from migration_intent + classification pins."""
    from services.planning.sql_extract_contract import resolve_sql_extract_tables

    anchor = (primary_table or "").strip()
    if not anchor:
        anchor = (
            (migration_intent.get("driving_entity_key") or "")
            or (classification.get("source_entity_key") or "")
            or (classification.get("source_table_name") or "")
        ).strip()

    related_keys = [
        str(k).strip()
        for k in (
            classification.get("related_entity_keys")
            or migration_intent.get("related_entity_keys")
            or []
        )
        if str(k).strip()
    ]
    return resolve_sql_extract_tables(
        table_hint=anchor or None,
        related_entity_keys=related_keys or None,
        migration_intent=migration_intent,
    )


def _token_matches_sql_column(token: str, column_name: str) -> bool:
    tok = (token or "").strip().lower()
    col = (column_name or "").strip().lower()
    if not tok or not col:
        return False
    return tok in col or col in tok


def resolve_sql_related_columns_from_intent(
    table_names: Sequence[str],
    migration_intent: Dict[str, Any],
    *,
    table_schemas: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Pick columns: field_hints on primary table, related_field_hints on joined tables.

    When table_schemas provided, match tokens against real columns; otherwise use hints as names.
    """
    tables = [t for t in table_names if (t or "").strip()]
    if not tables:
        return [], []

    primary = tables[0]
    primary_hints = _dedupe_preserve_order(migration_intent.get("field_hints") or [])
    related_hints = _dedupe_preserve_order(migration_intent.get("related_field_hints") or [])
    related_tables = tables[1:] if migration_intent.get("include_related_data") else []

    selected: List[str] = []
    column_metadata: List[Dict[str, Any]] = []

    def _add_column(tbl: str, col: str) -> None:
        col = (col or "").strip()
        if not col:
            return
        if col.lower() not in {c.lower() for c in selected}:
            selected.append(col)
        column_metadata.append({"table_name": tbl, "column_name": col, "name": col})

    schemas = table_schemas or {}
    primary_schema = schemas.get(primary) or {}

    if primary_hints:
        if primary_schema:
            for col in primary_schema.keys():
                if any(_token_matches_sql_column(h, col) for h in primary_hints):
                    _add_column(primary, col)
        else:
            for h in primary_hints:
                _add_column(primary, h)

    for tbl in related_tables:
        schema = schemas.get(tbl) or {}
        if related_hints:
            if schema:
                for col in schema.keys():
                    if any(_token_matches_sql_column(h, col) for h in related_hints):
                        _add_column(tbl, col)
            else:
                for h in related_hints:
                    _add_column(tbl, h)

    return selected, column_metadata


def format_related_extract_hints(
    *,
    primary_hints: Sequence[str],
    related_hints: Sequence[str],
    field_hints_strict: bool,
    include_related: bool,
    parent_label: Optional[str] = None,
) -> str:
    """Human-readable hint suffix for plan labels (primary vs related separated)."""
    if not field_hints_strict:
        return ""
    primary_part = ", ".join(str(h) for h in primary_hints if str(h).strip())
    related_part = ", ".join(str(h) for h in related_hints if str(h).strip())
    if include_related and parent_label and primary_part and related_part:
        return f" ({primary_part}; related {parent_label}: {related_part})"
    if include_related and parent_label and related_part:
        return f" (related {parent_label}: {related_part})"
    if primary_part:
        return f" ({primary_part})"
    return ""
