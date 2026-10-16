"""Validate connector refs and graph member payloads."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from uuid import UUID

from fastapi import HTTPException

from db.connection import database
from services.enterprise_graph.config import (
    CONNECTOR_TYPES,
    MEMBER_ROLES,
    max_members_per_graph,
    min_members_per_graph,
)


def connector_ref_for_type(connector_type: str, connector_id: str) -> Dict[str, str]:
    from services.enterprise_graph.connector_ref import connector_ref_for_storage

    if not (connector_id or "").strip():
        raise ValueError("connector_id is required")
    return connector_ref_for_storage(connector_type, connector_id)


def connector_id_from_ref(connector_type: str, connector_ref: Dict[str, Any]) -> str:
    ct = (connector_type or "").strip().lower()
    ref = connector_ref or {}
    if ct == "salesforce":
        return str(ref.get("connector_id") or ref.get("token_id") or ref.get("org_id") or "")
    if ct == "sql":
        return str(ref.get("connector_id") or "")
    if ct == "google_sheets":
        return str(ref.get("google_sheets_connection_id") or ref.get("connection_id") or "")
    return ""


def validate_connector_type(connector_type: str) -> str:
    ct = (connector_type or "").strip().lower()
    if ct not in CONNECTOR_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"connector_type must be one of: {', '.join(sorted(CONNECTOR_TYPES))}",
        )
    return ct


def validate_member_role(role: str) -> str:
    r = (role or "source").strip().lower()
    if r not in MEMBER_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of: {', '.join(sorted(MEMBER_ROLES))}",
        )
    return r


def member_ref_key(connector_type: str, connector_ref: Dict[str, Any]) -> Tuple[str, str]:
    """Stable key for duplicate detection."""
    import json

    return connector_type, json.dumps(connector_ref or {}, sort_keys=True)


async def assert_connector_owned(user_id: UUID, connector_type: str, connector_id: str) -> None:
    ct = validate_connector_type(connector_type)
    uid = str(user_id)
    if ct == "salesforce":
        from services.connectors.salesforce_ref import resolve_salesforce_token

        row = await resolve_salesforce_token(uid, connector_id)
        if not row:
            raise HTTPException(status_code=404, detail="Salesforce org not found")
        return
    if ct == "sql":
        row = await database.fetch_one(
            """
            SELECT 1 FROM sql_connectors
            WHERE id = :id AND user_id = :user_id AND is_active = true LIMIT 1
            """,
            {"id": connector_id, "user_id": uid},
        )
        if not row:
            raise HTTPException(status_code=404, detail="SQL connector not found")
        return
    row = await database.fetch_one(
        """
        SELECT 1 FROM google_sheets_connections
        WHERE id = :id AND user_id = :user_id LIMIT 1
        """,
        {"id": connector_id, "user_id": uid},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Google Sheets connection not found")


async def resolve_display_name(
    user_id: UUID,
    connector_type: str,
    connector_id: str,
    explicit: str | None,
) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    ct = validate_connector_type(connector_type)
    uid = str(user_id)
    if ct == "salesforce":
        from services.connectors.salesforce_ref import resolve_salesforce_token

        token = await resolve_salesforce_token(uid, connector_id)
        if token:
            return token.get("display_name") or connector_id
    if ct == "sql":
        row = await database.fetch_one(
            """
            SELECT name, host, database_name FROM sql_connectors
            WHERE id = :id AND user_id = :user_id LIMIT 1
            """,
            {"id": connector_id, "user_id": uid},
        )
        if row:
            d = dict(row)
            return d.get("name") or f"{d.get('host')}/{d.get('database_name')}" or connector_id
    row = await database.fetch_one(
        """
        SELECT connection_name, google_account_email FROM google_sheets_connections
        WHERE id = :id AND user_id = :user_id LIMIT 1
        """,
        {"id": connector_id, "user_id": uid},
    )
    if row:
        d = dict(row)
        return d.get("connection_name") or d.get("google_account_email") or connector_id
    return connector_id


def validate_member_list_size(members: List[Any]) -> None:
    if len(members) > max_members_per_graph():
        raise HTTPException(
            status_code=400,
            detail=f"A graph may have at most {max_members_per_graph()} connectors.",
        )


def validate_member_list_for_run(members: List[Any]) -> None:
    validate_member_list_size(members)
    if len(members) < min_members_per_graph():
        raise HTTPException(
            status_code=400,
            detail=f"Add at least {min_members_per_graph()} connectors before running an assessment.",
        )


async def validate_lake_member_constraints(
    user_id: str,
    members: List[Dict[str, Any]],
) -> None:
    """At most one lake member; lake must be a Snowflake SQL connector."""
    lake_members = [m for m in members if (m.get("role") or "").lower() == "lake"]
    if len(lake_members) > 1:
        raise HTTPException(
            status_code=400,
            detail="A graph may have at most one lake member.",
        )
    for m in lake_members:
        ct = (m.get("connector_type") or "").strip().lower()
        if ct != "sql":
            raise HTTPException(
                status_code=400,
                detail="Lake member must use a SQL connector (Snowflake).",
            )
        connector_id = connector_id_from_ref(ct, m.get("connector_ref") or {})
        if not connector_id:
            raise HTTPException(status_code=400, detail="Lake member connector_id is required.")
        row = await database.fetch_one(
            """
            SELECT connector_type FROM sql_connectors
            WHERE id = :id AND user_id = :user_id AND is_active = true
            LIMIT 1
            """,
            {"id": connector_id, "user_id": str(user_id)},
        )
        if not row:
            raise HTTPException(status_code=404, detail="Lake SQL connector not found.")
        subtype = (dict(row).get("connector_type") or "").strip().lower()
        if subtype != "snowflake":
            raise HTTPException(
                status_code=400,
                detail="Lake member SQL connector must be Snowflake.",
            )


def check_duplicate_members(
    members: List[Dict[str, Any]],
) -> None:
    seen: set[Tuple[str, str]] = set()
    for m in members:
        ct = m.get("connector_type", "")
        ref = m.get("connector_ref") or {}
        key = member_ref_key(ct, ref)
        if key in seen:
            raise HTTPException(
                status_code=400,
                detail="Duplicate connector in graph member list.",
            )
        seen.add(key)
