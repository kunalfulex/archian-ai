"""Unified extract field/column clarification for all source connector types."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.planning.migration_intent_schemas import ExtractAmbiguity, PromptMigrationIntent
from services.planning.schemas import normalize_connector_type

_ENTITY_AMBIGUITY_TYPES = frozenset({"extract_entity_role", "extract_related_vs_secondary"})


def has_entity_extract_ambiguities(intent: PromptMigrationIntent) -> bool:
    return any(
        (a.ambiguity_type or "") in _ENTITY_AMBIGUITY_TYPES
        for a in (intent.extract_ambiguities or [])
    )

_VAGUE_EXTRACT_PATTERNS = (
    re.compile(r"\bgeneral\b", re.I),
    re.compile(r"\ball\s+(?:the\s+)?(?:data|records|columns|fields|information)\b", re.I),
    re.compile(r"\buser\s+data\b", re.I),
    re.compile(r"\bcustomer\s+data\b", re.I),
    re.compile(r"\bclient\s+data\b", re.I),
    re.compile(r"\bmember\s+data\b", re.I),
    re.compile(r"\beverything\b", re.I),
    re.compile(r"\bany\s+(?:data|fields|columns)\b", re.I),
)

_VAGUE_FIELD_HINTS = frozenset(
    {
        "user data",
        "users data",
        "customer data",
        "client data",
        "member data",
        "general data",
        "all data",
        "contact data",
        "data",
        "information",
        "info",
        "details",
        "records",
        "everything",
    }
)

_FIELD_LIST_STOPWORDS = frozenset(
    {
        "to",
        "from",
        "and",
        "or",
        "the",
        "a",
        "an",
        "in",
        "on",
        "at",
        "of",
        "for",
        "with",
        "into",
        "then",
        "also",
        "please",
        "make",
        "sure",
        "that",
        "it",
        "be",
        "is",
        "are",
    }
)

_EXPLICIT_NAMED_FIELD_RE = re.compile(
    r"\b(first\s*name|last\s*name|email|phone|mobile|address|title|company)\b",
    re.I,
)

_EXPLICIT_COLUMNS_CLAUSE_RE = re.compile(
    r"\b(?:include|with|select|extract|get|fetch|only|just)\s+"
    r"(?:the\s+)?(?:columns?|fields?)\s+[:.]?\s*([a-z0-9_, \-]+)",
    re.I,
)

_EXTRACT_FIELDS_BEFORE_FROM_RE = re.compile(
    r"\b(?:extract|get|fetch|pull|select)\s+(.+?)\s+from\b",
    re.I,
)


def _is_vague_field_hint(hint: str) -> bool:
    norm = _normalize_field_token(hint)
    if not norm:
        return True
    if norm in _VAGUE_FIELD_HINTS:
        return True
    if any(p.search(norm) for p in _VAGUE_EXTRACT_PATTERNS):
        return True
    return False


def _prompt_lists_concrete_fields(prompt: str) -> bool:
    pl = (prompt or "").strip()
    if not pl:
        return False
    clause = _EXPLICIT_COLUMNS_CLAUSE_RE.search(pl)
    if clause:
        chunk = clause.group(1)
        parts = [p.strip() for p in re.split(r"[,;]", chunk) if p.strip()]
        concrete = [
            p
            for p in parts
            if not _is_vague_field_hint(p) and p.lower() not in _FIELD_LIST_STOPWORDS
        ]
        if concrete:
            return True
    if _EXPLICIT_NAMED_FIELD_RE.search(pl):
        return True
    for match in re.finditer(r"\b([a-z_][a-z0-9_]*)\s*,\s*([a-z_][a-z0-9_]*)\b", pl, re.I):
        a, b = match.group(1).lower(), match.group(2).lower()
        if a in _FIELD_LIST_STOPWORDS or b in _FIELD_LIST_STOPWORDS:
            continue
        if _is_vague_field_hint(a) or _is_vague_field_hint(b):
            continue
        if len(a) < 2 or len(b) < 2:
            continue
        return True
    return False


def fields_explicit_in_prompt(prompt: str, field_hints: Sequence[str]) -> bool:
    if not field_hints:
        return False
    concrete = [str(h) for h in field_hints if not _is_vague_field_hint(str(h))]
    if not concrete:
        return False
    matched = sum(1 for h in concrete if _field_token_in_prompt(prompt, h))
    return matched >= max(1, len(concrete) // 2)


def is_vague_extract_prompt(prompt: str) -> bool:
    pl = (prompt or "").strip()
    if not pl:
        return True
    if _prompt_lists_concrete_fields(pl):
        return False
    if any(p.search(pl) for p in _VAGUE_EXTRACT_PATTERNS):
        return True
    return False


_COMMON_INVENTED_CONTACT_FIELDS = frozenset(
    {
        "first name",
        "firstname",
        "last name",
        "lastname",
        "phone",
        "mobile",
        "email",
        "mailing address",
        "home phone",
        "assistant",
    }
)

_SYSTEM_FIELD_SUFFIXES = (
    "id",
    "uuid",
    "created_at",
    "updated_at",
    "created_by",
    "modified_by",
    "deleted_at",
)


def _normalize_field_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _field_token_in_prompt(prompt: str, hint: str) -> bool:
    pl = (prompt or "").lower()
    hint_l = (hint or "").lower().strip()
    if not pl or not hint_l:
        return False
    if hint_l in pl:
        return True
    norm_hint = _normalize_field_token(hint)
    norm_prompt = _normalize_field_token(pl)
    if norm_hint and norm_hint in norm_prompt:
        return True
    underscored = hint_l.replace(" ", "_")
    if underscored in pl.replace("-", "_"):
        return True
    return False


def hints_look_destination_invented(
    field_hints: Sequence[str],
    *,
    cross_system: bool,
    prompt: str,
) -> bool:
    if not cross_system or not field_hints:
        return False
    if fields_explicit_in_prompt(prompt, field_hints):
        return False
    normalized = {_normalize_field_token(h) for h in field_hints if h}
    if len(normalized) >= 2 and normalized.issubset(_COMMON_INVENTED_CONTACT_FIELDS):
        return True
    return False


def strip_vague_field_hints(field_hints: Sequence[str]) -> List[str]:
    return [str(h).strip() for h in field_hints if str(h).strip() and not _is_vague_field_hint(str(h))]


def needs_extract_field_clarification(
    intent: PromptMigrationIntent,
    prompt: str,
) -> bool:
    if intent.extract_fields_confirmed:
        return False
    if not intent.resolved_driving_key():
        return False
    if has_entity_extract_ambiguities(intent):
        return False

    from services.planning.migration_intent_schemas import scope_confidence_threshold

    scope = intent.extract_field_scope or "unspecified"
    conf = float(intent.field_scope_confidence or 0.0)
    threshold = scope_confidence_threshold()

    if scope == "all_descriptive" and conf >= threshold:
        return False
    if scope == "vague" and conf >= threshold:
        return True
    if scope == "explicit" and conf >= threshold and intent.field_hints_strict and intent.field_hints:
        return False

    if scope == "unspecified" or conf < threshold:
        if hints_look_destination_invented(
            intent.field_hints or [],
            cross_system=intent.cross_system,
            prompt=prompt,
        ):
            return True
        if scope == "vague":
            return True
        if scope == "explicit" and intent.field_hints_strict:
            return False
        return scope in ("vague", "unspecified")

    return False


def _is_descriptive_column(name: str, *, connector_type: str) -> bool:
    norm = _normalize_field_token(name).replace(" ", "_")
    if not norm:
        return False
    if norm in {"id", "uuid"}:
        return False
    for suffix in _SYSTEM_FIELD_SUFFIXES:
        if norm == suffix or norm.endswith(f"_{suffix}") or norm.endswith(suffix):
            return False
    if connector_type == "salesforce" and norm.endswith("id") and len(norm) > 2:
        return False
    return True


def _option_id_for_field(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    return f"field_hint::{slug or 'column'}"


def build_field_clarification_options(
    suggested_labels: Sequence[str],
    *,
    entity_label: Optional[str] = None,
) -> List[Dict[str, str]]:
    options: List[Dict[str, str]] = [
        {
            "id": "field_all_descriptive",
            "label": "All descriptive columns (recommended)",
        },
    ]
    seen: set[str] = set()
    for label in suggested_labels or []:
        text = str(label or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        options.append({"id": _option_id_for_field(text), "label": text})
        if len(options) >= 13:
            break
    entity = (entity_label or "source").strip()
    options.append(
        {
            "id": "fields_custom",
            "label": f"Type column names in chat (e.g. name, email for {entity})",
        }
    )
    return options


def _match_hint_to_catalog_label(hint: str, catalog_labels: Sequence[str]) -> Optional[str]:
    needle = _normalize_field_token(hint)
    if not needle:
        return None
    underscored = needle.replace(" ", "_")
    for label in catalog_labels:
        text = str(label or "").strip()
        if not text:
            continue
        norm = _normalize_field_token(text)
        if norm == needle or norm.replace(" ", "_") == underscored:
            return text
        if needle in norm or norm in needle:
            return text
    return None


async def validate_field_hints_against_catalog(
    intent: PromptMigrationIntent,
    *,
    user_id: str,
    classification: Dict[str, Any],
    entity_key: str,
) -> Dict[str, Any]:
    """Match explicit field hints to synced source catalog columns/fields."""
    hints = [str(h).strip() for h in (intent.field_hints or []) if str(h).strip()]
    catalog_labels = await load_source_field_labels(str(user_id), classification, entity_key)
    matched: List[str] = []
    unmatched: List[str] = []
    for hint in hints:
        hit = _match_hint_to_catalog_label(hint, catalog_labels)
        if hit:
            if hit not in matched:
                matched.append(hit)
        else:
            unmatched.append(hint)
    return {
        "valid": not unmatched,
        "matched": matched,
        "unmatched": unmatched,
        "normalized_hints": matched if matched else hints,
        "catalog_labels": catalog_labels,
    }


async def apply_catalog_field_validation(
    intent: PromptMigrationIntent,
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: str = "",
) -> PromptMigrationIntent:
    """For explicit scope, validate hints; downgrade to vague if catalog cannot match."""
    if (intent.extract_field_scope or "unspecified") != "explicit":
        return intent
    if not intent.field_hints:
        return intent.model_copy(
            update={"extract_field_scope": "vague", "field_hints_strict": False}
        )
    entity_key = intent.resolved_driving_key() or ""
    if not entity_key:
        return intent
    result = await validate_field_hints_against_catalog(
        intent,
        user_id=str(user_id),
        classification=classification,
        entity_key=entity_key,
    )
    if result.get("valid"):
        return intent.model_copy(
            update={
                "field_hints": result.get("normalized_hints") or intent.field_hints,
                "field_hints_strict": True,
            }
        )
    suggested = list(result.get("catalog_labels") or [])
    for h in intent.field_hints or []:
        if h not in suggested:
            suggested.insert(0, h)
    return intent.model_copy(
        update={
            "extract_field_scope": "vague",
            "field_hints": [],
            "field_hints_strict": False,
            "suggested_field_hints": _dedupe(suggested)[:20],
        }
    )


async def load_field_entries(
    user_id: str,
    classification: Dict[str, Any],
    entity_key: str,
    *,
    role: str = "source",
) -> List[Dict[str, str]]:
    """Load fields for one side (source or destination) with api_name and human label."""
    from services.planning.catalog_grounding import load_side_catalog_summary

    side = "destination" if role == "destination" else "source"
    ct = normalize_connector_type(classification.get(side) or "")
    entries, _ct, ctx = await load_side_catalog_summary(
        user_id, classification, role=side
    )
    entity = (entity_key or "").strip()
    if not entity:
        return []

    out: List[Dict[str, str]] = []

    if ct == "salesforce":
        org_id = ctx.get("org_id")
        if not org_id:
            return []
        from db.connection import database

        rows = await database.fetch_all(
            """
            SELECT field_api_name, field_label
            FROM salesforce_object_fields
            WHERE user_id = :user_id AND org_id = :org_id
              AND LOWER(object_api_name) = LOWER(:object)
            ORDER BY field_label NULLS LAST, field_api_name
            LIMIT 80
            """,
            {"user_id": str(user_id), "org_id": org_id, "object": entity},
        )
        for row in rows or []:
            r = dict(row)
            api = (r.get("field_api_name") or "").strip()
            label = (r.get("field_label") or api or "").strip()
            if not api or "." in api:
                continue
            if _is_descriptive_column(label, connector_type="salesforce"):
                out.append({"api_name": api, "label": label})
        seen: set[str] = set()
        deduped: List[Dict[str, str]] = []
        for item in out:
            key = item["api_name"].lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:40]

    if ct == "sql":
        connector_id = ctx.get("connector_id")
        if not connector_id:
            return []
        from handlers.sql_handler import get_sql_table_fields_from_store

        cols = await get_sql_table_fields_from_store(str(user_id), str(connector_id), entity)
        for col in cols or []:
            name = (col.get("name") or "").strip()
            if name and _is_descriptive_column(name, connector_type="sql"):
                out.append({"api_name": name, "label": name})
        return _dedupe_dict_entries(out)[:40]

    if ct == "google_sheets":
        spreadsheet_id = ctx.get("spreadsheet_id")
        tab = entity.split("::", 1)[-1] if "::" in entity else entity
        if not spreadsheet_id and "::" in entity:
            spreadsheet_id = entity.split("::", 1)[0]
        if not spreadsheet_id:
            for row in entries:
                if (row.get("entity_key") or "") == entity:
                    break
        if not spreadsheet_id:
            return []
        from db.connection import database
        from db.extracted_schema import _parse_gsheets_header_to_columns

        grow = await database.fetch_one(
            """
            SELECT header_schema
            FROM gsheets_extracted_schemas
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
              AND LOWER(sheet_name) = LOWER(:tab)
              AND COALESCE(is_deleted, false) = false
            ORDER BY extracted_at DESC
            LIMIT 1
            """,
            {
                "user_id": str(user_id),
                "spreadsheet_id": spreadsheet_id,
                "tab": tab,
            },
        )
        if not grow:
            return []
        cols = _parse_gsheets_header_to_columns(dict(grow).get("header_schema"))
        for name in sorted(cols.keys(), key=lambda x: x.lower()):
            if _is_descriptive_column(name, connector_type="google_sheets"):
                out.append({"api_name": name, "label": name})
        return _dedupe_dict_entries(out)[:40]

    return out


async def load_source_field_entries(
    user_id: str,
    classification: Dict[str, Any],
    entity_key: str,
) -> List[Dict[str, str]]:
    """Load source fields (alias for load_field_entries with role=source)."""
    return await load_field_entries(
        user_id, classification, entity_key, role="source"
    )


def _dedupe_dict_entries(items: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: set[str] = set()
    out: List[Dict[str, str]] = []
    for item in items:
        api = (item.get("api_name") or "").strip()
        if not api:
            continue
        key = api.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"api_name": api, "label": (item.get("label") or api).strip()})
    return out


async def load_source_field_labels(
    user_id: str,
    classification: Dict[str, Any],
    entity_key: str,
) -> List[str]:
    """Load human-readable column/field labels from the synced source catalog."""
    entries = await load_source_field_entries(user_id, classification, entity_key)
    labels = [(e.get("label") or e.get("api_name") or "").strip() for e in entries]
    return _dedupe([lbl for lbl in labels if lbl])[:20]


def _dedupe(values: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for v in values:
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _field_label_from_option_id(option_id: str, options: Sequence[Dict[str, str]]) -> Optional[str]:
    oid = (option_id or "").strip()
    for opt in options or []:
        if opt.get("id") == oid:
            return (opt.get("label") or "").strip() or None
    if oid.startswith("field_hint::"):
        return oid.split("::", 1)[-1].replace("_", " ").strip() or None
    return None


def apply_field_clarification_selections(
    intent: PromptMigrationIntent,
    *,
    option_ids: Sequence[str],
    text_value: Optional[str] = None,
    options: Optional[Sequence[Dict[str, str]]] = None,
) -> PromptMigrationIntent:
    """Apply one or more column picks from pick_extract_fields (multi-select supported)."""
    ids = [str(o).strip() for o in (option_ids or []) if str(o).strip()]
    if not ids:
        return intent

    exclusive = {"field_all_descriptive", "fields_custom"}
    if any(oid in exclusive for oid in ids):
        return apply_field_clarification_selection(
            intent, option_id=ids[0], text_value=text_value, options=options
        )
    if len(ids) == 1:
        return apply_field_clarification_selection(
            intent, option_id=ids[0], text_value=text_value, options=options
        )

    labels: List[str] = []
    for oid in ids:
        if not oid.startswith("field_hint::"):
            continue
        label = _field_label_from_option_id(oid, options or [])
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return intent

    updated = intent.model_copy(deep=True)
    updated = updated.model_copy(
        update={
            "field_hints": labels,
            "field_hints_strict": True,
            "extract_field_scope": "explicit",
            "field_scope_confidence": 1.0,
            "extract_fields_confirmed": True,
        }
    )
    amb = [
        a
        for a in (updated.extract_ambiguities or [])
        if a.ambiguity_type != "pick_extract_fields"
    ]
    updated = updated.model_copy(
        update={
            "extract_ambiguities": amb,
            "needs_extract_clarification": bool(amb),
        }
    )
    entity_label = updated.driving_entity_label or updated.resolved_driving_key() or "records"
    updated = updated.model_copy(
        update={
            "extraction_summary": _build_summary_with_fields(updated, entity_label),
        }
    )
    return updated


def apply_field_clarification_selection(
    intent: PromptMigrationIntent,
    *,
    option_id: str,
    text_value: Optional[str] = None,
    options: Optional[Sequence[Dict[str, str]]] = None,
) -> PromptMigrationIntent:
    oid = (option_id or "").strip()
    updated = intent.model_copy(deep=True)

    if oid == "field_all_descriptive":
        updated = updated.model_copy(
            update={
                "field_hints": [],
                "field_hints_strict": False,
                "extract_field_scope": "all_descriptive",
                "field_scope_confidence": 1.0,
                "extract_fields_confirmed": True,
            }
        )
    elif oid == "fields_custom":
        raw = (text_value or "").strip()
        if raw:
            parts = [p.strip() for p in re.split(r"[,;]", raw) if p.strip()]
            updated = updated.model_copy(
                update={
                    "field_hints": parts,
                    "field_hints_strict": True,
                    "extract_field_scope": "explicit",
                    "field_scope_confidence": 1.0,
                    "extract_fields_confirmed": True,
                }
            )
    elif oid.startswith("field_hint::"):
        label = _field_label_from_option_id(oid, options or [])
        if label:
            updated = updated.model_copy(
                update={
                    "field_hints": [label],
                    "field_hints_strict": True,
                    "extract_field_scope": "explicit",
                    "field_scope_confidence": 1.0,
                    "extract_fields_confirmed": True,
                }
            )

    if updated.extract_fields_confirmed:
        amb = [
            a
            for a in (updated.extract_ambiguities or [])
            if a.ambiguity_type != "pick_extract_fields"
        ]
        updated = updated.model_copy(
            update={
                "extract_ambiguities": amb,
                "needs_extract_clarification": bool(amb),
            }
        )
        entity_label = updated.driving_entity_label or updated.resolved_driving_key() or "records"
        updated = updated.model_copy(
            update={
                "extraction_summary": _build_summary_with_fields(updated, entity_label),
            }
        )
    return updated


def _build_summary_with_fields(intent: PromptMigrationIntent, entity_label: str) -> str:
    from services.planning.migration_intent import _build_extraction_summary

    hints = intent.field_hints if intent.field_hints_strict else []
    return _build_extraction_summary(
        driving_label=entity_label,
        field_hints=hints,
        include_related=bool(intent.include_related_data),
        parent_label=intent.parent_entity_label or intent.parent_entity_key,
        field_hints_strict=intent.field_hints_strict,
    )


def _intent_with_extract_fields_locked(
    intent: PromptMigrationIntent,
    *,
    classification: Optional[Dict[str, Any]] = None,
) -> PromptMigrationIntent:
    """Clear stale pick_extract_fields state once the user confirmed column picks."""
    from services.planning.plan_extract_gates import extract_fields_locked

    if not extract_fields_locked(classification or {}, intent):
        return intent
    amb = [
        a
        for a in (intent.extract_ambiguities or [])
        if (a.ambiguity_type or "") != "pick_extract_fields"
    ]
    return intent.model_copy(
        update={
            "extract_fields_confirmed": True,
            "needs_extract_clarification": bool(
                any((a.ambiguity_type or "") in _ENTITY_AMBIGUITY_TYPES for a in amb)
            ),
            "extract_ambiguities": amb,
        }
    )


async def resolve_field_clarification_for_plan(
    migration_intent: Optional[PromptMigrationIntent],
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: str,
    plan_contract: Optional[Any] = None,
) -> Optional[PromptMigrationIntent]:
    """
    Run field/column clarification after plan entities are resolved.

    Driving entity often comes from entity_resolver (plan_contract), not decompose.
    """
    from services.planning.migration_intent import _is_cross_system
    from services.planning.plan_extract_gates import extract_fields_locked

    if extract_fields_locked(classification, migration_intent):
        if migration_intent is None:
            return None
        return _intent_with_extract_fields_locked(
            migration_intent, classification=classification
        )

    intent = migration_intent
    if intent is None:
        src = getattr(plan_contract, "source", None) if plan_contract else None
        entity_key = getattr(src, "entity_key", None) if src else None
        if not entity_key:
            return None
        intent = PromptMigrationIntent(
            cross_system=_is_cross_system(classification),
            driving_entity_key=str(entity_key),
            driving_entity_label=(
                getattr(src, "catalog_label", None)
                or getattr(src, "entity_label", None)
                or str(entity_key)
            ),
        )
    elif plan_contract and getattr(plan_contract, "source", None):
        src = plan_contract.source
        if getattr(src, "is_resolved", lambda: False)():
            entity_key = (getattr(src, "entity_key", None) or "").strip()
            if entity_key:
                intent = intent.model_copy(
                    update={
                        "driving_entity_key": entity_key,
                        "driving_entity_label": (
                            getattr(src, "catalog_label", None)
                            or getattr(src, "entity_label", None)
                            or entity_key
                        ),
                    }
                )

    if has_entity_extract_ambiguities(intent):
        return intent

    intent = await apply_catalog_field_validation(
        intent,
        user_id=str(user_id),
        classification=classification,
        prompt=prompt or "",
    )

    return await finalize_extract_field_clarification(
        intent,
        user_id=str(user_id),
        classification=classification,
        prompt=prompt or "",
    )


async def finalize_extract_field_clarification(
    intent: PromptMigrationIntent,
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: str,
) -> PromptMigrationIntent:
    """After entity grounding, decide whether to ask the user which columns/fields to extract."""
    if not needs_extract_field_clarification(intent, prompt):
        return intent

    entity_key = intent.resolved_driving_key() or ""
    entity_label = intent.driving_entity_label or entity_key

    suggested = await load_source_field_labels(str(user_id), classification, entity_key)

    pending_hints = list(intent.field_hints or [])
    if pending_hints and not suggested:
        suggested = pending_hints
    elif pending_hints:
        for h in pending_hints:
            if h not in suggested:
                suggested.insert(0, h)
        suggested = _dedupe(suggested)[:20]

    prompt_text = (
        f"Which columns or fields should we extract from {entity_label}? "
        "Pick a suggestion below or type column names in chat."
    )
    amb = list(intent.extract_ambiguities or [])
    if not any(a.ambiguity_type == "pick_extract_fields" for a in amb):
        amb.append(
            ExtractAmbiguity(
                ambiguity_type="pick_extract_fields",
                prompt=prompt_text,
                entity_phrase=entity_label,
            )
        )

    return intent.model_copy(
        update={
            "field_hints": [],
            "field_hints_strict": False,
            "suggested_field_hints": suggested,
            "needs_extract_clarification": True,
            "extract_ambiguities": amb,
            "extraction_summary": f"Extract from {entity_label}",
        }
    )


def parse_field_clarification_reply(
    text: str,
    options: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """Map chat text to a field clarification selection."""
    raw = (text or "").strip()
    if not raw:
        return None
    tl = raw.lower()
    if any(k in tl for k in ("all columns", "all fields", "all descriptive", "everything")):
        return {"option_id": "field_all_descriptive"}
    if "," in raw or ";" in raw:
        return {"option_id": "fields_custom", "text_value": raw}
    opt_id = None
    for opt in options or []:
        label = (opt.get("label") or "").strip().lower()
        oid = (opt.get("id") or "").strip()
        if label and (tl == label or label in tl or tl in label):
            opt_id = oid
            break
    if opt_id:
        out: Dict[str, str] = {"option_id": opt_id}
        if opt_id == "fields_custom":
            out["text_value"] = raw
        return out
    if len(raw.split()) <= 6:
        return {"option_id": "fields_custom", "text_value": raw}
    return None
