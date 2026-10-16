"""
Sanitize Salesforce extraction SOQL: keep extract queries source-shaped;
defer dedupe/validation to post-extract data_ops steps.
"""
from __future__ import annotations

import re
from typing import List, Optional


def prompt_requests_post_extract_data_ops(
    prompt: Optional[str] = None,
    classification: Optional[dict] = None,
    *,
    goal: Optional[dict] = None,
) -> bool:
    """True when PLAN_SCOPE indicates post-extract data work (not prompt keywords)."""
    from services.planning.goal_parser import (
        prompt_requests_post_extract_data_ops as _scope_based,
    )
    from services.planning.plan_scope import write_plan_scope

    cls = dict(classification or {})
    if goal:
        cls = write_plan_scope(cls, goal)
    return _scope_based(prompt, cls if cls else None)


def _split_select_from_soql(soql: str) -> Optional[tuple[str, str, str]]:
    m = re.search(
        r"^\s*SELECT\s+(?P<select>.+?)\s+FROM\s+(?P<rest>.+)$",
        (soql or "").strip(),
        re.I | re.S,
    )
    if not m:
        return None
    return m.group("select"), m.group("rest"), ""


def fix_duplicate_soql_select_aliases(soql: str) -> str:
    """Drop bare aliases when a relationship field already selects the same leaf (e.g. Phone vs Account.Phone)."""
    parsed = _split_select_from_soql(soql)
    if not parsed:
        return soql
    select_part, from_rest, _suffix = parsed
    fields = [f.strip() for f in select_part.split(",") if f.strip()]
    rel_leaf_names = {
        f.split(".")[-1].strip().lower()
        for f in fields
        if "." in f
    }
    if not rel_leaf_names:
        return soql
    kept: List[str] = []
    for field in fields:
        if "." not in field and field.strip().lower() in rel_leaf_names:
            continue
        kept.append(field)
    if len(kept) == len(fields):
        return soql
    return f"SELECT {', '.join(kept)} FROM {from_rest}"


def _strip_where_group_by_from_soql(soql: str) -> str:
    """Keep SELECT … FROM object only; defer filters/aggregates to data_ops."""
    m = re.match(
        r"^(?P<head>\s*SELECT\s+.+?\s+FROM\s+\S+)\b",
        (soql or "").strip(),
        re.I | re.S,
    )
    if not m:
        return soql
    return m.group("head").strip()


def apply_soql_extraction_post_process(
    soql: str,
    prompt: Optional[str] = None,
    *,
    classification: Optional[dict] = None,
    goal: Optional[dict] = None,
) -> str:
    out = fix_duplicate_soql_select_aliases(soql)
    if prompt_requests_post_extract_data_ops(prompt, classification, goal=goal):
        out = _strip_where_group_by_from_soql(out)
        out = fix_duplicate_soql_select_aliases(out)
    return out
