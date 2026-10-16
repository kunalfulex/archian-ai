"""Select Google Sheets columns when the user asks for specific fields only."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

# User phrasing → normalized concept keys used for header matching.
_FIELD_CONCEPT_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("first_name", re.compile(r"\bfirst[\s_-]*names?\b|\bfirstnames?\b", re.I)),
    ("last_name", re.compile(r"\blast[\s_-]*names?\b|\blastnames?\b", re.I)),
    ("email", re.compile(r"\bemails?\b|\be[\s_-]*mails?\b", re.I)),
    ("phone", re.compile(r"\bphones?\b|\bmobiles?\b|\bcell[\s_-]*phones?\b", re.I)),
    ("company", re.compile(r"\bcompan(?:y|ies)\b|\borganizations?\b|\borg(?:s)?\b", re.I)),
    ("title", re.compile(r"\btitles?\b|\bjob[\s_-]*titles?\b", re.I)),
    ("name", re.compile(r"\bfull[\s_-]*names?\b|\bnames?\b", re.I)),
    ("id", re.compile(r"\bids?\b|\brecord[\s_-]*ids?\b", re.I)),
]

# Concept key → header normalization tokens (order matters — more specific first).
_HEADER_TOKENS: Dict[str, List[str]] = {
    "first_name": ["firstname", "first", "fname", "givenname", "given"],
    "last_name": ["lastname", "last", "lname", "surname", "familyname", "family"],
    "email": ["email", "emailaddress", "mail", "emailid"],
    "phone": ["phone", "mobile", "cell", "telephone", "phonenumber"],
    "company": ["company", "organization", "org", "account", "accountname"],
    "title": ["title", "jobtitle", "position", "role"],
    "name": ["fullname", "name", "contactname"],
    "id": ["id", "recordid", "externalid", "uuid"],
}


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def prompt_requests_specific_columns(prompt: str) -> bool:
    """True when the user likely wants a subset of columns, not the full sheet."""
    if not (prompt or "").strip():
        return False
    pl = prompt.lower()
    if re.search(r"\bsame\s+(?:fields?|columns?)\b", pl):
        return True
    if re.search(r"\b(only|just)\b.+\b(fields?|columns?|name|email)\b", pl):
        return True
    if re.search(r"\b(?:like|such as)\s+[^.;]{3,80}\b(from|and)\b", pl):
        return True
    # e.g. "first name, last name and email"
    if re.search(r"\bfirst[\s_-]*name\b", pl) and re.search(r"\blast[\s_-]*name\b", pl):
        return True
    if re.search(r"\bfields?\s+(?:like|such as|including)\b", pl):
        return True
    return False


def extract_field_concepts_from_prompt(prompt: str) -> List[str]:
    """
    Parse requested field concepts from natural language (e.g. first_name, email).
    Handles multi-source prompts by reading the full prompt, including "same fields".
    """
    if not (prompt or "").strip():
        return []

    text = prompt
    pl = prompt.lower()

    # Focus on the segment before "same fields" when present — it lists the canonical fields.
    same_idx = pl.find("same fields")
    if same_idx < 0:
        same_idx = pl.find("same columns")
    if same_idx > 0:
        text = prompt[:same_idx]

    concepts: List[str] = []
    seen: set[str] = set()
    for concept, pattern in _FIELD_CONCEPT_PATTERNS:
        if pattern.search(text) and concept not in seen:
            concepts.append(concept)
            seen.add(concept)

    # "name" alone is ambiguous when first/last already captured — drop generic name.
    if "first_name" in seen and "last_name" in seen and "name" in seen:
        concepts = [c for c in concepts if c != "name"]

    return concepts


def _score_header_for_concept(header: str, concept: str) -> int:
    norm = _normalize_token(header)
    if not norm:
        return 0
    tokens = _HEADER_TOKENS.get(concept, [_normalize_token(concept)])
    best = 0
    for token in tokens:
        if norm == token:
            best = max(best, 100)
        elif norm.startswith(token) or token.startswith(norm):
            best = max(best, 80)
        elif token in norm or norm in token:
            best = max(best, 60)
    return best


def match_headers_to_concepts(
    headers: Sequence[str], concepts: Sequence[str]
) -> List[str]:
    """Map requested concepts to actual spreadsheet column names (best match each)."""
    if not headers or not concepts:
        return []

    available = [h for h in headers if str(h).strip()]
    matched: List[str] = []
    used_headers: set[str] = set()

    for concept in concepts:
        scored: List[Tuple[int, str]] = []
        for header in available:
            if header in used_headers:
                continue
            score = _score_header_for_concept(str(header), concept)
            if score >= 60:
                scored.append((score, header))
        if not scored:
            continue
        scored.sort(key=lambda x: (-x[0], available.index(x[1])))
        chosen = scored[0][1]
        matched.append(chosen)
        used_headers.add(chosen)

    return matched


def build_keep_only_transformation_rules(columns: Sequence[str]) -> Dict[str, Any]:
    cols = [c for c in columns if str(c).strip()]
    return {
        "transformation_rules": {
            "fields": [{"action": "keep_only", "fields": cols}],
        }
    }


def restrict_dataframe_to_prompt_columns(
    df: pd.DataFrame,
    prompt: str,
    *,
    source_name: Optional[str] = None,
    min_matched_columns: int = 2,
) -> Tuple[pd.DataFrame, Optional[List[str]]]:
    """
    When the prompt asks for specific fields, keep only matching sheet columns.

    Returns (possibly filtered dataframe, list of kept column names or None if unchanged).
    """
    if df is None or df.empty:
        return df, None
    if not prompt_requests_specific_columns(prompt):
        return df, None

    concepts = extract_field_concepts_from_prompt(prompt)
    if not concepts:
        return df, None

    headers = [str(c) for c in df.columns]
    matched = match_headers_to_concepts(headers, concepts)

    # Require a meaningful match — avoids over-pruning on vague prompts.
    if len(matched) < min(min_matched_columns, len(concepts)):
        return df, None

    kept = [h for h in headers if h in matched]
    if not kept or len(kept) >= len(headers):
        return df, None

    return df[kept].copy(), kept
