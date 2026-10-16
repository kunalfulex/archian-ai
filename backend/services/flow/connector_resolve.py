"""Resolve stale flow source connectors after users reconnect orgs."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from db.connection import database
from fastapi import HTTPException

logger = logging.getLogger(__name__)


async def update_flow_schema_connector_id(
    flow_id: str,
    user_id: str,
    connector_id: str,
) -> None:
    await database.execute(
        """
        UPDATE extracted_schemas
        SET connector_id = :connector_id
        WHERE migration_flow_id = :flow_id AND user_id = :user_id
        """,
        {
            "connector_id": str(connector_id),
            "flow_id": str(flow_id),
            "user_id": str(user_id),
        },
    )


def _connection_stale_detail(
    *,
    message: str,
    side: str,
    connector_type: str,
    source_name: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "code": "CONNECTION_STALE",
        "message": message,
        "side": side,
        "connector_type": connector_type,
        "source_name": source_name,
        "flow_id": flow_id,
    }


async def resolve_salesforce_flow_source_token(
    *,
    flow_id: str,
    user_id: UUID,
    token_id: Optional[str],
    source_name: Optional[str],
) -> Dict[str, Any]:
    """
    Resolve Salesforce source token for a saved flow.
    Falls back to ``source_name`` (org display name) and remaps schema connector_id.
    """
    from services.connectors.salesforce_ref import (
        resolve_salesforce_token_by_display_name,
        resolve_salesforce_token_row,
    )

    uid = str(user_id)
    row: Optional[Dict[str, Any]] = None

    if token_id:
        row = await resolve_salesforce_token_row(uid, str(token_id))

    resolved_by_name = False
    if not row and source_name:
        by_name = await resolve_salesforce_token_by_display_name(uid, source_name)
        if by_name:
            row = await resolve_salesforce_token_row(uid, by_name["connector_id"])
            resolved_by_name = bool(row)

    if not row or not row.get("org_id"):
        raise HTTPException(
            status_code=404,
            detail=_connection_stale_detail(
                message=(
                    "Salesforce connection for this flow is no longer available. "
                    "Edit the flow connections and select your current org."
                ),
                side="source",
                connector_type="salesforce",
                source_name=source_name,
                flow_id=str(flow_id),
            ),
        )

    if resolved_by_name:
        try:
            await update_flow_schema_connector_id(flow_id, uid, str(row["id"]))
            logger.info(
                "Remapped flow %s Salesforce source connector to token %s (matched %r)",
                flow_id,
                row["id"],
                source_name,
            )
        except Exception as exc:
            logger.warning("Failed to persist remapped Salesforce connector: %s", exc)

    return row


async def resolve_sql_flow_connector(
    *,
    flow_id: str,
    user_id: UUID,
    connector_id: Optional[str],
    source_name: Optional[str],
) -> str:
    """Return SQL connector id; fallback match by connector name."""
    from services.sql_connector_service import SQLConnectorService

    uid = str(user_id)
    service = SQLConnectorService()

    if connector_id:
        data = await service.get_connector_by_id(connector_id, user_id)
        if data:
            conn = data.get("connector")
            try:
                cid = dict(conn).get("id") if conn is not None else None
            except (TypeError, ValueError):
                cid = None
            if cid:
                return str(cid)

    name = (source_name or "").strip()
    if name:
        row = await database.fetch_one(
            """
            SELECT id FROM sql_connectors
            WHERE user_id = :user_id
              AND lower(trim(name)) = lower(trim(:name))
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"user_id": uid, "name": name},
        )
        if row:
            new_id = str(row["id"])
            try:
                await update_flow_schema_connector_id(flow_id, uid, new_id)
                logger.info(
                    "Remapped flow %s SQL source connector to %s (matched %r)",
                    flow_id,
                    new_id,
                    name,
                )
            except Exception as exc:
                logger.warning("Failed to persist remapped SQL connector: %s", exc)
            return new_id

    raise HTTPException(
        status_code=404,
        detail=_connection_stale_detail(
            message=(
                "SQL connection for this flow is no longer available. "
                "Edit the flow connections and select your current database."
            ),
            side="source",
            connector_type="sql",
            source_name=source_name,
            flow_id=str(flow_id),
        ),
    )
