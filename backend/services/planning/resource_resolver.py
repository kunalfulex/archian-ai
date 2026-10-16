"""Catalog phrase matching utilities (no prompt keyword routing on the planning path)."""
from __future__ import annotations

import re
from typing import List, Optional


def normalize_salesforce_phrase(phrase: str) -> str:
    t = (phrase or "").strip()
    lowered = t.lower()
    for suffix in (
        " salesforce org",
        " salesforce",
        " org",
        " organization",
    ):
        if lowered.endswith(suffix):
            t = t[: -len(suffix)].strip()
            lowered = t.lower()
    return t


def normalize_sql_connection_phrase(text: str) -> str:
    """Strip connector noise so 'Test Database' and 'Test DB' normalize alike."""
    t = (text or "").strip().lower()
    for token in (
        " database",
        " databas",
        " db",
        " sql",
        " connector",
        " datasource",
    ):
        t = t.replace(token, " ")
    return " ".join(t.split()).strip()


def match_sql_connection_in_phrase(phrase: str, sql_names: List[str]) -> Optional[str]:
    """Fuzzy match user phrasing to a SQL connector display name."""
    phrase_n = normalize_sql_connection_phrase(phrase)
    if not phrase_n:
        return None
    for name in sorted(sql_names, key=len, reverse=True):
        nl = normalize_sql_connection_phrase(name)
        if not nl:
            continue
        if nl == phrase_n:
            return name
        if len(nl) < 4 and nl in phrase_n and nl != phrase_n:
            continue
        if nl in phrase_n or phrase_n in nl:
            return name
        phrase_words = phrase_n.split()
        name_words = nl.split()
        if phrase_words and name_words:
            short, long = (
                (phrase_words, name_words)
                if len(phrase_words) <= len(name_words)
                else (name_words, phrase_words)
            )
            long_set = set(long)
            if short != long and all(w in long_set for w in short):
                return name
    return None


def match_salesforce_in_phrase(phrase: str, sf_names: List[str]) -> Optional[str]:
    phrase_l = normalize_salesforce_phrase(phrase).lower()
    if not phrase_l:
        return None
    if re.search(r"\b(spreadsheet|workbook|sheet)\b", phrase_l, re.I):
        return None
    for name in sorted(sf_names, key=len, reverse=True):
        nl = name.lower()
        if nl == phrase_l:
            return name
        if len(nl) < 8 and nl in phrase_l and nl != phrase_l:
            continue
        if nl in phrase_l or phrase_l in nl:
            return name
    return None


def _normalize_workbook_phrase(text: str) -> str:
    t = (text or "").strip().lower()
    for suffix in (" spreadsheet", " spread sheet", " sheet", " sheets", " workbook"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t


def workbook_mentioned_as_destination(name: str, prompt: Optional[str]) -> bool:
    """True when a catalog workbook name appears as export/migrate target, not source."""
    pl = (prompt or "").strip().lower()
    nl = (name or "").strip().lower()
    norm = _normalize_workbook_phrase(nl)
    if not pl or not norm:
        return False

    tail = pl.split(" to ", 1)[-1] if " to " in pl else ""
    tail_norm = _normalize_workbook_phrase(tail)
    if tail and (norm in tail_norm or nl in tail or tail_norm in norm or norm in tail):
        return True

    for pattern in (
        rf"\bexport(?:\s+(?:it|them|data|records?|contacts?)){{0,4}}?\s+to\s+.{{0,60}}?{re.escape(norm)}",
        rf"\b(?:migrate|move|load|push|send|import)\s+.+?\s+to\s+.{{0,60}}?{re.escape(norm)}",
        rf"\bto\s+{re.escape(norm)}(?:\s+(?:sheet|spreadsheet|workbook))?\b",
        rf"\bmap(?:ping)?(?:\s+(?:the\s+)?data)?(?:\s+\w+){{0,10}}?\s+to\s+.{{0,60}}?{re.escape(norm)}",
    ):
        if re.search(pattern, pl, re.I):
            return True
    return False
