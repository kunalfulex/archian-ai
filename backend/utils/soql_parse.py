"""
Lightweight SOQL parsing for connection validation (main SELECT/FROM, field candidates).
Not a full SOQL parser — handles common extraction queries without subquery FROM confusion.
"""
from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple


def split_outer_select_clause(soql: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Split main SELECT list and base SObject name. Stops at the first FROM at paren depth 0
    after the leading SELECT (so nested subqueries in SELECT do not terminate early).
    Returns (select_clause, object_api_name) or (None, None) if parsing fails.
    """
    s = str(soql).strip()
    if not s:
        return None, None
    m = re.match(r"(?is)^\s*select\s+", s)
    if not m:
        return None, None
    i = m.end()
    depth = 0
    select_start = i
    in_quote = False
    quote_ch = ""
    while i < len(s):
        ch = s[i]
        if in_quote:
            if ch == quote_ch and (i == 0 or s[i - 1] != "\\"):
                in_quote = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_quote = True
            quote_ch = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and s[i : i + 4].lower() == "from" and (i == 0 or not s[i - 1].isalnum()):
            rest = s[i + 4 :].lstrip()
            mobj = re.match(r"^([a-zA-Z][a-zA-Z0-9_]*)", rest)
            if mobj:
                return s[select_start:i].strip(), mobj.group(1)
            return s[select_start:i].strip(), None
        i += 1
    return None, None


def _strip_soql_alias(expr: str) -> str:
    e = str(expr).strip()
    e = re.sub(r"\s+AS\s+[\w\.]+\s*$", "", e, flags=re.IGNORECASE)
    return e.strip()


def field_candidates_from_select_clause(select_clause: str) -> List[str]:
    """
    From the main SELECT list, collect field name candidates (including custom __c endings).
    Handles comma splits with respect to parentheses and string literals.
    """
    if not select_clause:
        return []
    clause = select_clause
    clause = re.sub(r"(?is)TYPEOF\s+.+?\s+END", "", clause)
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    in_quote = False
    quote_ch = ""
    i = 0
    while i < len(clause):
        ch = clause[i]
        if in_quote:
            if ch == quote_ch:
                in_quote = False
            cur.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            in_quote = True
            quote_ch = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if cur:
        parts.append("".join(cur).strip())
    out: List[str] = []
    seen = set()
    for raw in parts:
        raw = raw.strip()
        if not raw:
            continue
        expr = _strip_soql_alias(raw)
        if not expr:
            continue
        fm = re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\((.+)\)$", expr)
        if fm:
            inner = fm.group(1).strip()
            if inner and "," not in inner:
                expr = inner
        if re.match(r"^select\s+", expr, re.IGNORECASE):
            continue
        if "." in expr:
            out.append(expr.split(".")[-1])
            if expr not in seen:
                seen.add(expr)
                out.append(expr)
        else:
            if expr not in seen:
                seen.add(expr)
                out.append(expr)
    dedup: List[str] = []
    s2 = set()
    for x in out:
        k = x.lower()
        if k not in s2:
            s2.add(k)
            dedup.append(x)
    return dedup


def parse_soql_object_and_field_candidates(soql: str) -> Tuple[Optional[str], List[str]]:
    """Return base object API name and field name candidates to check against describe/synced metadata."""
    sel, obj = split_outer_select_clause(soql)
    if not sel or not obj:
        return None, []
    return obj, field_candidates_from_select_clause(sel)


def flatten_soql_select_item_to_extracted_key(item: str) -> str:
    """
    Derive extraction key from a SOQL select item without aliases (same flattening as extractors).

    Examples:
      - FirstName -> FirstName
      - Account.Name -> AccountName
      - Owner.Profile.Name -> OwnerProfileName
    """
    t = str(item or "").strip()
    if not t:
        return ""
    parts = [p.strip() for p in t.split(".") if p.strip()]
    if not parts:
        return t
    if len(parts) == 1:
        return parts[0]
    return "".join(parts)


def split_select_and_suffix(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Split outer SELECT list text and the rest of the statement starting at FROM (SOQL or SQL).
    Returns (select_list_inner, suffix_from_FROM) e.g. ('Id, Name', 'FROM Contact WHERE ...').
    """
    s = str(query).strip()
    if not s:
        return None, None
    m = re.match(r"(?is)^\s*select\s+", s)
    if not m:
        return None, None
    i = m.end()
    depth = 0
    select_start = i
    in_quote = False
    quote_ch = ""
    while i < len(s):
        ch = s[i]
        if in_quote:
            if ch == quote_ch and (i == 0 or s[i - 1] != "\\"):
                in_quote = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_quote = True
            quote_ch = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and s[i : i + 4].lower() == "from" and (i == 0 or not s[i - 1].isalnum()):
            return s[select_start:i].strip(), s[i:].lstrip()
        i += 1
    return None, None


def raw_select_parts(select_clause: str) -> List[str]:
    """Comma-split the SELECT list (respects parens and string literals)."""
    if not select_clause:
        return []
    clause = select_clause.strip()
    clause = re.sub(r"(?is)TYPEOF\s+.+?\s+END", "", clause)
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    in_quote = False
    quote_ch = ""
    i = 0
    while i < len(clause):
        ch = clause[i]
        if in_quote:
            if ch == quote_ch:
                in_quote = False
            cur.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            in_quote = True
            quote_ch = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _primary_sql_select_result_key(item: str) -> Optional[str]:
    """
    Result column label for one SELECT list item (same rules as JDBC-style keys:
    AS alias wins; else last segment of table.column; else expression / aggregate rules).
    """
    expr = item.strip()
    if not expr:
        return None
    m = re.search(
        r"(?is)\s+AS\s+((?:\"[^\"]+\"|`[^`]+`|[a-zA-Z_][\w]*))\s*$",
        expr,
    )
    if m:
        raw_alias = m.group(1).strip()
        if len(raw_alias) >= 2 and raw_alias[0] == raw_alias[-1] == '"':
            return raw_alias[1:-1]
        if len(raw_alias) >= 2 and raw_alias[0] == raw_alias[-1] == "`":
            return raw_alias[1:-1]
        return raw_alias
    core = re.sub(r"(?is)\s+AS\s+.+$", "", expr).strip().strip('"').strip("`")
    if not core:
        return None
    if "(" in core:
        inner_m = re.match(r"^[a-zA-Z_][\w]*\((.+)\)$", core)
        if inner_m:
            inner = inner_m.group(1).strip().strip('"').strip("`")
            if inner and "," not in inner and "." in inner:
                return inner.split(".")[-1].strip().strip('"').strip("`")
            if inner:
                return inner
            return core
        return core
    if "." in core:
        return core.split(".")[-1].strip().strip('"').strip("`")
    return core


def _sql_select_item_sync_match_keys(item: str) -> Set[str]:
    """
    Lowercase keys under which a SELECT item satisfies a mapped extracted_field_name.

    Includes the JDBC-style result label (so ``users.last_name AS LastName`` matches ``LastName``),
    the unaliased physical column tail when an AS is present (matches ``last_name``), and the
    legacy ``_simple_select_item_field_key`` token for parity with older queries.
    """
    keys: Set[str] = set()
    prim = _primary_sql_select_result_key(item)
    if prim:
        keys.add(prim.lower())
    expr = item.strip()
    if re.search(
        r"(?is)\s+AS\s+((?:\"[^\"]+\"|`[^`]+`|[a-zA-Z_][\w]*))\s*$",
        expr,
    ):
        core = re.sub(r"(?is)\s+AS\s+.+$", "", expr).strip().strip('"').strip("`")
        if core and "(" not in core:
            if "." in core:
                keys.add(core.split(".")[-1].strip().strip('"').strip("`").lower())
            else:
                keys.add(core.lower())
    sk = _simple_select_item_field_key(item)
    if sk:
        keys.add(sk.lower())
    return keys


def sql_select_result_column_keys(query: str) -> List[str]:
    """
    Ordered result column labels for a top-level SELECT ... FROM ... SQL statement.

    Uses the same SELECT-list splitting as raw_select_parts. For each item:
    - If the item ends with ``AS alias``, the result key is that alias (quoted identifiers supported).
    - Otherwise, for ``table.column`` the key is the last segment (typical driver label).
    - Otherwise the stripped expression is used.

    This is used to align migration ``extracted_field_name`` with the keys produced by the
    reverse-leg SQL extractor (aliases, casing, and ``table.col`` projection).
    """
    select_clause, _suffix = split_select_and_suffix(query or "")
    if not select_clause:
        return []
    out: List[str] = []
    for item in raw_select_parts(select_clause):
        k = _primary_sql_select_result_key(item)
        if k:
            out.append(k)
    return out


def _simple_select_item_field_key(expr: str) -> Optional[str]:
    """
    For a single SELECT item, return a comparable field name (last segment) if it looks like
    a plain field or relationship path; else None (aggregates, subqueries, etc.).
    """
    e = _strip_soql_alias(str(expr).strip())
    if not e:
        return None
    if re.match(r"^select\s+", e, re.IGNORECASE):
        return None
    fm = re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\((.+)\)$", e)
    if fm:
        inner = fm.group(1).strip()
        if inner and "," not in inner:
            e = inner
        else:
            return None
    if "(" in e:
        return None
    if "." in e:
        return e.split(".")[-1].strip()
    return e.strip()


# First token after FROM must not be these (unquoted) — e.g. "FROM LIMIT 1" is invalid.
_INVALID_FIRST_FROM_TOKEN = frozenset(
    {
        "limit",
        "order",
        "group",
        "offset",
        "having",
        "union",
        "where",
        "inner",
        "left",
        "right",
        "full",
        "cross",
        "join",
        "straight",
    }
)


def validate_select_query_for_mapping(query: str) -> Optional[str]:
    """
    Structural validation before sync or remote smoke tests: single SELECT ... FROM ...,
    non-empty select list, and a real table/object (or subquery) after FROM.

    Returns None if the shape looks OK; otherwise a short user-facing error string.
    """
    s = str(query).strip().rstrip(";")
    if not s:
        return "Query cannot be empty."
    sel, suffix = split_select_and_suffix(s)
    if sel is None or suffix is None:
        return (
            "Could not parse the query. Use one SELECT ... FROM ... statement "
            "with matching parentheses and quotes."
        )
    inner_check = sel.strip()
    if inner_check.upper().startswith("DISTINCT "):
        inner_check = inner_check[9:].strip()
    if not inner_check:
        return "The SELECT list cannot be empty."
    if inner_check == "*":
        return "Replace SELECT * with an explicit column list before saving."

    suf = suffix.strip()
    if not re.match(r"(?is)^from\s+", suf):
        return "Could not find a valid FROM clause after the SELECT list."

    rest = re.sub(r"(?is)^from\s+", "", suf).strip()
    if not rest:
        return "Missing table or Salesforce object name after FROM."

    if rest[0] == "(":
        return None
    if rest[0] in ('"', "'", "[", "`"):
        return None

    m = re.match(r"^[^\s,]+", rest)
    if not m:
        return "Invalid syntax after FROM."

    raw_tok = m.group(0)
    tok = raw_tok.lower()
    if tok.endswith(","):
        tok = tok[:-1].lower()
    tok = tok.strip("`\"[]")
    key = tok.split(".")[0] if tok else tok

    if key in _INVALID_FIRST_FROM_TOKEN:
        if key == "limit":
            return (
                "The query is missing a table or object name between FROM and LIMIT. "
                "Example: SELECT Id, Name FROM Contact LIMIT 10."
            )
        return (
            f"Invalid query: add a table or object name after FROM before {raw_tok.upper()}."
        )

    return None


_SOQL_CLAUSE_AFTER_OBJECT = re.compile(
    r"(?is)^(where|order\s+by|limit|group\s+by|offset|having|for|with|using)\b"
)


def validate_soql_from_clause_for_mapping(query: str) -> Optional[str]:
    """
    SOQL allows optional object aliases (e.g. FROM Contact c). For field-mapping flows we require
    FROM <Object> with nothing except SOQL clauses (WHERE, ORDER BY, LIMIT, …) after the object
    name, so stray text after the object is rejected before the org callout.
    """
    s = str(query).strip().rstrip(";")
    sel, suffix = split_select_and_suffix(s)
    if sel is None or suffix is None:
        return None
    suf = suffix.strip()
    rest = re.sub(r"(?is)^from\s+", "", suf).strip()
    if not rest or rest[0] == "(":
        return None
    if rest[0] in ('"', "'", "[", "`"):
        return None
    m_obj = re.match(r"^([a-zA-Z][a-zA-Z0-9_]*)\s*", rest)
    if not m_obj:
        return None
    after_obj = rest[m_obj.end() :].strip()
    if not after_obj:
        return None
    if _SOQL_CLAUSE_AFTER_OBJECT.match(after_obj):
        return None
    return (
        "After the object name in FROM, use only SOQL clauses (WHERE, ORDER BY, LIMIT, …) — "
        "not an extra label or typo. Example: SELECT Id FROM Contact WHERE LastName != null"
    )


def sync_select_query_with_source_fields(
    query: str,
    source_field_names: List[str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Rewrite the outer SELECT list so it includes all mapped source fields.

    For SQL, mapped names are **result column labels** (``AS`` aliases and ``table.col`` tails),
    same as ``sql_select_result_column_keys`` — not only the physical column name after stripping
    ``AS``. That way ``users.last_name AS LastName`` stays in the query when the mapping source
    field is ``LastName``.

    Drops simple field items that are no longer mapped; keeps aggregates and other non-simple items.
    Appends any mapped field name not already present.

    Returns (new_query, error_message). error_message set on failure (e.g. SELECT *).
    """
    sel, suffix = split_select_and_suffix(query)
    if sel is None or suffix is None:
        return None, "Could not parse the query; use a single SELECT ... FROM ... statement."

    inner_raw = sel.strip()
    had_distinct = inner_raw.upper().startswith("DISTINCT ")
    inner = inner_raw[9:].strip() if had_distinct else inner_raw
    if inner == "*":
        return None, (
            "SELECT * cannot be synced automatically. Replace * with an explicit column list, then save again."
        )

    req_lower = {str(n).strip().lower() for n in source_field_names if str(n).strip()}
    req_order: List[str] = []
    seen_l: set = set()
    for n in source_field_names:
        t = str(n).strip()
        if not t:
            continue
        tl = t.lower()
        if tl in seen_l:
            continue
        seen_l.add(tl)
        req_order.append(t)

    parts = raw_select_parts(inner)
    kept: List[str] = []
    covered_lower: set = set()

    for p in parts:
        if _simple_select_item_field_key(p) is None:
            kept.append(p)
            continue
        mkeys = _sql_select_item_sync_match_keys(p)
        if mkeys & req_lower:
            kept.append(p)
            covered_lower |= mkeys & req_lower

    for want in req_order:
        wl = want.lower()
        if wl not in covered_lower:
            kept.append(want)
            covered_lower.add(wl)

    new_inner = ", ".join(kept)
    if had_distinct:
        new_q = f"SELECT DISTINCT {new_inner} {suffix}".strip()
    else:
        new_q = f"SELECT {new_inner} {suffix}".strip()
    return new_q, None


def sync_select_query_with_field_items(
    query: str,
    field_items: List[str],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Replace the outer SELECT list with the given items (comma-separated), preserving DISTINCT
    and the rest of the statement (FROM …). Used when field expressions are fully derived
    (e.g. relationship paths + aliases for Salesforce mapping flows).
    """
    if not field_items:
        return None, "The SELECT list cannot be empty."
    sel, suffix = split_select_and_suffix(query)
    if sel is None or suffix is None:
        return None, "Could not parse the query; use a single SELECT ... FROM ... statement."

    inner_raw = sel.strip()
    had_distinct = inner_raw.upper().startswith("DISTINCT ")
    inner = inner_raw[9:].strip() if had_distinct else inner_raw
    if inner == "*":
        return None, (
            "SELECT * cannot be synced automatically. Replace * with an explicit column list, then save again."
        )

    cleaned = [str(x).strip() for x in field_items if str(x).strip()]
    if not cleaned:
        return None, "The SELECT list cannot be empty."

    new_inner = ", ".join(cleaned)
    if had_distinct:
        new_q = f"SELECT DISTINCT {new_inner} {suffix}".strip()
    else:
        new_q = f"SELECT {new_inner} {suffix}".strip()
    return new_q, None
