"""Unified catalog entity scoring for all connector types (Salesforce, SQL, Google Sheets)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from services.labeling.catalog_helpers import primary_from_semantics
from services.planning.label_match import _phrase_hits_term, normalize_phrase

# Tier 1 — exact identity matches on entity phrases / explicit mentions
_SCORE_EXACT_KEY = 200.0
_SCORE_EXACT_LABEL = 200.0
_SCORE_EXACT_SYNONYM = 195.0
_SCORE_EXACT_CATEGORY = 190.0

# Tier 2 — semantics (category, synonyms, domains, description)
_SCORE_SEMANTICS_CATEGORY = 80.0
_SCORE_SEMANTICS_SYNONYM = 70.0
_SCORE_SEMANTICS_DOMAIN = 60.0
_SCORE_SEMANTICS_DESCRIPTION = 40.0

# Tier 3 — partial / fuzzy
_SCORE_PARTIAL_LABEL = 55.0
_SCORE_PARTIAL_KEY = 50.0
_SCORE_HINT_CATALOG = 45.0

MIN_ENTITY_MATCH = 35.0
MIN_CATALOG_MATCH = 35.0
AUTO_PIN_MIN_SCORE = 120.0
AUTO_PIN_GAP = 40.0
_METADATA_SCORE_PENALTY = 100.0
_USER_DATA_BOOST = 25.0
_PENDING_LABELING_PENALTY = 15.0


@dataclass
class EntityScoreResult:
    score: float = 0.0
    match_reasons: List[str] = field(default_factory=list)


def _stem_token(token: str) -> str:
    t = (token or "").strip().lower()
    if len(t) > 4 and t.endswith("s"):
        return t[:-1]
    return t


def _hint_stems(hints: List[str]) -> set[str]:
    stems: set[str] = set()
    for hint in hints or []:
        pl = normalize_phrase(hint)
        if pl:
            stems.add(_stem_token(pl))
            for word in pl.split():
                stems.add(_stem_token(word))
    return {s for s in stems if s}


def _extra_label_word_penalty(hints: List[str], label: str) -> float:
    hint_stems = _hint_stems(hints)
    if not hint_stems:
        return 0.0
    penalty = 0.0
    for word in normalize_phrase(label).split():
        wstem = _stem_token(word)
        if not wstem:
            continue
        if not any(wstem == hs or wstem in hs or hs in wstem for hs in hint_stems):
            penalty += 6.0
    return penalty


def _label_word_matches_hint(word: str, hint_stems: set[str]) -> bool:
    wstem = _stem_token(word)
    if not wstem:
        return False
    return any(wstem == hs or wstem in hs or hs in wstem for hs in hint_stems)


def _label_hint_coverage(hints: List[str], label: str) -> float:
    hint_stems = _hint_stems(hints)
    if not hint_stems:
        return 0.0
    words = normalize_phrase(label).split()
    if not words:
        return 0.0
    matched_chars = sum(len(w) for w in words if _label_word_matches_hint(w, hint_stems))
    total_chars = sum(len(w) for w in words)
    return matched_chars / max(total_chars, 1)


def _catalog_common_word_penalty(
    label: str,
    hints: List[str],
    catalog: Optional[List[Dict[str, Any]]] = None,
    *,
    label_key: str = "label",
) -> float:
    if not catalog:
        return 0.0
    hint_stems = _hint_stems(hints)
    if not hint_stems:
        return 0.0
    freq: Dict[str, int] = {}
    total = 0
    for row in catalog:
        text = normalize_phrase(
            (row.get(label_key) or row.get("object_api_name") or row.get("table_name") or "")
        )
        for word in text.split():
            freq[_stem_token(word)] = freq.get(_stem_token(word), 0) + 1
            total += 1
    if total <= 0:
        return 0.0
    penalty = 0.0
    for word in normalize_phrase(label).split():
        if _label_word_matches_hint(word, hint_stems):
            continue
        wstem = _stem_token(word)
        if not wstem:
            continue
        ratio = freq.get(wstem, 0) / max(len(catalog), 1)
        if ratio >= 0.08:
            penalty += ratio * 8.0
    return penalty


def _hint_hits_catalog_label(hint: str, label: str, api: str) -> float:
    if not hint:
        return 0.0
    if _phrase_hits_term(hint, label) or _phrase_hits_term(hint, api):
        return _SCORE_HINT_CATALOG + float(max(len(label), len(api)))
    h = normalize_phrase(hint)
    h_stem = _stem_token(h)
    label_norm = normalize_phrase(label)
    api_norm = normalize_phrase(api)
    if h_stem and h_stem in label_norm.replace(" ", ""):
        return _SCORE_HINT_CATALOG + float(len(h_stem))
    if h_stem and h_stem in api_norm:
        return _SCORE_HINT_CATALOG + float(len(h_stem)) - 1.0
    for word in label_norm.split():
        if h_stem and (_stem_token(word) == h_stem or h_stem in _stem_token(word)):
            return _SCORE_HINT_CATALOG + float(len(word))
    if " " in label and label_norm in h:
        return _SCORE_HINT_CATALOG + float(len(label))
    return 0.0


def _exact_normalized(a: str, b: str) -> bool:
    return bool(a and b and normalize_phrase(a) == normalize_phrase(b))


def _apply_score(
    result: EntityScoreResult,
    points: float,
    reason: str,
) -> None:
    if points > result.score:
        result.score = points
    if reason and reason not in result.match_reasons:
        result.match_reasons.append(reason)


def _score_semantics(
    result: EntityScoreResult,
    phrases: List[str],
    semantics: Optional[Dict[str, Any]],
    *,
    tier1: bool,
) -> None:
    if not semantics:
        return
    meta = primary_from_semantics(semantics)
    category = str(meta.get("category") or "").strip()
    description = str(meta.get("description") or "").strip()

    for phrase in phrases:
        if not phrase:
            continue
        if category:
            if tier1 and _exact_normalized(phrase, category):
                _apply_score(result, _SCORE_EXACT_CATEGORY, f"exact_category:{category}")
            elif _phrase_hits_term(phrase, category):
                _apply_score(result, _SCORE_SEMANTICS_CATEGORY, f"category:{category}")
        for syn in meta.get("synonyms") or []:
            s = str(syn).strip()
            if not s:
                continue
            if tier1 and _exact_normalized(phrase, s):
                _apply_score(result, _SCORE_EXACT_SYNONYM, f"exact_synonym:{s}")
            elif _phrase_hits_term(phrase, s):
                _apply_score(result, _SCORE_SEMANTICS_SYNONYM, f"synonym:{s}")
        for dom in meta.get("domains") or []:
            d = str(dom).strip()
            if d and _phrase_hits_term(phrase, d):
                _apply_score(result, _SCORE_SEMANTICS_DOMAIN, f"domain:{d}")
        if description and _phrase_hits_term(phrase, description[:120]):
            _apply_score(result, _SCORE_SEMANTICS_DESCRIPTION, "description")


def _score_context_phrases(
    result: EntityScoreResult,
    context_phrases: List[str],
    *,
    key: str,
    lbl: str,
    semantics: Optional[Dict[str, Any]],
) -> None:
    """Tier-2/3 scoring for job/session fallback phrases (never tier-1 exact)."""
    for phrase in context_phrases:
        if not phrase:
            continue
        _score_semantics(result, [phrase], semantics, tier1=False)
        hint_pts = _hint_hits_catalog_label(phrase, lbl, key)
        if hint_pts > 0:
            _apply_score(result, hint_pts, "context_hint_catalog")
        if key and re.search(rf"\b{re.escape(key)}\b", phrase, re.I):
            _apply_score(result, _SCORE_PARTIAL_KEY + len(key), f"context_key:{key}")
        if lbl and _phrase_hits_term(phrase, lbl):
            _apply_score(
                result,
                _SCORE_PARTIAL_LABEL + min(len(lbl), 30),
                f"context_label:{lbl}",
            )


def score_catalog_entity(
    *,
    entity_key: str,
    label: str,
    prompt: str,
    hints: List[str],
    semantics: Optional[Dict[str, Any]] = None,
    catalog: Optional[List[Dict[str, Any]]] = None,
    workbook_name: Optional[str] = None,
    connector_type: Optional[str] = None,
    labeling_status: Optional[str] = None,
    context_hints: Optional[List[str]] = None,
) -> EntityScoreResult:
    """
    Score one catalog entity against user entity phrases and optional semantics.

    Tier 1 exact matches apply to hints (entity phrases). The full prompt contributes
    tier 2 semantics and tier 3 partial matches only — avoiding migrate wording false positives.
    """
    key = (entity_key or "").strip()
    lbl = (label or key or "").strip()
    if not key:
        return EntityScoreResult()

    entity_phrases = [h for h in dict.fromkeys(hints or []) if (h or "").strip()]
    result = EntityScoreResult()

    for phrase in entity_phrases:
        if _exact_normalized(phrase, key):
            _apply_score(result, _SCORE_EXACT_KEY, f"exact_key:{key}")
        if lbl and _exact_normalized(phrase, lbl):
            _apply_score(result, _SCORE_EXACT_LABEL, f"exact_label:{lbl}")
        if key and re.search(rf"\b{re.escape(key)}\b", phrase, re.I):
            _apply_score(result, _SCORE_EXACT_KEY, f"exact_key_token:{key}")
        _score_semantics(result, [phrase], semantics, tier1=True)
        hint_pts = _hint_hits_catalog_label(phrase, lbl, key)
        if hint_pts > 0:
            _apply_score(result, hint_pts, "hint_catalog")

    all_texts = [prompt] + entity_phrases
    for text in all_texts:
        if not text:
            continue
        if key and re.search(rf"\b{re.escape(key)}\b", text, re.I):
            _apply_score(result, _SCORE_PARTIAL_KEY + len(key), f"key_in_text:{key}")
        if lbl and _phrase_hits_term(text, lbl):
            _apply_score(result, _SCORE_PARTIAL_LABEL + min(len(lbl), 30), f"label_partial:{lbl}")
        elif lbl and " " in lbl and normalize_phrase(lbl) in normalize_phrase(text):
            _apply_score(result, _SCORE_PARTIAL_LABEL, f"label_substring:{lbl}")

    wb = (workbook_name or "").strip()
    if wb:
        for text in all_texts:
            if text and _phrase_hits_term(text, wb):
                _apply_score(result, 30.0, f"workbook:{wb}")

    _score_semantics(result, all_texts, semantics, tier1=False)
    _score_context_phrases(
        result,
        [h for h in dict.fromkeys(context_hints or []) if (h or "").strip()],
        key=key,
        lbl=lbl,
        semantics=semantics,
    )

    ct = (connector_type or "").strip().lower()
    if ct:
        from services.planning.entity_catalog_policy import (
            is_metadata_entity,
            is_user_data_priority_entity,
            is_vague_user_data_migration_prompt,
        )

        if is_metadata_entity(key, connector_type=ct, semantics=semantics):
            result.score -= _METADATA_SCORE_PENALTY
            if "metadata_penalty" not in result.match_reasons:
                result.match_reasons.append("metadata_penalty")

        if is_vague_user_data_migration_prompt(prompt) and is_user_data_priority_entity(
            key, connector_type=ct
        ):
            result.score += _USER_DATA_BOOST
            result.match_reasons.append("user_data_priority")

    if labeling_status and labeling_status not in ("ready",):
        result.score -= _PENDING_LABELING_PENALTY

    result.score -= _extra_label_word_penalty(entity_phrases, lbl)
    result.score -= _catalog_common_word_penalty(lbl, entity_phrases, catalog)
    result.score += _label_hint_coverage(entity_phrases, lbl) * 12.0
    result.score = max(0.0, result.score)
    return result


def score_entity_summary(
    prompt: str,
    entity_key: str,
    semantics: Optional[Dict[str, Any]],
    *,
    hints: Optional[List[str]] = None,
) -> EntityScoreResult:
    meta = primary_from_semantics(semantics)
    label = str(meta.get("category") or entity_key or "").strip()
    return score_catalog_entity(
        entity_key=entity_key,
        label=label,
        prompt=prompt,
        hints=hints or [],
        semantics=semantics,
    )


def match_entity_in_prompt(
    prompt: str,
    entity_summaries: List[Dict[str, Any]],
) -> Optional[str]:
    """Best entity_key whose catalog semantics match the user prompt."""
    pl = normalize_phrase(prompt)
    if not pl or not entity_summaries:
        return None
    best_key: Optional[str] = None
    best_score = 0.0
    for ent in entity_summaries:
        key = str(ent.get("entity_key") or "").strip()
        if not key:
            continue
        sem = ent.get("semantics") if isinstance(ent.get("semantics"), dict) else {}
        scored = score_entity_summary(prompt, key, sem)
        if scored.score > best_score:
            best_score = scored.score
            best_key = key
    return best_key if best_score >= MIN_ENTITY_MATCH else None


def _semantics_for_key(
    semantics_map: Optional[Dict[str, Dict[str, Any]]],
    entity_key: str,
) -> Optional[Dict[str, Any]]:
    if not semantics_map:
        return None
    sem = semantics_map.get(entity_key)
    return sem if isinstance(sem, dict) else None


def rank_salesforce_catalog(
    prompt: str,
    catalog: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Tuple[str, str, float]]:
    ranked: List[Tuple[str, str, float]] = []
    for row in catalog:
        api = (row.get("object_api_name") or "").strip()
        label = (row.get("label") or api or "").strip()
        if not api:
            continue
        scored = score_catalog_entity(
            entity_key=api,
            label=label,
            prompt=prompt,
            hints=hints,
            semantics=_semantics_for_key(semantics_map, api),
            catalog=catalog,
        )
        if scored.score > 0:
            ranked.append((api, label, scored.score))
    ranked.sort(key=lambda item: (item[2], item[1]), reverse=True)
    return ranked


def rank_sql_catalog(
    prompt: str,
    tables: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
    connector_type: str = "sql",
) -> List[Tuple[str, float]]:
    ranked: List[Tuple[str, float]] = []
    for row in tables:
        name = (row.get("table_name") or row.get("entity_key") or row.get("api_name") or "").strip()
        if not name:
            continue
        label = (row.get("label") or name).strip()
        sem = _semantics_for_key(semantics_map, name)
        scored = score_catalog_entity(
            entity_key=name,
            label=label,
            prompt=prompt,
            hints=hints,
            semantics=sem,
            catalog=[{"label": (r.get("label") or r.get("table_name") or ""), "table_name": r.get("table_name")} for r in tables],
            connector_type=connector_type,
        )
        if scored.score > 0:
            ranked.append((name, scored.score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def rank_gsheets_catalog(
    prompt: str,
    sheets: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
    connector_type: str = "google_sheets",
) -> List[Tuple[str, str, str, float]]:
    ranked: List[Tuple[str, str, str, float]] = []
    for row in sheets:
        sid = (row.get("spreadsheet_id") or "").strip()
        tab = (row.get("sheet_name") or "").strip()
        wb = (row.get("spreadsheet_name") or row.get("label") or "").strip()
        entity_key = (row.get("entity_key") or f"{sid}::{tab}" if sid and tab else tab).strip()
        if not entity_key:
            continue
        label = (row.get("label") or tab or entity_key).strip()
        sem = _semantics_for_key(semantics_map, entity_key) or _semantics_for_key(semantics_map, tab)
        scored = score_catalog_entity(
            entity_key=entity_key,
            label=label,
            prompt=prompt,
            hints=hints,
            semantics=sem,
            workbook_name=wb or None,
            connector_type=connector_type,
        )
        if scored.score > 0:
            ranked.append((entity_key, tab or label, label, scored.score))
    ranked.sort(key=lambda item: item[3], reverse=True)
    return ranked


def score_retrieve_entry(
    prompt: str,
    entity_key: str,
    label: str,
    hints: List[str],
    semantics: Optional[Dict[str, Any]] = None,
    catalog: Optional[List[Dict[str, Any]]] = None,
    *,
    connector_type: Optional[str] = None,
    labeling_status: Optional[str] = None,
    context_hints: Optional[List[str]] = None,
) -> EntityScoreResult:
    """Score a catalog row for binding shortlist ranking."""
    return score_catalog_entity(
        entity_key=entity_key,
        label=label,
        prompt=prompt,
        hints=hints,
        semantics=semantics,
        catalog=catalog,
        connector_type=connector_type,
        labeling_status=labeling_status,
        context_hints=context_hints,
    )


def can_auto_pin_entity(ranked_scores: List[float]) -> bool:
    if not ranked_scores:
        return False
    if ranked_scores[0] < AUTO_PIN_MIN_SCORE:
        return False
    if len(ranked_scores) < 2:
        return True
    return ranked_scores[0] - ranked_scores[1] >= AUTO_PIN_GAP
