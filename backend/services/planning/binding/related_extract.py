"""Finalize related extract intent after bind_source LLM output."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.planning.binding.contract import EntityCandidate, SourceBindLLMOutput
from services.planning.binding.observability import log_binding
from services.planning.catalog_grounding import label_for_catalog_key
from services.planning.migration_intent_schemas import PromptMigrationIntent
from services.planning.schemas import normalize_connector_type


def _allowed_related_keys(primary: Optional[EntityCandidate]) -> set[str]:
    if not primary:
        return set()
    return {
        (rt.related_entity_key or "").strip().lower()
        for rt in (primary.relationship_targets or [])
        if (rt.related_entity_key or "").strip()
    }


def _related_label(
    primary: Optional[EntityCandidate],
    related_key: str,
    catalog_entries: Optional[List[Dict[str, Any]]],
) -> str:
    if primary:
        for rt in primary.relationship_targets or []:
            if (rt.related_entity_key or "").lower() == related_key.lower():
                if rt.label:
                    return rt.label
    if catalog_entries:
        return label_for_catalog_key(catalog_entries, related_key)
    return related_key


async def finalize_related_extract_intent(
    migration_intent: PromptMigrationIntent,
    source_bind: SourceBindLLMOutput,
    *,
    primary_entity_key: str,
    primary_candidate: Optional[EntityCandidate],
    classification: Dict[str, Any],
    user_id: str,
    catalog_ctx: Optional[Dict[str, Any]] = None,
    catalog_entries: Optional[List[Dict[str, Any]]] = None,
    prompt: str = "",
) -> PromptMigrationIntent:
    """Merge related entity bind output into migration_intent with catalog FK check."""
    related_key = (source_bind.related_entity_key or "").strip()
    if not related_key:
        return migration_intent

    allowed = _allowed_related_keys(primary_candidate)
    if allowed and related_key.lower() not in allowed:
        log_binding(
            "validate",
            f"related_rejected not_in_hints primary={primary_entity_key} related={related_key}",
            level=40,
        )
        return migration_intent

    src_ct = normalize_connector_type(classification.get("source") or "")
    ctx = dict(catalog_ctx or {})
    if not ctx.get("org_id") and src_ct == "salesforce":
        from utils.destination_connector_resolve import resolve_salesforce_token_record

        tr = await resolve_salesforce_token_record(
            user_id,
            destination_name=classification.get("source_name"),
            token_id=classification.get("source_connector_id"),
        )
        if tr and tr.get("org_id"):
            ctx["org_id"] = tr.get("org_id")

    from services.planning.catalog_grounding import lookup_source_relationship

    rel = await lookup_source_relationship(
        str(user_id),
        connector_type=src_ct,
        context=ctx,
        entity_a=primary_entity_key,
        entity_b=related_key,
    )
    if not rel:
        log_binding(
            "validate",
            f"related_rejected no_fk primary={primary_entity_key} related={related_key}",
            level=40,
        )
        return migration_intent

    child, parent, lookup_field, rel_name = rel
    related_label = _related_label(primary_candidate, parent, catalog_entries)
    related_hints = [
        str(h).strip()
        for h in (source_bind.related_field_keys or [])
        if str(h).strip()
    ]

    join_keys: List[str] = []
    for k in (primary_entity_key, child, parent, related_key):
        kk = (k or "").strip()
        if kk and kk.lower() not in {x.lower() for x in join_keys}:
            join_keys.append(kk)

    from services.planning.migration_intent import _build_extraction_summary

    updates: Dict[str, Any] = {
        "include_related_data": True,
        "relationship_status": "related",
        "extract_shape": "single_with_related",
        "driving_entity_key": child,
        "driving_entity_label": migration_intent.driving_entity_label or primary_entity_key,
        "parent_entity_key": parent,
        "parent_entity_label": related_label,
        "related_entity_key": parent,
        "related_entity_label": related_label,
        "lookup_field_path": lookup_field,
        "lookup_relationship_name": rel_name,
        "related_field_hints": related_hints,
        "join_entity_keys": join_keys,
        "extraction_summary": _build_extraction_summary(
            driving_label=migration_intent.driving_entity_label or primary_entity_key,
            field_hints=list(migration_intent.field_hints or []),
            include_related=True,
            parent_label=related_label,
            field_hints_strict=bool(migration_intent.field_hints_strict),
            related_field_hints=related_hints,
        ),
    }
    log_binding(
        "validate",
        f"related_ok primary={child} parent={parent} hints={len(related_hints)}",
    )
    return migration_intent.model_copy(update=updates)
