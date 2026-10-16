"""Detect when a Salesforce connector needs a friendly name or user description."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from db.connection import database
from services.connectors.salesforce_ref import resolve_salesforce_token
from services.labeling.store import LabelingStore

_EMAIL_LIKE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_generic_salesforce_display_name(
    display_name: Optional[str],
    salesforce_user_email: Optional[str] = None,
) -> bool:
    name = (display_name or "").strip()
    if not name:
        return True
    email = (salesforce_user_email or "").strip()
    if email and name.lower() == email.lower():
        return True
    if _EMAIL_LIKE.match(name):
        return True
    lower = name.lower()
    if lower.startswith("https://") or ".my.salesforce.com" in lower:
        return True
    return False


async def get_salesforce_token_meta(
    user_id: str,
    connector_instance_id: str,
) -> Optional[Dict[str, Any]]:
    token = await resolve_salesforce_token(user_id, connector_instance_id)
    if not token:
        return None
    row = await database.fetch_one(
        """
        SELECT id::text AS connector_id,
               org_id::text AS org_id,
               NULLIF(TRIM(org_display_name), '') AS org_display_name,
               NULLIF(TRIM(salesforce_user_email), '') AS salesforce_user_email,
               COALESCE(environment_type, 'production') AS environment_type
        FROM salesforce_tokens
        WHERE user_id = :user_id AND id::text = :connector_id
        LIMIT 1
        """,
        {"user_id": str(user_id), "connector_id": token["connector_id"]},
    )
    return dict(row) if row else None


async def get_connector_setup_status(
    *,
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
) -> Dict[str, Any]:
    if connector_type != "salesforce":
        return {
            "needs_connector_setup": False,
            "needs_org_name": False,
            "needs_org_description": False,
        }

    meta = await get_salesforce_token_meta(user_id, connector_instance_id)
    if not meta:
        return {
            "needs_connector_setup": False,
            "needs_org_name": False,
            "needs_org_description": False,
        }

    profile = await LabelingStore.get_business_profile(
        user_id=str(user_id),
        connector_type="salesforce",
        connector_instance_id=meta["connector_id"],
    )
    user_description = ((profile or {}).get("user_description") or "").strip()
    display_name = meta.get("org_display_name") or meta.get("salesforce_user_email") or ""

    needs_org_name = is_generic_salesforce_display_name(
        display_name,
        meta.get("salesforce_user_email"),
    )
    needs_org_description = not user_description
    needs_connector_setup = needs_org_name or needs_org_description

    return {
        "needs_connector_setup": needs_connector_setup,
        "needs_org_name": needs_org_name,
        "needs_org_description": needs_org_description,
        "org_display_name": meta.get("org_display_name"),
        "org_id": meta.get("org_id"),
        "salesforce_user_email": meta.get("salesforce_user_email"),
        "environment_type": meta.get("environment_type"),
        "setup_connections_path": (
            f"/connections?sfConnector={meta['connector_id']}&setup=1"
            if needs_connector_setup
            else None
        ),
    }


def build_setup_email_paragraph(*, setup_url: str) -> str:
    return (
        f"Take a moment to name this org and tell us what it's for — "
        f"Archian AI works better when it knows your context.\n\n"
        f"Open Connections to add a name and description:\n{setup_url}\n"
    )


def build_setup_email_paragraph_html(*, setup_url: str) -> str:
    from html import escape

    safe_url = escape(setup_url, quote=True)
    return (
        f"<p>Take a moment to name this org and tell us what it's for — "
        f"Archian AI works better when it knows your context.</p>"
        f'<p><a href="{safe_url}" style="color: #2563eb; text-decoration: underline;">'
        f"Open Connections</a> to add a name and description.</p>"
    )
