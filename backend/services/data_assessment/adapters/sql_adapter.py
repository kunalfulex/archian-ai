from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from services.data_assessment.config import ASSESSMENT_CHUNK_SIZE, DEFAULT_SAMPLE_ROW_CAP
from services.data_assessment.models import EntityRef, GapFinding, ObjectProfile
from services.data_assessment.streaming_profiler import (
    profile_rows_single_async,
    profile_rows_streaming_async,
)
from services.sql_connector_service import SQLConnectorFactory, SQLConnectorService

logger = logging.getLogger(__name__)


class SqlAssessmentAdapter:
    connector_type = "sql"

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._svc = SQLConnectorService()

    async def list_assessable_entities(
        self,
        scope: str,
        connector_ref: Dict[str, Any],
        entity_list: Optional[List[str]] = None,
    ) -> List[EntityRef]:
        connector_id = connector_ref["connector_id"]
        data = await self._svc.get_connector_by_id(connector_id, self.user_id)
        if not data:
            return []
        tables = data.get("tables") or []
        names = entity_list or [t["table_name"] for t in tables]
        if scope == "quick":
            names = names[:10]
        return [EntityRef(entity_id=n, label=n) for n in names if n]

    async def profile_entity(
        self,
        entity: EntityRef,
        connector_ref: Dict[str, Any],
        options: Dict[str, Any],
        cancel_check: Callable[[], Awaitable[bool]],
    ) -> ObjectProfile:
        connector_id = connector_ref["connector_id"]
        row_cap = int(options.get("sample_row_cap") or DEFAULT_SAMPLE_ROW_CAP)
        data = await self._svc.get_connector_by_id(connector_id, self.user_id)
        if not data:
            return ObjectProfile(
                entity_id=entity.entity_id,
                label=entity.label,
                records_sampled=0,
                quality_report={},
                dimension_scores={},
                error="SQL connector not found",
            )
        connector = dict(data["connector"])
        config = self._svc.prepare_connector_config(connector)
        table = entity.entity_id
        sql_connector = None
        try:
            sql_connector = SQLConnectorFactory.create_connector(
                connector["connector_type"], config
            )
            query = f'SELECT * FROM "{table}" LIMIT {row_cap}'
            if connector["connector_type"].lower() in ("mysql", "mariadb"):
                query = f"SELECT * FROM `{table}` LIMIT {row_cap}"
            elif connector["connector_type"].lower() in ("postgresql", "postgres"):
                query = f'SELECT * FROM "{table}" LIMIT {row_cap}'

            chunks: List[List[Dict[str, Any]]] = []
            total = 0
            async for batch in sql_connector.execute_query_iter_batches(
                query, batch_size=ASSESSMENT_CHUNK_SIZE
            ):
                if await cancel_check():
                    break
                chunks.append(batch)
                total += len(batch)
                if total >= row_cap:
                    break
            if not chunks:
                return await profile_rows_single_async(entity.entity_id, entity.label, [])
            return await profile_rows_streaming_async(entity.entity_id, entity.label, chunks)
        except Exception as exc:
            logger.exception("SQL assessment failed table=%s", entity.entity_id)
            return ObjectProfile(
                entity_id=entity.entity_id,
                label=entity.label,
                records_sampled=0,
                quality_report={},
                dimension_scores={},
                error=str(exc),
            )
        finally:
            if sql_connector is not None:
                try:
                    await sql_connector.close_connection()
                except Exception:
                    logger.warning(
                        "Failed to close SQL connection after assessing %s",
                        entity.entity_id,
                        exc_info=True,
                    )

    async def metadata_gaps(
        self,
        connector_ref: Dict[str, Any],
        entities: List[EntityRef],
        entity_profiles: Optional[List[Dict[str, Any]]] = None,
    ) -> List[GapFinding]:
        from services.data_assessment.metadata_gap_analyzer import analyze_sql_gaps

        return await analyze_sql_gaps(
            self.user_id,
            connector_ref["connector_id"],
            entities,
            entity_profiles=entity_profiles,
        )
