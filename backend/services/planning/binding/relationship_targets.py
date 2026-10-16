"""Build related-entity candidates from primary semantics relationship_hints."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.labeling.catalog_helpers import primary_from_semantics
from services.planning.binding.contract import RelationshipTargetCandidate
from services.planning.entity_scoring import score_retrieve_entry

MAX_RELATED_TARGETS = 6
MAX_DESCRIPTION_CHARS = 280

# Deprioritize system lookups unless prompt mentions them explicitly.
_SYSTEM_RELATED_KEYS = frozenset(
    {
        "user",
        "recordtype",
        "group",
        "profile",
        "permissionset",
        "organization",
    }
)

_SYSTEM_PROMPT_TOKENS = (
    "owner",
    "record type",
    "recordtype",
    "created by",
    "modified by",
    "profile",
)


def _truncate_description(text: str) -> Optional[str]:
    desc = (text or "").strip()
    if not desc:
        return None
    if len(desc) > MAX_DESCRIPTION_CHARS:
        return desc[: MAX_DESCRIPTION_CHARS - 3] + "..."
    return desc


def _catalog_label(catalog_by_key: Dict[str, Dict[str, Any]], entity_key: str) -> str:
    row = catalog_by_key.get(entity_key) or catalog_by_key.get(entity_key.lower())
    if not row:
        return entity_key
    return (row.get("label") or row.get("entity_key") or entity_key).strip() or entity_key


def _prompt_mentions_system_related(prompt: str, related_key: str) -> bool:
    pl = (prompt or "").lower()
    if not pl:
        return False
    key_l = related_key.lower()
    if key_l in pl.replace("_", " "):
        return True
    return any(tok in pl for tok in _SYSTEM_PROMPT_TOKENS)


def _is_deprioritized_system_related(related_key: str, prompt: str) -> bool:
    key_l = (related_key or "").strip().lower()
    if key_l not in _SYSTEM_RELATED_KEYS:
        return False
    return not _prompt_mentions_system_related(prompt, related_key)


def build_relationship_targets(
    *,
    primary_semantics: Optional[Dict[str, Any]],
    semantics_map: Dict[str, Dict[str, Any]],
    catalog_by_key: Dict[str, Dict[str, Any]],
    prompt: str,
    max_targets: int = MAX_RELATED_TARGETS,
) -> List[RelationshipTargetCandidate]:
    """
    Expand primary entity relationship_hints into label-aware related targets
    using related object semantics from connector_entity_semantics.
    """
    sem = primary_semantics if isinstance(primary_semantics, dict) else {}
    hints = sem.get("relationship_hints") or []
    if not isinstance(hints, list) or not hints:
        return []

    catalog_rows = [
        {
            "label": (row.get("label") or row.get("entity_key") or ""),
            "object_api_name": (row.get("entity_key") or row.get("api_name") or ""),
        }
        for row in catalog_by_key.values()
        if (row.get("entity_key") or row.get("api_name") or "").strip()
    ]

    seen: set[str] = set()
    scored: List[tuple[float, RelationshipTargetCandidate]] = []

    for raw in hints:
        if not isinstance(raw, dict):
            continue
        related_key = (raw.get("related_entity") or "").strip()
        if not related_key or related_key.lower() in seen:
            continue
        seen.add(related_key.lower())

        if _is_deprioritized_system_related(related_key, prompt):
            continue

        related_sem = semantics_map.get(related_key) or {}
        meta = primary_from_semantics(related_sem if isinstance(related_sem, dict) else None)
        label = _catalog_label(catalog_by_key, related_key)

        score_result = score_retrieve_entry(
            prompt,
            related_key,
            label,
            hints=[related_key, label],
            semantics=related_sem if isinstance(related_sem, dict) else None,
            catalog=catalog_rows,
        )
        # Boost when prompt language aligns with related synonyms/category.
        bonus = 0.0
        pl = (prompt or "").lower()
        for syn in list(meta.get("synonyms") or [])[:8]:
            syn_l = str(syn).lower().strip()
            if syn_l and syn_l in pl:
                bonus += 25.0
        cat = (meta.get("category") or "").lower()
        if cat and any(w in pl for w in re.findall(r"[a-z]{4,}", cat)):
            bonus += 10.0

        final_score = score_result.score + bonus
        if final_score <= 0 and not raw.get("field"):
            continue

        target = RelationshipTargetCandidate(
            related_entity_key=related_key,
            lookup_field=(raw.get("field") or None),
            relationship=(raw.get("relationship") or None),
            label=label,
            category=(meta.get("category") or "").strip() or None,
            description=_truncate_description(meta.get("description") or ""),
            synonyms=list(meta.get("synonyms") or [])[:8],
            suggested_match_score=round(final_score, 2) if final_score > 0 else None,
            match_reasons=(score_result.match_reasons or [])[:5],
        )
        scored.append((final_score, target))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:max_targets]]
