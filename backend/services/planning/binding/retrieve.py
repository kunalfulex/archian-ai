"""Build entity shortlists (label, category, description, fields) for bind LLM calls."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.labeling.catalog_helpers import fetch_semantics_map, primary_from_semantics, resolve_connector_instance_id
from services.planning.binding.contract import EntityCandidate, FieldCandidate
from services.planning.binding.observability import log_binding
from services.planning.binding.relationship_targets import build_relationship_targets
from services.planning.entity_hints import build_retrieve_entity_hints
from services.planning.entity_scoring import score_retrieve_entry
from services.planning.schemas import normalize_connector_type

MAX_ENTITIES = 12
MAX_FIELDS_PER_ENTITY = 40
MAX_DESCRIPTION_CHARS = 280


async def _fields_for_entity(
    user_id: str,
    classification: Dict[str, Any],
    entity_key: str,
    *,
    connector_type: str,
    ctx: Dict[str, Any],
    role: str = "source",
) -> List[FieldCandidate]:
    from services.planning.extract_field_clarification import load_field_entries

    entries = await load_field_entries(
        str(user_id), classification, entity_key, role=role
    )
    out: List[FieldCandidate] = []
    for entry in entries[:MAX_FIELDS_PER_ENTITY]:
        api = (entry.get("api_name") or "").strip()
        label = (entry.get("label") or api).strip()
        if not api:
            continue
        out.append(FieldCandidate(key=api, label=label))
    return out


def _filter_metadata_for_vague_user_data(
    ranked: List[Tuple[float, Dict[str, str], List[str]]],
    *,
    prompt: str,
    connector_type: str,
    semantics_map: Dict[str, Dict[str, Any]],
) -> List[Tuple[float, Dict[str, str], List[str]]]:
    from services.planning.entity_catalog_policy import (
        is_metadata_entity,
        is_vague_user_data_migration_prompt,
    )

    if not is_vague_user_data_migration_prompt(prompt):
        return ranked
    filtered: List[Tuple[float, Dict[str, str], List[str]]] = []
    for score, row, reasons in ranked:
        key = (row.get("entity_key") or row.get("api_name") or "").strip()
        sem = semantics_map.get(key) or {}
        if is_metadata_entity(key, connector_type=connector_type, semantics=sem if isinstance(sem, dict) else None):
            continue
        filtered.append((score, row, reasons))
    return filtered if filtered else ranked


async def retrieve_entity_shortlist(
    user_id: str,
    classification: Dict[str, Any],
    *,
    role: str,
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]] = None,
    extra_hints: Optional[List[str]] = None,
    required_entity_keys: Optional[List[str]] = None,
    k: int = MAX_ENTITIES,
    migration_intent: Optional[Dict[str, Any]] = None,
    source_entity_key: Optional[str] = None,
    source_entity_label: Optional[str] = None,
) -> Tuple[List[EntityCandidate], str, Dict[str, Any]]:
    """
    Load and rank catalog entities for one side (source or destination).

    Returns (candidates, connector_type, context dict for field loads).
    """
    from services.planning.catalog_grounding import load_side_catalog_summary

    entries, ct, ctx = await load_side_catalog_summary(
        str(user_id), classification, role="source" if role == "source" else "destination"
    )
    if not entries:
        log_binding("retrieve", f"{role} candidates=0 (empty catalog)")
        return [], ct, ctx

    hint_bundle = build_retrieve_entity_hints(
        role=role,
        classification=classification,
        parsed_goal=parsed_goal,
        connector_type=ct,
        migration_intent=migration_intent
        or (
            classification.get("migration_intent")
            if isinstance(classification.get("migration_intent"), dict)
            else None
        ),
        extra_hints=extra_hints,
        source_entity_key=source_entity_key,
        source_entity_label=source_entity_label,
    )
    hints = hint_bundle.user_hints
    context_hints = hint_bundle.context_hints
    merged_required = list(required_entity_keys or [])
    for req in hint_bundle.required_entity_keys:
        if req and req not in merged_required:
            merged_required.append(req)

    connector_instance_id = await resolve_connector_instance_id(
        str(user_id), ct, ctx, classification, role=role
    )
    ctx["connector_instance_id"] = connector_instance_id

    semantics_map: Dict[str, Dict[str, Any]] = {}
    if connector_instance_id and ct in ("salesforce", "sql", "google_sheets"):
        semantics_map = await fetch_semantics_map(
            user_id=str(user_id),
            connector_type=ct,
            connector_instance_id=str(connector_instance_id),
        )

    catalog_rows = [
        {
            "label": (row.get("label") or row.get("entity_key") or row.get("api_name") or ""),
            "object_api_name": (row.get("entity_key") or row.get("api_name") or ""),
        }
        for row in entries
        if (row.get("entity_key") or row.get("api_name") or "").strip()
    ]
    catalog_by_key: Dict[str, Dict[str, Any]] = {}
    for row in entries:
        key = (row.get("entity_key") or row.get("api_name") or "").strip()
        if key:
            catalog_by_key[key] = row
            catalog_by_key[key.lower()] = row

    ranked: List[Tuple[float, Dict[str, str], List[str]]] = []
    for row in entries:
        key = (row.get("entity_key") or row.get("api_name") or "").strip()
        label = (row.get("label") or key).strip()
        if not key:
            continue
        sem = semantics_map.get(key) or {}
        scored = score_retrieve_entry(
            prompt,
            key,
            label,
            hints,
            semantics=sem if isinstance(sem, dict) else None,
            catalog=catalog_rows,
            connector_type=ct,
            context_hints=context_hints,
        )
        ranked.append((scored.score, row, scored.match_reasons))

    ranked.sort(key=lambda x: x[0], reverse=True)
    scored_total = len(ranked)
    ranked = _filter_metadata_for_vague_user_data(
        ranked, prompt=prompt, connector_type=ct, semantics_map=semantics_map
    )
    top_rows = [(r[1], r[0], r[2]) for r in ranked[:k]]
    present_keys = {
        (row.get("entity_key") or row.get("api_name") or "").strip().lower()
        for row, _, _ in top_rows
    }
    for req in merged_required or []:
        req_l = (req or "").strip().lower()
        if not req_l or req_l in present_keys:
            continue
        for score, row, reasons in ranked:
            key = (row.get("entity_key") or row.get("api_name") or "").strip()
            if key.lower() != req_l:
                continue
            top_rows.append((row, score, reasons))
            present_keys.add(req_l)
            break

    candidates: List[EntityCandidate] = []
    for row, match_score, match_reasons in top_rows:
        key = (row.get("entity_key") or row.get("api_name") or "").strip()
        label = (row.get("label") or key).strip()
        sem = semantics_map.get(key) or {}
        meta = primary_from_semantics(sem if isinstance(sem, dict) else None)
        desc = (meta.get("description") or "").strip()
        if len(desc) > MAX_DESCRIPTION_CHARS:
            desc = desc[: MAX_DESCRIPTION_CHARS - 3] + "..."
        fields = await _fields_for_entity(
            user_id,
            classification,
            key,
            connector_type=ct,
            ctx=ctx,
            role=role,
        )
        if not fields:
            continue
        relationship_targets = []
        if role == "source":
            relationship_targets = build_relationship_targets(
                primary_semantics=sem if isinstance(sem, dict) else None,
                semantics_map=semantics_map,
                catalog_by_key=catalog_by_key,
                prompt=prompt,
            )
        candidates.append(
            EntityCandidate(
                entity_key=key,
                label=label,
                category=(meta.get("category") or "").strip() or None,
                description=desc or None,
                synonyms=list(meta.get("synonyms") or [])[:12],
                match_score=match_score,
                match_reasons=match_reasons[:8],
                fields=fields,
                relationship_targets=relationship_targets,
            )
        )

    top_keys = ",".join(c.entity_key for c in candidates[:5])
    log_binding(
        "retrieve",
        f"{role} scored={scored_total} shortlist={len(candidates)} "
        f"catalog={len(entries)} top={top_keys or 'none'}",
    )
    return candidates, ct, ctx


def candidates_to_prompt_json(candidates: List[EntityCandidate]) -> str:
    import json

    payload = []
    for c in candidates:
        rel_targets = []
        for rt in c.relationship_targets or []:
            rel_targets.append(
                {
                    "related_entity_key": rt.related_entity_key,
                    "lookup_field": rt.lookup_field,
                    "relationship": rt.relationship,
                    "label": rt.label,
                    "category": rt.category,
                    "description": rt.description,
                    "synonyms": rt.synonyms[:8] if rt.synonyms else [],
                    "suggested_match_score": rt.suggested_match_score,
                    "match_reasons": rt.match_reasons[:5] if rt.match_reasons else [],
                }
            )
        payload.append(
            {
                "entity_key": c.entity_key,
                "label": c.label,
                "category": c.category,
                "description": c.description,
                "synonyms": c.synonyms[:8] if c.synonyms else [],
                "match_score": c.match_score,
                "match_reasons": c.match_reasons[:5] if c.match_reasons else [],
                "fields": [{"key": f.key, "label": f.label} for f in c.fields[:MAX_FIELDS_PER_ENTITY]],
                "relationship_targets": rel_targets,
            }
        )
    return json.dumps(payload, indent=2)
