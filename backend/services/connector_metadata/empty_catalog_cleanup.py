"""Remove catalog entities that have no storable fields/columns (hard delete, not soft-delete)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from db.connection import database


def sql_schema_storable_column_count(schema_data: Any) -> int:
    """Count columns with a non-empty name in SQL schema_data."""
    if not isinstance(schema_data, dict):
        if isinstance(schema_data, str):
            try:
                schema_data = json.loads(schema_data)
            except (json.JSONDecodeError, TypeError):
                return 0
        else:
            return 0
    cols = schema_data.get("columns") or []
    if not isinstance(cols, list):
        return 0
    count = 0
    for col in cols:
        if isinstance(col, dict):
            name = (col.get("column_name") or col.get("name") or "").strip()
        elif isinstance(col, str):
            name = col.strip()
        else:
            name = ""
        if name:
            count += 1
    return count


async def _salesforce_connector_instance_id(user_id: str, org_id: str) -> Optional[str]:
    row = await database.fetch_one(
        """
        SELECT id::text AS connector_id FROM salesforce_tokens
        WHERE user_id = :user_id AND org_id = :org_id
        ORDER BY issued_at DESC LIMIT 1
        """,
        {"user_id": str(user_id), "org_id": org_id},
    )
    if not row:
        return None
    return dict(row).get("connector_id")


async def purge_salesforce_object_catalog_entry(
    user_id: str,
    org_id: str,
    object_api_name: str,
) -> None:
    """Hard-delete one SF object and related rows when it has no usable catalog fields."""
    obj = (object_api_name or "").strip()
    if not obj:
        return
    uid = str(user_id)
    params = {"user_id": uid, "org_id": org_id, "obj": obj}

    await database.execute(
        """
        DELETE FROM salesforce_object_record_types
        WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
        """,
        params,
    )
    await database.execute(
        """
        DELETE FROM salesforce_object_fields
        WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
        """,
        params,
    )
    connector_id = await _salesforce_connector_instance_id(uid, org_id)
    if connector_id:
        await database.execute(
            """
            DELETE FROM connector_entity_semantics
            WHERE user_id = :user_id
              AND connector_type = 'salesforce'
              AND connector_instance_id = :connector_id
              AND entity_key = :obj
            """,
            {"user_id": uid, "connector_id": connector_id, "obj": obj},
        )
    await database.execute(
        """
        DELETE FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
        """,
        params,
    )


async def purge_salesforce_objects_without_fields(user_id: str, org_id: str) -> int:
    """Delete all SF objects in an org that have zero rows in salesforce_object_fields."""
    uid = str(user_id)
    rows = await database.fetch_all(
        """
        SELECT so.object_api_name
        FROM salesforce_objects so
        WHERE so.user_id = :user_id AND so.org_id = :org_id
          AND NOT EXISTS (
            SELECT 1 FROM salesforce_object_fields f
            WHERE f.user_id = so.user_id
              AND f.org_id = so.org_id
              AND f.object_api_name = so.object_api_name
          )
        """,
        {"user_id": uid, "org_id": org_id},
    )
    names = [
        dict(r).get("object_api_name")
        for r in rows or []
        if dict(r).get("object_api_name")
    ]
    for name in names:
        await purge_salesforce_object_catalog_entry(uid, org_id, name)
    return len(names)


async def purge_sql_table_catalog_entry(
    user_id: str,
    connector_id: str,
    table_name: str,
) -> None:
    """Hard-delete a SQL table row and labeling metadata when it has no columns."""
    table = (table_name or "").strip()
    if not table:
        return
    uid = str(user_id)
    cid = str(connector_id)
    await database.execute(
        """
        DELETE FROM connector_entity_semantics
        WHERE user_id = :user_id
          AND connector_type = 'sql'
          AND connector_instance_id = :connector_id
          AND entity_key = :table_name
        """,
        {"user_id": uid, "connector_id": cid, "table_name": table},
    )
    await database.execute(
        """
        DELETE FROM sql_extracted_schemas
        WHERE user_id = :user_id AND connector_id = :connector_id AND table_name = :table_name
        """,
        {"user_id": uid, "connector_id": cid, "table_name": table},
    )


async def purge_sql_tables_without_columns(user_id: str, connector_id: str) -> int:
    """Delete SQL catalog tables whose schema_data has no named columns."""
    uid = str(user_id)
    cid = str(connector_id)
    rows = await database.fetch_all(
        """
        SELECT table_name, schema_data
        FROM sql_extracted_schemas
        WHERE user_id = :user_id AND connector_id = :connector_id
        """,
        {"user_id": uid, "connector_id": cid},
    )
    removed = 0
    for row in rows or []:
        d = dict(row)
        table = (d.get("table_name") or "").strip()
        if not table:
            continue
        if sql_schema_storable_column_count(d.get("schema_data")) == 0:
            await purge_sql_table_catalog_entry(uid, cid, table)
            removed += 1
    return removed


async def purge_gsheets_tab_without_headers(
    user_id: str,
    connection_id: str,
    spreadsheet_id: str,
    sheet_name: str,
) -> None:
    """Hard-delete a sheet tab catalog row with no header columns."""
    uid = str(user_id)
    entity_key = f"{spreadsheet_id}::{sheet_name}"
    await database.execute(
        """
        DELETE FROM connector_entity_semantics
        WHERE user_id = :user_id
          AND connector_type = 'google_sheets'
          AND connector_instance_id = :connection_id
          AND entity_key = :entity_key
        """,
        {
            "user_id": uid,
            "connection_id": str(connection_id),
            "entity_key": entity_key,
        },
    )
    await database.execute(
        """
        DELETE FROM gsheets_extracted_schemas
        WHERE user_id = :user_id
          AND google_sheets_connection_id = :connection_id
          AND spreadsheet_id = :spreadsheet_id
          AND sheet_name = :sheet_name
        """,
        {
            "user_id": uid,
            "connection_id": str(connection_id),
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
        },
    )
