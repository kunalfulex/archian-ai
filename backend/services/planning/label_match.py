"""Phrase scoring against connector profiles and entity semantics."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


def normalize_phrase(text: Optional[str]) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    for suffix in (
        " salesforce org",
        " salesforce",
        " org",
        " organization",
        " database",
        " db",
        " spreadsheet",
        " workbook",
        " google sheet",
        " google sheets",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2]


def _phrase_hits_term(phrase: str, term: str) -> bool:
    p = normalize_phrase(phrase)
    t = normalize_phrase(term)
    if not p or not t:
        return False
    if p == t or t in p or p in t:
        return True
    pt, tt = set(_tokens(p)), set(_tokens(t))
    if not pt or not tt:
        return False
    overlap = pt & tt
    if len(overlap) >= min(2, len(pt), len(tt)):
        return True
    if len(pt) == 1 and pt <= tt:
        return True
    return False


@dataclass
class ConnectorLabelCandidate:
    connector_type: str
    display_name: str
    connector_id: Optional[str] = None
    score: float = 0.0
    match_reasons: List[str] = field(default_factory=list)
    profile_synopsis: Optional[str] = None
    business_domains: List[str] = field(default_factory=list)


def score_connector_for_phrase(
    phrase: str,
    *,
    connector_type: str,
    display_name: str,
    connector_id: Optional[str],
    profile: Optional[Any] = None,
    entity_summaries: Optional[List[Dict[str, Any]]] = None,
) -> ConnectorLabelCandidate:
    """Score how well a user phrase matches a connector's labels."""
    candidate = ConnectorLabelCandidate(
        connector_type=connector_type,
        display_name=display_name,
        connector_id=connector_id,
    )
    if not normalize_phrase(phrase):
        return candidate

    if _phrase_hits_term(phrase, display_name):
        candidate.score += 120
        candidate.match_reasons.append(f"display_name:{display_name}")

    if profile:
        candidate.profile_synopsis = getattr(profile, "synopsis", None)
        candidate.business_domains = list(getattr(profile, "business_domains", None) or [])
        for syn in list(getattr(profile, "synonyms", None) or []):
            if _phrase_hits_term(phrase, str(syn)):
                candidate.score += 80
                candidate.match_reasons.append(f"synonym:{syn}")
        for dom in candidate.business_domains:
            if _phrase_hits_term(phrase, str(dom)):
                candidate.score += 60
                candidate.match_reasons.append(f"domain:{dom}")
        for role in list(getattr(profile, "system_roles", None) or []):
            if _phrase_hits_term(phrase, str(role)):
                candidate.score += 40
                candidate.match_reasons.append(f"role:{role}")
        synopsis = (candidate.profile_synopsis or "").strip()
        if synopsis and _phrase_hits_term(phrase, synopsis[:120]):
            candidate.score += 25
            candidate.match_reasons.append("profile_synopsis")

    for ent in entity_summaries or []:
        sem = ent.get("semantics") if isinstance(ent.get("semantics"), dict) else {}
        primary = sem.get("primary") if isinstance(sem.get("primary"), dict) else {}
        category = str(primary.get("category") or "").strip()
        if category and _phrase_hits_term(phrase, category):
            candidate.score += 35
            candidate.match_reasons.append(f"entity:{ent.get('entity_key')}")
        for key in ("synonyms", "domains", "processes"):
            for val in sem.get(key) or []:
                if _phrase_hits_term(phrase, str(val)):
                    candidate.score += 30
                    candidate.match_reasons.append(f"entity_{key}:{val}")
                    break

    return candidate


def pick_connector_candidates(
    phrase: str,
    scored: List[ConnectorLabelCandidate],
    *,
    min_score: float = 45.0,
    ambiguity_gap: float = 15.0,
) -> Tuple[Optional[ConnectorLabelCandidate], List[ConnectorLabelCandidate]]:
    """
    Return (winner, ambiguous_list).
    ambiguous_list is non-empty when top scores are too close to auto-pick.
    """
    ranked = sorted(
        [c for c in scored if c.score >= min_score],
        key=lambda c: c.score,
        reverse=True,
    )
    if not ranked:
        return None, []
    if len(ranked) == 1:
        return ranked[0], []
    top, second = ranked[0], ranked[1]
    if top.score - second.score >= ambiguity_gap:
        return top, []
    return None, ranked[:5]


def match_entity_in_prompt(
    prompt: str,
    entity_summaries: List[Dict[str, Any]],
) -> Optional[str]:
    """Best entity_key whose labels appear in the user prompt (unified entity scorer)."""
    from services.planning.entity_scoring import match_entity_in_prompt as _unified_match

    return _unified_match(prompt, entity_summaries)


def extract_phrases_from_prompt(prompt: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Deprecated — use PLAN_SCOPE ``source_phrase`` / ``destination_phrase`` instead."""
    del prompt
    return None, None
