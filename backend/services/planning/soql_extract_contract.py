"""Structured extract contract for Salesforce SOQL generation.

Binding + field selection define what may appear in SELECT; the user prompt
is context for WHERE / LIMIT / ORDER BY only.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.planning.extract_field_scope import (
    _parse_soql_select_fields,
    _rebuild_soql_select,
    build_extract_field_scope,
    strict_extract_field_prompt_block,
)


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for v in values:
        s = (v or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _relationship_prefix(field_path: str) -> Optional[str]:
    if "." not in field_path:
        return None
    return field_path.split(".", 1)[0]


def _normalize_lookup_key(key: str) -> str:
    k = (key or "").strip()
    if k.endswith("__r"):
        return k[:-3] + "__c"
    if k.endswith("Id") and len(k) > 2:
        return k[:-2]
    return k


def build_soql_extract_contract(
    object_result: Dict[str, Any],
    *,
    extract_field_scope: Optional[Dict[str, Any]] = None,
    migration_intent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build authoritative SOQL extract contract from planning + field selection."""
    field_scope = dict(extract_field_scope or object_result.get("extract_field_scope") or {})
    if migration_intent and isinstance(migration_intent, dict):
        merged = build_extract_field_scope(migration_intent)
        for key, val in merged.items():
            if key not in field_scope or not field_scope.get(key):
                field_scope[key] = val

    field_metadata = object_result.get("field_metadata") or []
    selected_fields = _dedupe_preserve_order(object_result.get("selected_fields") or [])
    lookup_relationships = object_result.get("lookup_relationships") or {}

    allowed_relationship_prefixes: List[str] = []
    for field in field_metadata:
        if not field.get("is_relationship_field"):
            continue
        api = (field.get("api_name") or "").strip()
        prefix = _relationship_prefix(api)
        if prefix and prefix not in allowed_relationship_prefixes:
            allowed_relationship_prefixes.append(prefix)

    pinned_related = _dedupe_preserve_order(field_scope.get("parent_entity_keys") or [])
    allowed_lookup_objects = {_normalize_lookup_key(k) for k in pinned_related}

    forbidden_relationship_prefixes: List[str] = []
    for lookup_obj, rels in (lookup_relationships or {}).items():
        if _normalize_lookup_key(lookup_obj) in allowed_lookup_objects:
            continue
        if not field_scope.get("include_related_data"):
            for rel in rels or []:
                name = (rel.get("relationship_name") or "").strip()
                if name and name not in forbidden_relationship_prefixes:
                    forbidden_relationship_prefixes.append(name)

    base_field_names = {
        (f.get("api_name") or "").strip()
        for f in field_metadata
        if f.get("api_name") and "." not in f.get("api_name", "")
    }
    base_field_names.add("Id")

    relationship_paths = {
        (f.get("api_name") or "").strip()
        for f in field_metadata
        if f.get("api_name") and "." in f.get("api_name", "")
    }

    allowed_where_paths = _dedupe_preserve_order(
        list(selected_fields) + sorted(base_field_names) + sorted(relationship_paths)
    )

    return {
        "primary_object": object_result.get("object_api_name"),
        "object_label": object_result.get("object_label"),
        "selected_fields": selected_fields,
        "allowed_select_fields": list(selected_fields),
        "allowed_where_field_paths": allowed_where_paths,
        "allowed_relationship_prefixes": allowed_relationship_prefixes,
        "forbidden_relationship_prefixes": forbidden_relationship_prefixes,
        "pinned_related_entity_keys": pinned_related,
        "include_related_data": bool(field_scope.get("include_related_data")),
        "filters_hint": object_result.get("filters"),
        "field_scope": field_scope,
        "lookup_relationships": lookup_relationships,
    }


def soql_authority_prompt_block(contract: Dict[str, Any]) -> str:
    """Prompt section: extract contract is authoritative; user text is context only."""
    selected = contract.get("allowed_select_fields") or []
    primary = contract.get("primary_object") or "Unknown"
    filters = contract.get("filters_hint") or "None"
    allowed_rels = contract.get("allowed_relationship_prefixes") or []
    forbidden_rels = contract.get("forbidden_relationship_prefixes") or []
    pinned = contract.get("pinned_related_entity_keys") or []

    rel_lines = ""
    if allowed_rels:
        rel_lines += f"- Allowed relationship prefixes in SELECT: {json.dumps(allowed_rels)}\n"
    if pinned:
        rel_lines += f"- Pinned related entities from plan: {json.dumps(pinned)}\n"
    if forbidden_rels:
        rel_lines += (
            f"- Do NOT use these relationship prefixes (not authorized by plan): "
            f"{json.dumps(forbidden_rels)}\n"
        )

    scope = contract.get("field_scope") or {}
    strict_block = strict_extract_field_prompt_block(scope)

    return f"""
**EXTRACT CONTRACT (AUTHORITATIVE — overrides user text for object, SELECT fields, and relationships):**
- Primary object: {primary}
- SELECT fields — use ONLY these exact API paths (no additions, no substitutions):
{json.dumps(selected, indent=2)}
- Suggested business filters from field selection: {filters}
{rel_lines}
**USER PROMPT ROLE (NON-AUTHORITATIVE for SELECT/relationships):**
The user message and conversation history below are context ONLY for WHERE clauses, LIMIT, ORDER BY,
and business filter phrasing. Do NOT add SELECT fields or relationship paths because the user mentioned
them (e.g. "contact" on Opportunity) unless that exact path appears in SELECT fields above.
{strict_block}
"""


def enforce_soql_select_contract(
    soql: str,
    allowed_select_fields: Sequence[str],
) -> Tuple[str, List[str], List[str]]:
    """
    Strip unauthorized fields from SOQL SELECT clause.

    Returns (updated_soql, kept_fields, removed_fields).
    """
    if not soql or not allowed_select_fields:
        return soql, _parse_soql_select_fields(soql), []

    allowed_set = {f.strip() for f in allowed_select_fields if f and str(f).strip()}
    select_fields = _parse_soql_select_fields(soql)
    if not select_fields:
        return soql, [], []

    kept: List[str] = []
    removed: List[str] = []
    for raw in select_fields:
        token = raw.strip()
        if not token or token.startswith("("):
            kept.append(raw)
            continue
        if token in allowed_set:
            kept.append(raw)
        else:
            removed.append(token)

    if not removed:
        return soql, kept, removed

    if not kept:
        return soql, kept, removed

    return _rebuild_soql_select(soql, kept), kept, removed


def validate_soql_referenced_fields(
    referenced_fields: Sequence[str],
    object_result: Dict[str, Any],
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate SOQL referenced fields against object field metadata.

    Supports base fields and relationship paths (e.g. Account.Name).
    Returns (valid, invalid_fields, available_fields).
    """
    field_metadata = object_result.get("field_metadata") or []
    available_set: Set[str] = set()
    relationship_prefixes: Set[str] = set()

    for f in field_metadata:
        api = (f.get("api_name") or "").strip()
        if not api:
            continue
        available_set.add(api)
        prefix = _relationship_prefix(api)
        if prefix:
            relationship_prefixes.add(prefix)

    available_set.add("Id")

    invalid: List[str] = []
    for field in referenced_fields:
        name = (field or "").strip()
        if not name:
            continue
        if name in available_set:
            continue
        prefix = _relationship_prefix(name)
        if prefix and prefix not in relationship_prefixes:
            invalid.append(name)
            continue
        if prefix:
            invalid.append(name)
            continue
        if name not in available_set:
            invalid.append(name)

    available_list = sorted(available_set)
    return len(invalid) == 0, invalid, available_list


def user_context_prompt_block(prompt: str, conversation_context: str = "") -> str:
    """Wrap user text as non-authoritative context for SOQL filters/limits."""
    ctx = conversation_context.strip()
    conv_block = f"\n{ctx}\n" if ctx else ""
    return f"""
**User context (WHERE / LIMIT / ORDER BY only — NOT for SELECT field selection):**
"{prompt}"
{conv_block}
"""
