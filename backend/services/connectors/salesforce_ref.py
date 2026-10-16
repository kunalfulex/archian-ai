"""Salesforce connector identity: public connector_id = salesforce_tokens.id."""
from __future__ import annotations

from typing import Any, Dict, Optional

from db.connection import database


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


async def resolve_salesforce_token_row(
    user_id: str,
    connector_ref: str,
) -> Optional[Dict[str, Any]]:
    """Full token row for extraction / flow rerun (id, org_id, instance_url, …)."""
    token = await resolve_salesforce_token(user_id, connector_ref)
    if not token:
        return None
    row = await database.fetch_one(
        """
        SELECT st.id, st.org_id, st.instance_url, st.connected_app_id,
               st.org_display_name, st.environment_type
        FROM salesforce_tokens st
        WHERE st.id = :token_id AND st.user_id = :user_id
        LIMIT 1
        """,
        {"token_id": token["connector_id"], "user_id": str(user_id)},
    )
    return _row_dict(row) if row else None


async def resolve_salesforce_token_by_display_name(
    user_id: str,
    display_name: str,
) -> Optional[Dict[str, Any]]:
    """Match a user's Salesforce org by saved flow source_name / org label."""
    name = (display_name or "").strip()
    if not name:
        return None
    uid = str(user_id)

    exact = await database.fetch_one(
        """
        SELECT id::text AS connector_id,
               org_id::text AS org_id,
               COALESCE(org_display_name, instance_url, 'Salesforce') AS display_name
        FROM salesforce_tokens
        WHERE user_id = :user_id
          AND (
            lower(trim(COALESCE(org_display_name, ''))) = lower(trim(:name))
            OR lower(trim(COALESCE(instance_url, ''))) = lower(trim(:name))
          )
        ORDER BY is_default DESC NULLS LAST, issued_at DESC
        LIMIT 1
        """,
        {"user_id": uid, "name": name},
    )
    if exact:
        return _row_dict(exact)

    rows = await database.fetch_all(
        """
        SELECT id::text AS connector_id,
               org_id::text AS org_id,
               COALESCE(org_display_name, instance_url, 'Salesforce') AS display_name
        FROM salesforce_tokens
        WHERE user_id = :user_id
          AND COALESCE(org_display_name, '') ILIKE :like
        ORDER BY is_default DESC NULLS LAST, issued_at DESC
        LIMIT 2
        """,
        {"user_id": uid, "like": f"%{name}%"},
    )
    if rows and len(rows) == 1:
        return _row_dict(rows[0])
    return None


async def resolve_salesforce_token(
    user_id: str,
    connector_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Resolve connector_id (token UUID or legacy org_id) to a token row.
    Returns connector_id (token id), org_id, display_name.
    """
    uid = str(user_id)
    ref = (connector_id or "").strip()
    if not ref:
        return None

    row = await database.fetch_one(
        """
        SELECT id::text AS connector_id,
               org_id::text AS org_id,
               COALESCE(org_display_name, instance_url, 'Salesforce') AS display_name
        FROM salesforce_tokens
        WHERE user_id = :user_id AND id::text = :ref
        ORDER BY issued_at DESC
        LIMIT 1
        """,
        {"user_id": uid, "ref": ref},
    )
    if row:
        return _row_dict(row)

    row = await database.fetch_one(
        """
        SELECT id::text AS connector_id,
               org_id::text AS org_id,
               COALESCE(org_display_name, instance_url, 'Salesforce') AS display_name
        FROM salesforce_tokens
        WHERE user_id = :user_id AND org_id = :ref
        ORDER BY is_default DESC NULLS LAST, issued_at DESC
        LIMIT 1
        """,
        {"user_id": uid, "ref": ref},
    )
    if row:
        return _row_dict(row)
    return None


def enriched_salesforce_connector_ref(token: Dict[str, Any]) -> Dict[str, str]:
    return {
        "connector_id": str(token["connector_id"]),
        "org_id": str(token["org_id"]),
    }


async def normalize_salesforce_connector_ref(
    user_id: str,
    connector_ref: Dict[str, Any],
) -> Dict[str, str]:
    """Ensure connector_ref has token connector_id and org_id for schema joins."""
    ref = dict(connector_ref or {})
    cid = ref.get("connector_id") or ref.get("token_id")
    if cid:
        token = await resolve_salesforce_token(user_id, str(cid))
        if token:
            return enriched_salesforce_connector_ref(token)
    org_id = ref.get("org_id")
    if org_id:
        token = await resolve_salesforce_token(user_id, str(org_id))
        if token:
            return enriched_salesforce_connector_ref(token)
    raise ValueError("Salesforce connector not found")


async def resolve_salesforce_org_id(user_id: str, connector_id: str) -> Optional[str]:
    token = await resolve_salesforce_token(user_id, connector_id)
    if not token:
        return None
    return str(token.get("org_id") or "")


def salesforce_public_connector_id(connector_ref: Dict[str, Any]) -> str:
    ref = connector_ref or {}
    return str(
        ref.get("connector_id")
        or ref.get("token_id")
        or ref.get("org_id")
        or ""
    )


def salesforce_org_id_from_ref(connector_ref: Dict[str, Any]) -> str:
    org_id = (connector_ref or {}).get("org_id")
    if not org_id:
        raise ValueError("org_id missing from Salesforce connector_ref")
    return str(org_id)
