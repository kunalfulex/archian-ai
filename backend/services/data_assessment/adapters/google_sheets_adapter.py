from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from db.connection import database
from services.data_assessment.config import DEFAULT_SAMPLE_ROW_CAP
from services.data_assessment.models import EntityRef, GapFinding, ObjectProfile
from services.data_assessment.streaming_profiler import profile_rows_single_async
from utils.google_sheets_auth import get_valid_google_sheets_token
from services.google_sheets_service import GoogleSheetsService

logger = logging.getLogger(__name__)


class GoogleSheetsAssessmentAdapter:
    connector_type = "google_sheets"

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def list_assessable_entities(
        self,
        scope: str,
        connector_ref: Dict[str, Any],
        entity_list: Optional[List[str]] = None,
    ) -> List[EntityRef]:
        conn_id = connector_ref.get("google_sheets_connection_id")
        if not conn_id:
            return []
        rows = await database.fetch_all(
            """
            SELECT spreadsheet_id, spreadsheet_name, sheet_name
            FROM user_google_spreadsheets
            WHERE user_id = :user_id
              AND google_sheets_connection_id = :conn_id
            ORDER BY spreadsheet_name, sheet_name
            """,
            {"user_id": self.user_id, "conn_id": conn_id},
        )
        out: List[EntityRef] = []
        for r in rows or []:
            row = dict(r)
            sid = row.get("spreadsheet_id")
            sheet = row.get("sheet_name")
            if not sid or not sheet:
                continue
            eid = f"{sid}::{sheet}"
            if entity_list is not None and eid not in entity_list:
                continue
            label = f"{row.get('spreadsheet_name') or sid} / {sheet}"
            out.append(
                EntityRef(
                    entity_id=eid,
                    label=label,
                    meta={"spreadsheet_id": sid, "sheet_name": sheet},
                )
            )
        if scope == "quick":
            return out[:10]
        return out

    async def profile_entity(
        self,
        entity: EntityRef,
        connector_ref: Dict[str, Any],
        options: Dict[str, Any],
        cancel_check: Callable[[], Awaitable[bool]],
    ) -> ObjectProfile:
        row_cap = int(options.get("sample_row_cap") or DEFAULT_SAMPLE_ROW_CAP)
        spreadsheet_id = entity.meta.get("spreadsheet_id") or entity.entity_id.split("::")[0]
        sheet_name = entity.meta.get("sheet_name") or entity.entity_id.split("::")[-1]

        try:
            conn_id = connector_ref.get("google_sheets_connection_id")
            token = await get_valid_google_sheets_token(self.user_id, conn_id)
            if not token:
                return ObjectProfile(
                    entity_id=entity.entity_id,
                    label=entity.label,
                    records_sampled=0,
                    quality_report={},
                    dimension_scores={},
                    error="Google Sheets not connected",
                )
            svc = GoogleSheetsService(
                token["access_token"],
                token.get("refresh_token") or "",
                token.get("expires_at"),
            )
            if await cancel_check():
                return await profile_rows_single_async(entity.entity_id, entity.label, [])

            result = await svc.read_spreadsheet(
                spreadsheet_id,
                range_name="A1:ZZ",
                sheet_name=sheet_name,
                max_rows=row_cap,
            )
            if not result.get("success"):
                return ObjectProfile(
                    entity_id=entity.entity_id,
                    label=entity.label,
                    records_sampled=0,
                    quality_report={},
                    dimension_scores={},
                    error=result.get("error") or "Failed to read sheet",
                )
            rows = result.get("data") or []
            return await profile_rows_single_async(entity.entity_id, entity.label, rows[:row_cap])
        except Exception as exc:
            logger.exception("Sheets assessment failed %s", entity.entity_id)
            return ObjectProfile(
                entity_id=entity.entity_id,
                label=entity.label,
                records_sampled=0,
                quality_report={},
                dimension_scores={},
                error=str(exc),
            )

    async def metadata_gaps(
        self,
        connector_ref: Dict[str, Any],
        entities: List[EntityRef],
        entity_profiles: Optional[List[Dict[str, Any]]] = None,
    ) -> List[GapFinding]:
        from services.data_assessment.metadata_gap_analyzer import analyze_sheets_gaps

        conn_id = connector_ref.get("google_sheets_connection_id")
        if not conn_id:
            return []
        return await analyze_sheets_gaps(
            self.user_id,
            conn_id,
            entities,
            entity_profiles=entity_profiles,
        )
