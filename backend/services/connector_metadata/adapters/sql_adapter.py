"""SQL connector metadata sync — schema drift; labeling runs in background."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from db.connection import database
from services.connector_metadata.drift import compute_drift_from_maps, fingerprint_sql_schema
from services.connector_metadata.models import DriftReport
from services.labeling.stale_detection import collect_entities_needing_labeling, hashes_for_keys
from services.labeling.schedule import schedule_entity_labeling_fire_and_forget
from services.sql_background_processing_service import SQLBackgroundProcessingService
from services.sql_connector_service import SQLConnectorFactory

logger = logging.getLogger(__name__)


class SqlMetadataAdapter:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._bg = SQLBackgroundProcessingService()

    async def list_entity_ids(self, connector_ref: Dict[str, Any]) -> List[str]:
        connector_id = connector_ref["connector_id"]
        rows = await database.fetch_all(
            """
            SELECT table_name FROM sql_extracted_schemas
            WHERE user_id = :user_id AND connector_id = :connector_id
              AND COALESCE(is_deleted, FALSE) = FALSE
            ORDER BY table_name
            """,
            {"user_id": self.user_id, "connector_id": connector_id},
        )
        return [dict(r)["table_name"] for r in rows or [] if dict(r).get("table_name")]

    async def _load_previous_hashes(self, connector_id: str) -> Dict[str, Optional[str]]:
        rows = await database.fetch_all(
            """
            SELECT table_name, schema_hash FROM sql_extracted_schemas
            WHERE user_id = :user_id AND connector_id = :connector_id
            """,
            {"user_id": self.user_id, "connector_id": connector_id},
        )
        return {dict(r)["table_name"]: dict(r).get("schema_hash") for r in rows or [] if dict(r).get("table_name")}

    async def sync_schema(
        self,
        connector_ref: Dict[str, Any],
        *,
        sync_mode: str,
        on_progress: Optional[Callable[..., Any]] = None,
    ) -> DriftReport:
        connector_id = connector_ref["connector_id"]
        connector = await self._bg.get_connector_details(connector_id, self.user_id)
        if not connector:
            raise ValueError("SQL connector not found")

        previous_hashes = await self._load_previous_hashes(connector_id)
        connector_config = self._bg.prepare_connector_config(connector)
        connector_instance = SQLConnectorFactory.create_connector(
            connector["connector_type"],
            connector_config,
        )

        try:
            tables = await connector_instance.get_tables()
        except Exception as exc:
            await connector_instance.close_connection()
            raise exc

        current_hashes: Dict[str, str] = {}
        total = len(tables)
        for idx, table_name in enumerate(tables):
            if on_progress:
                await on_progress(
                    percent=int((idx / total) * 80) if total else 0,
                    message=f"Syncing schema for {table_name}",
                )
            schema_data = await connector_instance.get_table_schema(table_name)
            from services.connector_metadata.empty_catalog_cleanup import (
                purge_sql_table_catalog_entry,
                sql_schema_storable_column_count,
            )

            if sql_schema_storable_column_count(schema_data) == 0:
                await purge_sql_table_catalog_entry(self.user_id, connector_id, table_name)
                continue
            h = fingerprint_sql_schema(schema_data)
            current_hashes[table_name] = h
            await self._upsert_table_schema(connector_id, table_name, schema_data, h)

        await connector_instance.close_connection()

        from services.connector_metadata.empty_catalog_cleanup import (
            purge_sql_table_catalog_entry,
            purge_sql_tables_without_columns,
        )

        await purge_sql_tables_without_columns(self.user_id, connector_id)

        current_set = set(current_hashes.keys())
        stored_ids = set(previous_hashes.keys())
        for removed in stored_ids - current_set:
            await purge_sql_table_catalog_entry(
                self.user_id, connector_id, removed
            )

        drift = compute_drift_from_maps(previous_hashes=previous_hashes, current_hashes=current_hashes)
        to_categorize = drift.entities_needing_categorization(sync_mode)

        if to_categorize and sync_mode != "schema_only":
            to_label = await collect_entities_needing_labeling(
                user_id=self.user_id,
                connector_type="sql",
                connector_instance_id=connector_id,
                drift_keys=to_categorize,
                current_hashes=current_hashes,
            )
            if to_label:
                if on_progress:
                    await on_progress(
                        percent=90,
                        message=f"Queued labeling for {len(to_label)} table(s)",
                    )
                schedule_entity_labeling_fire_and_forget(
                    user_id=self.user_id,
                    connector_type="sql",
                    connector_ref={"connector_id": connector_id},
                    entity_keys=to_label,
                    entity_hashes=hashes_for_keys(to_label, current_hashes),
                )

        await database.execute(
            """
            UPDATE sql_connectors
            SET analysis_completed = TRUE, analysis_error = NULL, updated_at = NOW()
            WHERE id = :connector_id AND user_id = :user_id
            """,
            {"connector_id": connector_id, "user_id": self.user_id},
        )

        if on_progress:
            await on_progress(percent=100, message="Schema sync complete")

        return drift

    async def _upsert_table_schema(
        self,
        connector_id: str,
        table_name: str,
        schema_data: Dict[str, Any],
        schema_hash: str,
    ) -> None:
        existing = await database.fetch_one(
            """
            SELECT id FROM sql_extracted_schemas
            WHERE connector_id = :connector_id AND table_name = :table_name AND user_id = :user_id
            """,
            {"connector_id": connector_id, "table_name": table_name, "user_id": self.user_id},
        )
        if existing:
            await database.execute(
                """
                UPDATE sql_extracted_schemas
                SET schema_data = CAST(:schema_data AS jsonb),
                    schema_hash = :schema_hash,
                    schema_synced_at = NOW()
                WHERE id = :id
                """,
                {
                    "id": existing["id"],
                    "schema_data": json.dumps(schema_data),
                    "schema_hash": schema_hash,
                },
            )
        else:
            await database.execute(
                """
                INSERT INTO sql_extracted_schemas (
                    id, user_id, connector_id, table_name, schema_data,
                    ai_categorization, schema_hash, schema_synced_at
                ) VALUES (
                    :id, :user_id, :connector_id, :table_name, CAST(:schema_data AS jsonb),
                    NULL, :schema_hash, NOW()
                )
                """,
                {
                    "id": str(uuid.uuid4()),
                    "user_id": self.user_id,
                    "connector_id": connector_id,
                    "table_name": table_name,
                    "schema_data": json.dumps(schema_data),
                    "schema_hash": schema_hash,
                },
            )
