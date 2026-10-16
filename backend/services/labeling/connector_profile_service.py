"""Connector business profile rollup from entity semantics."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from db.connection import database
from services.labeling.config import LabelingConfig
from services.labeling.connector_ref import connector_instance_id
from services.labeling.models import ConnectorAiProfile
from services.labeling.profile_prompts import build_connector_profile_prompt
from services.labeling.store import LabelingStore
from utils.openai_helpers import parse_openai_json_response

logger = logging.getLogger(__name__)


async def get_connector_display_name(
    *,
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
) -> str:
    if connector_type == "salesforce":
        row = await database.fetch_one(
            """
            SELECT COALESCE(
                NULLIF(TRIM(org_display_name), ''),
                NULLIF(TRIM(salesforce_user_email), ''),
                instance_url,
                'Salesforce'
            ) AS name
            FROM salesforce_tokens
            WHERE user_id = :user_id AND id::text = :connector_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "connector_id": connector_instance_id},
        )
        return str(dict(row).get("name") or "Salesforce") if row else "Salesforce"
    if connector_type == "sql":
        row = await database.fetch_one(
            """
            SELECT name FROM sql_connectors
            WHERE user_id = :user_id AND id::text = :connector_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "connector_id": connector_instance_id},
        )
        return str(dict(row).get("name") or "SQL Database") if row else "SQL Database"
    if connector_type == "google_sheets":
        row = await database.fetch_one(
            """
            SELECT COALESCE(connection_name, google_account_email, 'Google Sheets') AS name
            FROM google_sheets_connections
            WHERE user_id = :user_id AND id::text = :connection_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "connection_id": connector_instance_id},
        )
        return str(dict(row).get("name") or "Google Sheets") if row else "Google Sheets"
    return connector_instance_id


class ConnectorProfileService:
    async def should_rollup(
        self,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        stats = await LabelingStore.get_entity_labeling_stats(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=connector_instance_id,
        )
        total = stats.get("total") or 0
        ready = stats.get("ready") or 0
        if ready == 0:
            return False
        if total == 0:
            return ready > 0
        return (ready / total) >= LabelingConfig.profile_rollup_min_ratio()

    async def rollup_connector_profile(
        self,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        inst = connector_instance_id(connector_type, connector_ref)
        if not await self.should_rollup(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=inst,
            force=force,
        ):
            logger.info(
                "Skipping profile rollup %s/%s — below ready threshold",
                connector_type,
                inst,
            )
            return None

        stats = await LabelingStore.get_entity_labeling_stats(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=inst,
        )
        entities = await LabelingStore.list_ready_entity_summaries(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=inst,
        )
        if not entities:
            return None

        display_name = await get_connector_display_name(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=inst,
        )
        existing = await LabelingStore.get_business_profile(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=inst,
        )
        user_description = (existing or {}).get("user_description")

        await LabelingStore.upsert_business_profile_shell(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            display_name=display_name,
        )

        prompt = build_connector_profile_prompt(
            connector_type=connector_type,
            display_name=display_name,
            user_description=user_description,
            entity_summaries=entities,
            stats=stats,
        )

        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.SCHEMA_CATEGORIZATION,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2500,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = parse_openai_json_response(raw)
        profile = ConnectorAiProfile.from_llm_dict(parsed if isinstance(parsed, dict) else {})
        profile.rollup_stats = {
            "entity_count": stats.get("total", 0),
            "categorized_entity_count": stats.get("ready", 0),
            "failed_entity_count": stats.get("failed", 0),
        }
        ai_dict = profile.to_dict()

        await LabelingStore.save_ai_profile(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            display_name=display_name,
            ai_profile=ai_dict,
            profile_status="ready",
            user_description=user_description,
        )
        return ai_dict
