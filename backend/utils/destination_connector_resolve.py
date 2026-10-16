"""Resolve Salesforce tokens and SQL connectors by id or fuzzy display name."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from services.planning.resource_resolver import (
    match_salesforce_in_phrase,
    match_sql_connection_in_phrase,
)


def _looks_like_uuid(value: Optional[str]) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    try:
        UUID(raw)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


async def resolve_salesforce_token_record(
    user_id: Any,
    *,
    destination_name: Optional[str] = None,
    token_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Lookup salesforce_tokens by token id, exact org_display_name, then fuzzy phrase match.
    """
    from db.connection import database

    uid = str(user_id)
    if token_id and _looks_like_uuid(token_id):
        row = await database.fetch_one(
            """
            SELECT id, org_id, org_display_name, connected_app_id
            FROM salesforce_tokens
            WHERE user_id = :user_id AND id = :token_id
            LIMIT 1
            """,
            values={"user_id": uid, "token_id": str(token_id)},
        )
        if row:
            return _row_dict(row)

    name = (destination_name or "").strip()
    if not name or name.lower() == "unknown":
        return None

    row = await database.fetch_one(
        """
        SELECT id, org_id, org_display_name, connected_app_id
        FROM salesforce_tokens
        WHERE user_id = :user_id
          AND LOWER(TRIM(org_display_name)) = LOWER(TRIM(:destination_name))
        ORDER BY is_default DESC NULLS LAST, issued_at DESC
        LIMIT 1
        """,
        values={"user_id": uid, "destination_name": name},
    )
    if row:
        return _row_dict(row)

    all_rows = await database.fetch_all(
        """
        SELECT id, org_id, org_display_name, connected_app_id, is_default
        FROM salesforce_tokens
        WHERE user_id = :user_id
        ORDER BY is_default DESC NULLS LAST, issued_at DESC
        """,
        values={"user_id": uid},
    )
    tokens = [_row_dict(r) for r in (all_rows or [])]
    names = [str(t.get("org_display_name") or "").strip() for t in tokens if t.get("org_display_name")]
    hit = match_salesforce_in_phrase(name, names)
    if not hit:
        return None
    for t in tokens:
        if (t.get("org_display_name") or "").strip() == hit:
            print(
                f"[SF_RESOLVE] Fuzzy org match: {name!r} → {hit!r} (token {t.get('id')})"
            )
            return t
    return None


async def resolve_sql_connector_record(
    user_id: Any,
    *,
    destination_name: Optional[str] = None,
    connector_id: Optional[str] = None,
    active_only: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Lookup sql_connectors by id, exact name, then fuzzy phrase match.
    """
    from db.connection import database

    uid = str(user_id)
    if connector_id and _looks_like_uuid(connector_id):
        row = await database.fetch_one(
            """
            SELECT id, name, connector_type
            FROM sql_connectors
            WHERE id = :connector_id AND user_id = :user_id
            LIMIT 1
            """,
            values={"connector_id": str(connector_id), "user_id": uid},
        )
        if row:
            return _row_dict(row)

    name = (destination_name or "").strip()
    if not name or name.lower() == "unknown":
        return None

    active_clause = "AND is_active = true" if active_only else ""
    row = await database.fetch_one(
        f"""
        SELECT id, name, connector_type
        FROM sql_connectors
        WHERE user_id = :user_id
          AND LOWER(TRIM(name)) = LOWER(TRIM(:name))
          {active_clause}
        LIMIT 1
        """,
        values={"user_id": uid, "name": name},
    )
    if row:
        return _row_dict(row)

    all_rows = await database.fetch_all(
        f"""
        SELECT id, name, connector_type
        FROM sql_connectors
        WHERE user_id = :user_id
          {active_clause}
        ORDER BY created_at DESC
        """,
        values={"user_id": uid},
    )
    connectors = [_row_dict(r) for r in (all_rows or [])]
    names = [str(c.get("name") or "").strip() for c in connectors if c.get("name")]
    hit = match_sql_connection_in_phrase(name, names)
    if not hit:
        return None
    for c in connectors:
        if (c.get("name") or "").strip() == hit:
            print(
                f"[SQL_RESOLVE] Fuzzy connector match: {name!r} → {hit!r} (id {c.get('id')})"
            )
            return c
    return None
