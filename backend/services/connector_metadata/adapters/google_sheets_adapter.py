"""Google Sheets metadata sync (header schema + hash)."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from db.connection import database
from services.connector_metadata.drift import compute_drift_from_maps, fingerprint_sheets_header
from services.connector_metadata.models import DriftReport
from services.labeling.stale_detection import collect_entities_needing_labeling, hashes_for_keys
from services.labeling.schedule import schedule_entity_labeling_fire_and_forget

logger = logging.getLogger(__name__)


def _sheets_connection_id(connector_ref: Dict[str, Any]) -> str:
    ref = connector_ref or {}
    return str(ref.get("connection_id") or ref.get("google_sheets_connection_id") or "")


class GoogleSheetsMetadataAdapter:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def list_entity_ids(self, connector_ref: Dict[str, Any]) -> List[str]:
        conn_id = _sheets_connection_id(connector_ref)
        if not conn_id:
            return []
        rows = await database.fetch_all(
            """
            SELECT spreadsheet_id, sheet_name FROM gsheets_extracted_schemas
            WHERE user_id = :user_id AND google_sheets_connection_id = :conn_id
              AND COALESCE(is_deleted, FALSE) = FALSE
            """,
            {"user_id": self.user_id, "conn_id": conn_id},
        )
        out = []
        for r in rows or []:
            row = dict(r)
            sid, sheet = row.get("spreadsheet_id"), row.get("sheet_name")
            if sid and sheet:
                out.append(f"{sid}::{sheet}")
        return out

    async def sync_schema(
        self,
        connector_ref: Dict[str, Any],
        *,
        sync_mode: str,
        on_progress: Optional[Callable[..., Any]] = None,
    ) -> DriftReport:
        # Sheets schema refresh is driven by spreadsheet re-sync elsewhere.
        # v1: recompute hashes from cached headers only.
        conn_id = _sheets_connection_id(connector_ref)
        if not conn_id:
            return DriftReport()
        rows = await database.fetch_all(
            """
            SELECT spreadsheet_id, sheet_name, header_schema, schema_hash
            FROM gsheets_extracted_schemas
            WHERE user_id = :user_id AND google_sheets_connection_id = :conn_id
            """,
            {"user_id": self.user_id, "conn_id": conn_id},
        )
        previous: Dict[str, Optional[str]] = {}
        current: Dict[str, str] = {}
        for r in rows or []:
            row = dict(r)
            sid, sheet = row.get("spreadsheet_id"), row.get("sheet_name")
            if not sid or not sheet:
                continue
            eid = f"{sid}::{sheet}"
            previous[eid] = row.get("schema_hash")
            raw = row.get("header_schema")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = {}
            h = fingerprint_sheets_header(raw if isinstance(raw, dict) else {})
            current[eid] = h
            await database.execute(
                """
                UPDATE gsheets_extracted_schemas
                SET schema_hash = :schema_hash, schema_synced_at = NOW(), is_deleted = FALSE, deleted_at = NULL
                WHERE user_id = :user_id AND google_sheets_connection_id = :conn_id
                  AND spreadsheet_id = :sid AND sheet_name = :sheet
                """,
                {
                    "user_id": self.user_id,
                    "conn_id": conn_id,
                    "sid": sid,
                    "sheet": sheet,
                    "schema_hash": h,
                },
            )

        drift = compute_drift_from_maps(previous_hashes=previous, current_hashes=current)

        if sync_mode != "schema_only":
            drift_keys = drift.entities_needing_categorization(sync_mode)
            to_label = await collect_entities_needing_labeling(
                user_id=self.user_id,
                connector_type="google_sheets",
                connector_instance_id=conn_id,
                drift_keys=drift_keys,
                current_hashes=current,
            )
            if to_label:
                if on_progress:
                    await on_progress(
                        percent=90,
                        message=f"Queued labeling for {len(to_label)} sheet(s)",
                    )
                schedule_entity_labeling_fire_and_forget(
                    user_id=self.user_id,
                    connector_type="google_sheets",
                    connector_ref={"connection_id": conn_id},
                    entity_keys=to_label,
                    entity_hashes=hashes_for_keys(to_label, current),
                )

        if on_progress:
            await on_progress(percent=100, message="Sheet schema hashes updated")

        return drift
