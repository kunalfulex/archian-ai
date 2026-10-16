"""Backfill entity labeling for existing connectors."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from db.connection import database
from services.labeling.schedule import schedule_entity_labeling

logger = logging.getLogger(__name__)


async def list_sql_connectors(user_id: str) -> List[Dict[str, str]]:
    rows = await database.fetch_all(
        """
        SELECT id::text AS connector_id FROM sql_connectors
        WHERE user_id = :user_id AND is_active = TRUE
        """,
        {"user_id": str(user_id)},
    )
    return [{"connector_id": dict(r)["connector_id"]} for r in rows or [] if dict(r).get("connector_id")]


async def list_salesforce_connectors(user_id: str) -> List[Dict[str, str]]:
    rows = await database.fetch_all(
        """
        SELECT id::text AS connector_id, org_id::text AS org_id
        FROM salesforce_tokens
        WHERE user_id = :user_id
        """,
        {"user_id": str(user_id)},
    )
    return [
        {"connector_id": dict(r)["connector_id"], "org_id": dict(r)["org_id"]}
        for r in rows or []
        if dict(r).get("connector_id") and dict(r).get("org_id")
    ]


async def list_gsheet_connections(user_id: str) -> List[Dict[str, str]]:
    rows = await database.fetch_all(
        """
        SELECT id::text AS connection_id FROM google_sheets_connections
        WHERE user_id = :user_id
        """,
        {"user_id": str(user_id)},
    )
    return [{"connection_id": dict(r)["connection_id"]} for r in rows or [] if dict(r).get("connection_id")]


async def pending_sql_tables(user_id: str, connector_id: str) -> List[str]:
    rows = await database.fetch_all(
        """
        SELECT ses.table_name
        FROM sql_extracted_schemas ses
        LEFT JOIN connector_entity_semantics ces
          ON ces.user_id = ses.user_id
         AND ces.connector_type = 'sql'
         AND ces.connector_instance_id = ses.connector_id::text
         AND ces.entity_key = ses.table_name
        WHERE ses.user_id = :user_id
          AND ses.connector_id = :connector_id
          AND COALESCE(ses.is_deleted, FALSE) = FALSE
          AND (ces.labeling_status IS NULL OR ces.labeling_status IN ('pending', 'failed'))
        ORDER BY ses.table_name
        """,
        {"user_id": str(user_id), "connector_id": connector_id},
    )
    return [dict(r)["table_name"] for r in rows or [] if dict(r).get("table_name")]


async def pending_sf_objects(user_id: str, connector_id: str, org_id: str) -> List[str]:
    rows = await database.fetch_all(
        """
        SELECT o.object_api_name
        FROM salesforce_objects o
        LEFT JOIN connector_entity_semantics ces
          ON ces.user_id = o.user_id
         AND ces.connector_type = 'salesforce'
         AND ces.connector_instance_id = :connector_id
         AND ces.entity_key = o.object_api_name
        WHERE o.user_id = :user_id
          AND o.org_id = :org_id
          AND COALESCE(o.is_deleted, FALSE) = FALSE
          AND (ces.labeling_status IS NULL OR ces.labeling_status IN ('pending', 'failed'))
        ORDER BY o.object_api_name
        """,
        {
            "user_id": str(user_id),
            "connector_id": connector_id,
            "org_id": org_id,
        },
    )
    return [dict(r)["object_api_name"] for r in rows or [] if dict(r).get("object_api_name")]


async def pending_gsheet_tabs(user_id: str, connection_id: str) -> List[str]:
    rows = await database.fetch_all(
        """
        SELECT ges.spreadsheet_id, ges.sheet_name
        FROM gsheets_extracted_schemas ges
        LEFT JOIN connector_entity_semantics ces
          ON ces.user_id = ges.user_id
         AND ces.connector_type = 'google_sheets'
         AND ces.connector_instance_id = ges.google_sheets_connection_id::text
         AND ces.entity_key = ges.spreadsheet_id || '::' || ges.sheet_name
        WHERE ges.user_id = :user_id
          AND ges.google_sheets_connection_id = :connection_id
          AND COALESCE(ges.is_deleted, FALSE) = FALSE
          AND (ces.labeling_status IS NULL OR ces.labeling_status IN ('pending', 'failed'))
        ORDER BY ges.spreadsheet_name, ges.sheet_name
        """,
        {"user_id": str(user_id), "connection_id": connection_id},
    )
    keys = []
    for r in rows or []:
        row = dict(r)
        sid, sheet = row.get("spreadsheet_id"), row.get("sheet_name")
        if sid and sheet:
            keys.append(f"{sid}::{sheet}")
    return keys


async def backfill_user_labeling(user_id: str) -> Dict[str, Any]:
    """Queue background labeling for all pending entities for one user."""
    jobs: List[str] = []

    for ref in await list_sql_connectors(user_id):
        cid = ref["connector_id"]
        keys = await pending_sql_tables(user_id, cid)
        if keys:
            job_id = await schedule_entity_labeling(
                user_id=user_id,
                connector_type="sql",
                connector_ref={"connector_id": cid},
                entity_keys=keys,
            )
            if job_id:
                jobs.append(job_id)

    for ref in await list_salesforce_connectors(user_id):
        connector_id = ref["connector_id"]
        org_id = ref["org_id"]
        keys = await pending_sf_objects(user_id, connector_id, org_id)
        if keys:
            job_id = await schedule_entity_labeling(
                user_id=user_id,
                connector_type="salesforce",
                connector_ref={"connector_id": connector_id, "org_id": org_id},
                entity_keys=keys,
            )
            if job_id:
                jobs.append(job_id)

    for ref in await list_gsheet_connections(user_id):
        conn_id = ref["connection_id"]
        keys = await pending_gsheet_tabs(user_id, conn_id)
        if keys:
            job_id = await schedule_entity_labeling(
                user_id=user_id,
                connector_type="google_sheets",
                connector_ref={"connection_id": conn_id},
                entity_keys=keys,
            )
            if job_id:
                jobs.append(job_id)

    return {"user_id": user_id, "jobs_queued": len(jobs), "job_ids": jobs}
