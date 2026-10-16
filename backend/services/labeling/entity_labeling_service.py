"""Unified LLM entity labeling for SF, SQL, and Google Sheets."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from db.connection import database
from services.labeling.config import LabelingConfig
from services.connectors.salesforce_ref import salesforce_org_id_from_ref
from services.labeling.connector_ref import parse_gsheet_entity_key
from services.labeling.legacy_sync import sync_primary_to_legacy_columns
from services.labeling.models import EntitySemantics
from services.labeling.prompts import build_entity_labeling_prompt
from services.labeling.schema_summary import (
    _normalize_gsheets_headers,
    build_gsheets_labeling_schema_summary,
    build_salesforce_labeling_schema_summary,
    build_sql_labeling_schema_summary,
    filter_relationship_hints_to_catalog,
)
from services.labeling.store import LabelingStore
from services.sql_background_processing_service import SQLBackgroundProcessingService
from services.sql_connector_service import SQLConnectorFactory
from utils.openai_helpers import parse_openai_json_response

logger = logging.getLogger(__name__)

_label_semaphore: Optional[asyncio.Semaphore] = None


def _semaphore() -> asyncio.Semaphore:
    global _label_semaphore
    if _label_semaphore is None:
        _label_semaphore = asyncio.Semaphore(LabelingConfig.max_concurrency())
    return _label_semaphore


@dataclass
class EntityLoadResult:
    entity_display_name: str
    schema_summary: Dict[str, Any]
    schema_hash: Optional[str]
    sample_data: List[Dict[str, Any]]


def _gsheet_entity_display_name(spreadsheet_name: Optional[str], sheet_name: str) -> str:
    wb = (spreadsheet_name or "").strip()
    tab = (sheet_name or "").strip() or "Sheet1"
    if wb and wb.lower() != tab.lower():
        return f"{wb} · {tab}"
    return tab


class EntityLabelingService:
    async def label_entities_batch(
        self,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        entity_keys: List[str],
        on_progress: Optional[Any] = None,
    ) -> Dict[str, Any]:
        total = len(entity_keys)
        labeled = 0
        failed = 0
        for entity_key in entity_keys:
            try:
                async with _semaphore():
                    await self.label_one_entity(
                        user_id=user_id,
                        connector_type=connector_type,
                        connector_ref=connector_ref,
                        entity_key=entity_key,
                    )
                labeled += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Entity labeling failed %s/%s: %s",
                    connector_type,
                    entity_key,
                    exc,
                )
                await LabelingStore.save_entity_semantics(
                    user_id=user_id,
                    connector_type=connector_type,
                    connector_ref=connector_ref,
                    entity_key=entity_key,
                    entity_display_name=entity_key,
                    semantics={},
                    schema_hash=None,
                    labeling_status="failed",
                    error_summary=str(exc)[:500],
                )
            if on_progress:
                await on_progress(labeled=labeled, total=total, failed=failed, entity_key=entity_key)
        return {"labeled": labeled, "failed": failed, "total": total}

    async def label_one_entity(
        self,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        entity_key: str,
    ) -> EntitySemantics:
        loaded = await self._load_entity(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            entity_key=entity_key,
        )
        await LabelingStore.save_entity_semantics(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            entity_key=entity_key,
            entity_display_name=loaded.entity_display_name,
            semantics={},
            schema_hash=loaded.schema_hash,
            labeling_status="processing",
        )

        prompt = build_entity_labeling_prompt(
            connector_type=connector_type,
            entity_key=entity_key,
            entity_display_name=loaded.entity_display_name,
            schema_summary=loaded.schema_summary,
            sample_data=loaded.sample_data or None,
        )
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.SCHEMA_CATEGORIZATION,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = parse_openai_json_response(raw)
        semantics = EntitySemantics.from_llm_dict(parsed if isinstance(parsed, dict) else {})
        semantics = self._finalize_semantics(
            semantics,
            connector_type=connector_type,
            schema_summary=loaded.schema_summary,
        )

        await LabelingStore.save_entity_semantics(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            entity_key=entity_key,
            entity_display_name=loaded.entity_display_name,
            semantics=semantics.to_dict(),
            schema_hash=loaded.schema_hash,
            labeling_status="ready",
        )
        await sync_primary_to_legacy_columns(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            entity_key=entity_key,
            semantics=semantics,
        )
        return semantics

    def _finalize_semantics(
        self,
        semantics: EntitySemantics,
        *,
        connector_type: str,
        schema_summary: Dict[str, Any],
    ) -> EntitySemantics:
        """Prefer structurally derived relationship_hints over LLM-invented ones."""
        ct = (connector_type or "").strip().lower()
        suggested = list(schema_summary.get("suggested_relationship_hints") or [])

        if ct == "sql":
            catalog = {
                (t or "").strip()
                for t in (schema_summary.get("catalog_tables") or [])
                if (t or "").strip()
            }
            if suggested:
                semantics.relationship_hints = suggested
            else:
                semantics.relationship_hints = filter_relationship_hints_to_catalog(
                    semantics.relationship_hints,
                    allowed_entity_keys=catalog,
                )
            return semantics

        if ct == "salesforce":
            if suggested:
                semantics.relationship_hints = suggested
            return semantics

        if ct == "google_sheets":
            allowed: set[str] = {str(schema_summary.get("entity_key") or "").strip()}
            for sib in schema_summary.get("sibling_tabs") or []:
                if isinstance(sib, dict):
                    ek = (sib.get("entity_key") or "").strip()
                    if ek:
                        allowed.add(ek)
            semantics.relationship_hints = filter_relationship_hints_to_catalog(
                semantics.relationship_hints,
                allowed_entity_keys=allowed,
            )
            return semantics

        return semantics

    async def _load_entity(
        self,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        entity_key: str,
    ) -> EntityLoadResult:
        if connector_type == "sql":
            return await self._load_sql_entity(user_id, connector_ref, entity_key)
        if connector_type == "salesforce":
            return await self._load_salesforce_entity(user_id, connector_ref, entity_key)
        if connector_type == "google_sheets":
            return await self._load_gsheets_entity(user_id, connector_ref, entity_key)
        raise ValueError(f"Unsupported connector_type: {connector_type}")

    async def _load_sql_entity(
        self,
        user_id: str,
        connector_ref: Dict[str, Any],
        table_name: str,
    ) -> EntityLoadResult:
        connector_id = connector_ref["connector_id"]
        row = await database.fetch_one(
            """
            SELECT schema_data, schema_hash FROM sql_extracted_schemas
            WHERE user_id = :user_id AND connector_id = :connector_id AND table_name = :table_name
            """,
            {"user_id": str(user_id), "connector_id": connector_id, "table_name": table_name},
        )
        if not row:
            raise ValueError(f"SQL table not found: {table_name}")
        raw = dict(row).get("schema_data")
        catalog_rows = await database.fetch_all(
            """
            SELECT table_name FROM sql_extracted_schemas
            WHERE user_id = :user_id AND connector_id = :connector_id
              AND COALESCE(is_deleted, FALSE) = FALSE
            ORDER BY table_name
            """,
            {"user_id": str(user_id), "connector_id": connector_id},
        )
        catalog_tables = [
            str(dict(r).get("table_name") or "").strip()
            for r in catalog_rows or []
            if str(dict(r).get("table_name") or "").strip()
        ]
        schema_summary = build_sql_labeling_schema_summary(
            table_name=table_name,
            schema_data_raw=raw,
            catalog_tables=catalog_tables,
        )
        sample_data: List[Dict[str, Any]] = []
        bg = SQLBackgroundProcessingService()
        connector = await bg.get_connector_details(connector_id, user_id)
        if connector:
            try:
                inst = SQLConnectorFactory.create_connector(
                    connector["connector_type"],
                    bg.prepare_connector_config(connector),
                )
                sample_data = await bg.get_sample_data(inst, table_name, limit=3)
                await inst.close_connection()
            except Exception as exc:
                logger.warning("SQL sample data for %s: %s", table_name, exc)
        return EntityLoadResult(
            entity_display_name=table_name,
            schema_summary=schema_summary,
            schema_hash=dict(row).get("schema_hash"),
            sample_data=sample_data[:3],
        )

    async def _load_salesforce_entity(
        self,
        user_id: str,
        connector_ref: Dict[str, Any],
        object_api_name: str,
    ) -> EntityLoadResult:
        org_id = salesforce_org_id_from_ref(connector_ref)
        obj = await database.fetch_one(
            """
            SELECT label, schema_hash FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
            """,
            {"user_id": str(user_id), "org_id": org_id, "obj": object_api_name},
        )
        if not obj:
            raise ValueError(f"Salesforce object not found: {object_api_name}")
        fields = await database.fetch_all(
            """
            SELECT field_api_name, field_label, data_type, field_description,
                   is_lookup, lookup_object_api_name, is_required
            FROM salesforce_object_fields
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
            ORDER BY field_api_name
            LIMIT 200
            """,
            {"user_id": str(user_id), "org_id": org_id, "obj": object_api_name},
        )
        field_dicts = [dict(f) for f in fields or []]
        schema_summary = build_salesforce_labeling_schema_summary(
            object_api_name=object_api_name,
            label=dict(obj).get("label"),
            fields=field_dicts,
        )
        return EntityLoadResult(
            entity_display_name=dict(obj).get("label") or object_api_name,
            schema_summary=schema_summary,
            schema_hash=dict(obj).get("schema_hash"),
            sample_data=[],
        )

    async def _load_gsheets_entity(
        self,
        user_id: str,
        connector_ref: Dict[str, Any],
        entity_key: str,
    ) -> EntityLoadResult:
        connection_id = connector_ref["connection_id"]
        spreadsheet_id, sheet_name = parse_gsheet_entity_key(entity_key)
        row = await database.fetch_one(
            """
            SELECT spreadsheet_name, header_schema, schema_hash
            FROM gsheets_extracted_schemas
            WHERE user_id = :user_id
              AND google_sheets_connection_id = :connection_id
              AND spreadsheet_id = :spreadsheet_id
              AND sheet_name = :sheet_name
            """,
            {
                "user_id": str(user_id),
                "connection_id": connection_id,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
            },
        )
        if not row:
            raise ValueError(f"Google Sheet tab not found: {entity_key}")
        raw_headers = dict(row).get("header_schema")
        sibling_rows = await database.fetch_all(
            """
            SELECT sheet_name, header_schema
            FROM gsheets_extracted_schemas
            WHERE user_id = :user_id
              AND google_sheets_connection_id = :connection_id
              AND spreadsheet_id = :spreadsheet_id
              AND sheet_name != :sheet_name
            ORDER BY sheet_name
            LIMIT 20
            """,
            {
                "user_id": str(user_id),
                "connection_id": connection_id,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
            },
        )
        sibling_tabs: List[Dict[str, Any]] = []
        for sib in sibling_rows or []:
            sd = dict(sib)
            sib_name = (sd.get("sheet_name") or "").strip()
            if not sib_name:
                continue
            sib_headers = _normalize_gsheets_headers(sd.get("header_schema"))
            sibling_tabs.append(
                {
                    "entity_key": f"{spreadsheet_id}::{sib_name}",
                    "sheet_name": sib_name,
                    "header_preview": [h.get("name") for h in sib_headers[:8] if h.get("name")],
                }
            )
        schema_summary = build_gsheets_labeling_schema_summary(
            spreadsheet_id=spreadsheet_id,
            spreadsheet_name=dict(row).get("spreadsheet_name"),
            sheet_name=sheet_name,
            entity_key=entity_key,
            header_schema_raw=raw_headers,
            sibling_tabs=sibling_tabs,
        )
        return EntityLoadResult(
            entity_display_name=_gsheet_entity_display_name(
                dict(row).get("spreadsheet_name"),
                sheet_name,
            ),
            schema_summary=schema_summary,
            schema_hash=dict(row).get("schema_hash"),
            sample_data=[],
        )
