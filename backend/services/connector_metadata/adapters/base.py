"""Connector metadata adapter protocol."""
from __future__ import annotations

from typing import Any, Dict, List, Protocol

from services.connector_metadata.models import DriftReport


class ConnectorMetadataAdapter(Protocol):
    user_id: str

    async def sync_schema(
        self,
        connector_ref: Dict[str, Any],
        *,
        sync_mode: str,
        on_progress: Any,
    ) -> DriftReport:
        """Pull metadata from source, upsert cache, soft-delete missing, categorize drifted."""
        ...

    async def list_entity_ids(self, connector_ref: Dict[str, Any]) -> List[str]:
        ...
