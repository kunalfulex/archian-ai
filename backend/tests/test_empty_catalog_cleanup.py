"""Tests for hard-delete empty catalog entity cleanup."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from services.connector_metadata.empty_catalog_cleanup import (
    purge_salesforce_object_catalog_entry,
    purge_salesforce_objects_without_fields,
    purge_sql_table_catalog_entry,
    sql_schema_storable_column_count,
)


def test_sql_schema_storable_column_count_dict():
    schema = {
        "columns": [
            {"column_name": "id"},
            {"name": "email"},
            {"column_name": "  "},
        ]
    }
    assert sql_schema_storable_column_count(schema) == 2


def test_sql_schema_storable_column_count_json_string():
    schema = json.dumps({"columns": [{"column_name": "a"}]})
    assert sql_schema_storable_column_count(schema) == 1


def test_sql_schema_storable_column_count_empty():
    assert sql_schema_storable_column_count(None) == 0
    assert sql_schema_storable_column_count({"columns": []}) == 0
    assert sql_schema_storable_column_count("not-json") == 0


def test_purge_salesforce_object_catalog_entry_hard_deletes():
    async def _run():
        with patch(
            "services.connector_metadata.empty_catalog_cleanup.database"
        ) as db:
            db.fetch_one = AsyncMock(return_value={"connector_id": "tok-1"})
            db.execute = AsyncMock()

            await purge_salesforce_object_catalog_entry(
                "user-1", "org-1", "ContactCleanInfo"
            )

            assert db.execute.await_count == 4
            sql_batches = [call.args[0] for call in db.execute.await_args_list]
            assert all("DELETE FROM" in sql for sql in sql_batches)
            assert not any("is_deleted" in sql.lower() for sql in sql_batches)

    asyncio.run(_run())


def test_purge_salesforce_objects_without_fields():
    async def _run():
        with patch(
            "services.connector_metadata.empty_catalog_cleanup.database"
        ) as db, patch(
            "services.connector_metadata.empty_catalog_cleanup.purge_salesforce_object_catalog_entry",
            new_callable=AsyncMock,
        ) as purge_one:
            db.fetch_all = AsyncMock(
                return_value=[
                    {"object_api_name": "ContactCleanInfo"},
                    {"object_api_name": "EmptyObj"},
                ]
            )

            removed = await purge_salesforce_objects_without_fields("user-1", "org-1")

            assert removed == 2
            assert purge_one.await_count == 2

    asyncio.run(_run())


def test_purge_sql_table_catalog_entry_hard_deletes():
    async def _run():
        with patch(
            "services.connector_metadata.empty_catalog_cleanup.database"
        ) as db:
            db.execute = AsyncMock()

            await purge_sql_table_catalog_entry("user-1", "conn-1", "empty_table")

            assert db.execute.await_count == 2
            sql_batches = [call.args[0] for call in db.execute.await_args_list]
            assert all("DELETE FROM" in sql for sql in sql_batches)
            assert not any("is_deleted" in sql.lower() for sql in sql_batches)

    asyncio.run(_run())
