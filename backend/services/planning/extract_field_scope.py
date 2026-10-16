"""Cross-system extract field scoping and catalog guardrails."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.planning.schemas import normalize_connector_type

# Base-object fields that are system references even when not flagged lookup in catalog.
_SYSTEM_REFERENCE_FIELDS = frozenset(
    {
        "Id",
        "OwnerId",
        "RecordTypeId",
        "CreatedById",
        "LastModifiedById",
        "CreatedDate",
        "LastModifiedDate",
        "SystemModstamp",
        "IsDeleted",
        "MasterRecordId",
        "ParentId",
    }
)

_ID_EXPLICIT_PATTERNS = (
    re.compile(r"\b(?:include|with|add)\s+(?:the\s+)?(?:salesforce\s+)?id\b", re.I),
    re.compile(r"\brecord\s+ids?\b", re.I),
    re.compile(r"\bobject\s+ids?\b", re.I),
)


def user_explicitly_requested_id(prompt: Optional[str]) -> bool:
    pl = (prompt or "").lower()
    if not pl:
        return False
    if any(p.search(pl) for p in _ID_EXPLICIT_PATTERNS):
        return True
    return any(k in pl for k in ("salesforce id", "record id", " include id", " with id"))


def infer_cross_system_from_extract_context(
    *,
    source_type: Optional[str] = None,
    destination: Optional[str] = None,
    source_name: Optional[str] = None,
    destination_name: Optional[str] = None,
) -> bool:
    src = normalize_connector_type(source_type or "")
    dest = normalize_connector_type(destination or "")
    if src and dest and src != dest:
        return True
    if src == dest == "salesforce":
        sname = (source_name or "").strip().lower()
        dname = (destination_name or "").strip().lower()
        if sname and dname and sname != dname:
            return True
    return False


def extract_portability_context(
    classification: Optional[Dict[str, Any]] = None,
    *,
    source_type: Optional[str] = None,
    destination: Optional[str] = None,
    source_name: Optional[str] = None,
    destination_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolved source/destination facts for extract prompts — not a planning policy flag.

    ``heterogeneous`` (alias ``cross_system`` in scope dicts) is true when portable-field
    guardrails should apply (different connector types or different SF org names).
    """
    cls = dict(classification or {})
    src = normalize_connector_type(source_type or cls.get("source") or "")
    dest = normalize_connector_type(destination or cls.get("destination") or "")
    sname = (source_name or cls.get("source_name") or "").strip()
    dname = (destination_name or cls.get("destination_name") or "").strip()
    heterogeneous = infer_cross_system_from_extract_context(
        source_type=src,
        destination=dest,
        source_name=sname,
        destination_name=dname,
    )
    return {
        "source_type": src or None,
        "destination_type": dest or None,
        "source_name": sname or None,
        "destination_name": dname or None,
        "heterogeneous": heterogeneous,
    }


def resolve_extract_field_scope(
    extract_field_scope: Optional[Dict[str, Any]],
    *,
    classification: Optional[Dict[str, Any]] = None,
    source_type: Optional[str] = None,
    destination: Optional[str] = None,
    source_name: Optional[str] = None,
    destination_name: Optional[str] = None,
    related_entity_keys: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    if isinstance(extract_field_scope, dict) and extract_field_scope:
        return dict(extract_field_scope)
    portability = extract_portability_context(
        classification,
        source_type=source_type,
        destination=destination,
        source_name=source_name,
        destination_name=destination_name,
    )
    return build_extract_field_scope(
        cross_system=bool(portability.get("heterogeneous")),
        include_related_data=bool(related_entity_keys),
        parent_entity_keys=related_entity_keys,
        portability_context=portability,
    )


def build_extract_field_scope(
    migration_intent: Optional[Dict[str, Any]] = None,
    *,
    cross_system: bool = False,
    include_related_data: bool = False,
    parent_entity_keys: Optional[Sequence[str]] = None,
    field_hints: Optional[Sequence[str]] = None,
    portability_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    portability = dict(portability_context or {})
    heterogeneous = bool(
        intent.get("cross_system")
        or portability.get("heterogeneous")
        or cross_system
    )
    parents: List[str] = []
    for key in (
        intent.get("parent_entity_key"),
        intent.get("related_entity_key"),
        *(intent.get("related_entity_keys") or []),
        *(parent_entity_keys or []),
    ):
        k = str(key or "").strip()
        if k and k not in parents:
            parents.append(k)
    scope: Dict[str, Any] = {
        "cross_system": heterogeneous,
        "heterogeneous": heterogeneous,
        "include_related_data": bool(intent.get("include_related_data", include_related_data)),
        "parent_entity_keys": parents,
        "field_hints": [str(h) for h in (intent.get("field_hints") or field_hints or []) if str(h).strip()],
        "field_hints_strict": bool(intent.get("field_hints_strict", False)),
        "extract_field_scope": intent.get("extract_field_scope") or "unspecified",
        "field_scope_confidence": float(intent.get("field_scope_confidence") or 0.0),
        "related_field_hints": [
            str(h) for h in (intent.get("related_field_hints") or []) if str(h).strip()
        ],
        "destination_entity_key": (intent.get("destination_entity_key") or "").strip() or None,
        "extract_shape": intent.get("extract_shape"),
        "relationship_status": intent.get("relationship_status"),
    }
    if portability:
        for key in ("source_type", "destination_type", "source_name", "destination_name"):
            val = portability.get(key)
            if val:
                scope[key] = val
    for key in ("lookup_field_path", "lookup_relationship_name", "parent_entity_label"):
        val = intent.get(key)
        if val is not None and val != "":
            scope[key] = val
    return scope


def cross_system_field_prompt_block(scope: Dict[str, Any]) -> str:
    if not scope.get("cross_system"):
        return ""
    parents = scope.get("parent_entity_keys") or []
    parent_note = (
        f"Allowed related objects for relationship fields: {', '.join(parents)}."
        if parents
        else "Do not include relationship fields unless the user explicitly asked for related descriptive data."
    )
    return f"""
**CROSS-SYSTEM MIGRATION FIELD RULES (CRITICAL):**
- Source and destination are different systems/orgs — extracted values must be portable descriptive data.
- **DO NOT** select Id, lookup foreign keys (*Id fields like AccountId, OwnerId), or reference identifiers.
- **DO NOT** select RecordTypeId — use RecordType.Name only when the user explicitly asks for record type.
- **ONLY** include relationship fields (ParentObject.Field) for descriptive parent attributes the user requested.
- {parent_note}
- Prefer Name, Email, Phone, and other business-meaningful descriptive fields on the base object.
- Lookup FK values from the source org will not work in the destination — omit them entirely.
"""


def _relationship_parent_key(field_api_name: str) -> Optional[str]:
    if "." not in field_api_name:
        return None
    return field_api_name.split(".", 1)[0].replace("__r", "__c").rstrip("Id")


def _normalize_parent_key(key: str) -> str:
    k = (key or "").strip()
    if k.endswith("__r"):
        return k[:-3] + "__c"
    if k.endswith("Id") and len(k) > 2:
        return k[:-2]
    return k


def is_reference_base_field(field_api_name: str, field_meta: Optional[Dict[str, Any]] = None) -> bool:
    api = (field_api_name or "").strip()
    if not api or "." in api:
        return False
    meta = field_meta or {}
    if meta.get("is_lookup"):
        return True
    if api in _SYSTEM_REFERENCE_FIELDS:
        return True
    if api.endswith("Id"):
        return True
    return False


def is_allowed_field_for_scope(
    field_api_name: str,
    scope: Dict[str, Any],
    *,
    field_meta: Optional[Dict[str, Any]] = None,
    prompt: Optional[str] = None,
    for_attachments: bool = False,
) -> bool:
    api = (field_api_name or "").strip()
    if not api:
        return False
    if not scope.get("cross_system"):
        return True

    if for_attachments and api == "Id":
        return True

    if "." in api:
        if not scope.get("include_related_data"):
            return False
        allowed = {_normalize_parent_key(k) for k in (scope.get("parent_entity_keys") or [])}
        rel_parent = _normalize_parent_key(_relationship_parent_key(api) or "")
        if not allowed:
            return False
        if rel_parent not in allowed and not any(
            rel_parent.lower() == a.lower() or a.lower() in rel_parent.lower()
            for a in allowed
        ):
            return False
        related_hints = scope.get("related_field_hints") or []
        if related_hints:
            field_part = api.split(".", 1)[-1].lower()
            label_part = (field_meta or {}).get("label") or field_part
            combined = f"{field_part} {label_part}".lower()
            if not any(
                str(h).lower() in combined or str(h).lower() in field_part
                for h in related_hints
            ):
                return False
        return True

    if api == "Id":
        return user_explicitly_requested_id(prompt)

    if is_reference_base_field(api, field_meta):
        return False

    if (field_meta or {}).get("is_defaulted_on_create") and api in (
        "CreatedById",
        "LastModifiedById",
        "OwnerId",
    ):
        return False

    return True


def filter_field_metadata_for_scope(
    field_list: Sequence[Dict[str, Any]],
    scope: Dict[str, Any],
    *,
    prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not scope.get("cross_system"):
        return list(field_list)
    out: List[Dict[str, Any]] = []
    for field in field_list:
        api = (field.get("api_name") or "").strip()
        if not api:
            continue
        if field.get("is_relationship_field"):
            if is_allowed_field_for_scope(api, scope, field_meta=field, prompt=prompt):
                out.append(dict(field))
            continue
        if is_allowed_field_for_scope(api, scope, field_meta=field, prompt=prompt):
            out.append(dict(field))
    return out


def apply_cross_system_field_guardrail(
    selected_fields: Sequence[str],
    scope: Dict[str, Any],
    *,
    field_metadata: Optional[Sequence[Dict[str, Any]]] = None,
    prompt: Optional[str] = None,
    for_attachments: bool = False,
) -> List[str]:
    if not scope.get("cross_system"):
        return list(selected_fields)

    meta_by_api = {
        (f.get("api_name") or ""): f for f in (field_metadata or []) if f.get("api_name")
    }
    kept: List[str] = []
    for field in selected_fields:
        api = (field or "").strip()
        if not api:
            continue
        meta = meta_by_api.get(api)
        if is_allowed_field_for_scope(
            api,
            scope,
            field_meta=meta,
            prompt=prompt,
            for_attachments=for_attachments,
        ):
            kept.append(api)
    return _dedupe_preserve_order(kept)


def _dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _parse_soql_select_fields(soql: str) -> List[str]:
    if not soql:
        return []
    m = re.search(r"\bselect\s+(.*?)\s+from\s+", soql, re.I | re.DOTALL)
    if not m:
        return []
    chunk = m.group(1)
    fields: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in chunk:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            token = "".join(current).strip()
            if token:
                fields.append(token)
            current = []
        else:
            current.append(ch)
    token = "".join(current).strip()
    if token:
        fields.append(token)
    cleaned: List[str] = []
    for f in fields:
        f = f.strip()
        if not f or f == "*":
            continue
        # Strip simple aliases — SOQL rarely uses AS in our flow.
        cleaned.append(f.split()[-1] if " " in f and not f.startswith("(") else f)
    return cleaned


def _rebuild_soql_select(soql: str, fields: Sequence[str]) -> str:
    if not soql or not fields:
        return soql
    m = re.search(r"^(\s*select\s+)(.*?)(\s+from\s+.+)$", soql, re.I | re.DOTALL)
    if not m:
        return soql
    return f"{m.group(1)}{', '.join(fields)}{m.group(3)}"


def guardrail_soql_for_cross_system(
    soql: str,
    scope: Dict[str, Any],
    *,
    field_metadata: Optional[Sequence[Dict[str, Any]]] = None,
    prompt: Optional[str] = None,
    for_attachments: bool = False,
) -> Tuple[str, List[str]]:
    """Remove reference/id fields from SOQL SELECT for cross-system migrations."""
    if not scope.get("cross_system") or not soql:
        return soql, _parse_soql_select_fields(soql)

    select_fields = _parse_soql_select_fields(soql)
    if not select_fields:
        return soql, select_fields

    meta_by_api = {
        (f.get("api_name") or ""): f for f in (field_metadata or []) if f.get("api_name")
    }
    kept: List[str] = []
    for raw in select_fields:
        api = raw.strip()
        if "(" in api:
            kept.append(raw)
            continue
        meta = meta_by_api.get(api.split(".")[-1] if "." not in api else api)
        if is_allowed_field_for_scope(
            api,
            scope,
            field_meta=meta,
            prompt=prompt,
            for_attachments=for_attachments,
        ):
            kept.append(raw)

    if kept and len(kept) != len(select_fields):
        return _rebuild_soql_select(soql, kept), kept
    return soql, select_fields


# --- SQL column scoping ---

_SQL_ID_PATTERNS = (
    re.compile(r"^id$", re.I),
    re.compile(r".*_id$", re.I),
    re.compile(r"^uuid$", re.I),
    re.compile(r".*_uuid$", re.I),
    re.compile(r".*_fk$", re.I),
    re.compile(r".*foreign.?key.*", re.I),
)


def is_sql_reference_column(column_name: str) -> bool:
    col = (column_name or "").strip()
    if not col:
        return False
    return any(p.match(col) for p in _SQL_ID_PATTERNS)


def sql_cross_system_prompt_block(scope: Dict[str, Any]) -> str:
    if not scope.get("cross_system"):
        return ""
    block = """
**CROSS-SYSTEM MIGRATION (SQL):**
- Do NOT SELECT primary-key/id/foreign-key columns (id, *_id, uuid, *_fk) unless the user explicitly asked for them.
- When joining tables, select descriptive/business columns only — not join-key identifiers from the source database.
- Prefer names, emails, phones, addresses, and other human-meaningful attributes.
"""
    return block + strict_extract_field_prompt_block(scope)


def strict_extract_field_prompt_block(scope: Dict[str, Any]) -> str:
    """Unified strict field list for SQL, Salesforce, and Google Sheets extract prompts."""
    if not scope.get("field_hints_strict") or not scope.get("field_hints"):
        return ""
    import json

    hints = scope.get("field_hints") or []
    return f"""
**STRICT FIELD MODE (CRITICAL):**
The user confirmed ONLY these extract columns/fields: {json.dumps(hints)}
Select ONLY columns/fields that match this list. Do NOT add other columns unless they appear in that list.
"""


def filter_sql_referenced_columns(
    columns: Sequence[str],
    scope: Dict[str, Any],
    *,
    prompt: Optional[str] = None,
) -> List[str]:
    if not scope.get("cross_system"):
        return list(columns)
    if user_explicitly_requested_id(prompt):
        return list(columns)
    return [c for c in columns if not is_sql_reference_column(c)]


# --- Google Sheets column scoping ---

_GSHEETS_ID_HEADER_TOKENS = frozenset(
    {"id", "recordid", "externalid", "uuid", "foreignkey", "fk", "salesforceid", "sfid"}
)


def is_gsheets_reference_header(header: str) -> bool:
    norm = re.sub(r"[^a-z0-9]", "", (header or "").lower())
    if not norm:
        return False
    if norm in _GSHEETS_ID_HEADER_TOKENS:
        return True
    if norm.endswith("id") and len(norm) <= 32:
        return True
    if norm.endswith("uuid") or norm.endswith("fk"):
        return True
    return False


def filter_gsheets_columns_for_scope(
    columns: Sequence[str],
    scope: Dict[str, Any],
    *,
    prompt: Optional[str] = None,
) -> List[str]:
    if not scope.get("cross_system"):
        return list(columns)
    if user_explicitly_requested_id(prompt):
        return list(columns)
    return [c for c in columns if not is_gsheets_reference_header(str(c))]
