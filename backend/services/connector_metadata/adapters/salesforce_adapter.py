"""Salesforce metadata sync: schema + drift-only org rules."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID

from db.connection import database
from services.connectors.salesforce_ref import normalize_salesforce_connector_ref
from services.connector_metadata.drift import compute_drift_from_maps
from services.connector_metadata.models import DriftReport
from services.labeling.catalog_hash_helpers import (
    salesforce_legacy_uncategorized_keys,
    update_salesforce_object_hashes,
)
from services.labeling.pending_labeling import schedule_pending_entity_labeling
from services.salesforce_org_rules_service import sync_org_rules_for_objects
from utils.salesforceapimigration import fetch_and_store_salesforce_objects, get_valid_salesforce_token

logger = logging.getLogger(__name__)


class SalesforceMetadataAdapter:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def list_entity_ids(self, connector_ref: Dict[str, Any]) -> List[str]:
        connector_ref = await normalize_salesforce_connector_ref(self.user_id, connector_ref)
        org_id = connector_ref["org_id"]
        rows = await database.fetch_all(
            """
            SELECT object_api_name FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id
              AND EXISTS (
                SELECT 1 FROM salesforce_object_fields f
                WHERE f.user_id = salesforce_objects.user_id
                  AND f.org_id = salesforce_objects.org_id
                  AND f.object_api_name = salesforce_objects.object_api_name
              )
            ORDER BY object_api_name
            """,
            {"user_id": self.user_id, "org_id": org_id},
        )
        return [dict(r)["object_api_name"] for r in rows or [] if dict(r).get("object_api_name")]

    async def _load_previous_hashes(self, org_id: str) -> Dict[str, Optional[str]]:
        rows = await database.fetch_all(
            """
            SELECT object_api_name, schema_hash FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id
            """,
            {"user_id": self.user_id, "org_id": org_id},
        )
        return {
            dict(r)["object_api_name"]: dict(r).get("schema_hash")
            for r in rows or []
            if dict(r).get("object_api_name")
        }

    async def sync_schema(
        self,
        connector_ref: Dict[str, Any],
        *,
        sync_mode: str,
        on_progress: Optional[Callable[..., Any]] = None,
    ) -> DriftReport:
        connector_ref = await normalize_salesforce_connector_ref(self.user_id, connector_ref)
        org_id = connector_ref["org_id"]
        token = await get_valid_salesforce_token(UUID(self.user_id), org_id)
        if not token:
            raise ValueError("Salesforce token not available for this org")

        previous_hashes = await self._load_previous_hashes(org_id)

        if on_progress:
            await on_progress(percent=10, message="Fetching Salesforce schema")

        await fetch_and_store_salesforce_objects(
            UUID(self.user_id),
            token["instance_url"],
            token["access_token"],
            org_id,
            skip_org_rules_sync=True,
            schedule_labeling=False,
        )

        if on_progress:
            await on_progress(percent=70, message="Computing schema drift")

        from services.connector_metadata.empty_catalog_cleanup import (
            purge_salesforce_objects_without_fields,
        )

        await purge_salesforce_objects_without_fields(self.user_id, org_id)
        current_hashes = await update_salesforce_object_hashes(self.user_id, org_id)
        current_set = set(current_hashes.keys())
        stored_ids = set(previous_hashes.keys())

        for removed in stored_ids - current_set:
            from services.connector_metadata.empty_catalog_cleanup import (
                purge_salesforce_object_catalog_entry,
            )

            await purge_salesforce_object_catalog_entry(
                self.user_id, org_id, removed
            )

        drift = compute_drift_from_maps(previous_hashes=previous_hashes, current_hashes=current_hashes)
        to_rules = drift.entities_needing_categorization(sync_mode)

        if to_rules and sync_mode != "schema_only":
            if on_progress:
                await on_progress(percent=85, message="Syncing org rules for changed objects")
            await sync_org_rules_for_objects(
                UUID(self.user_id),
                org_id,
                token["instance_url"],
                token["access_token"],
                to_rules,
            )

        if sync_mode != "schema_only":
            drift_keys = list(dict.fromkeys(drift.entities_needing_categorization(sync_mode)))
            legacy_keys = await salesforce_legacy_uncategorized_keys(self.user_id, org_id)
            queued = await schedule_pending_entity_labeling(
                user_id=self.user_id,
                connector_type="salesforce",
                connector_ref=connector_ref,
                current_hashes=current_hashes,
                drift_keys=drift_keys,
                legacy_keys=legacy_keys,
            )
            if queued and on_progress:
                await on_progress(
                    percent=92,
                    message=f"Queued labeling for {queued} object(s)",
                )

        if on_progress:
            await on_progress(percent=100, message="Schema sync complete")

        return drift
