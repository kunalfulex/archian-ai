"""Write primary labels to legacy schema cache columns."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from db.connection import database
from services.labeling.connector_ref import parse_gsheet_entity_key
from services.labeling.models import EntitySemantics

logger = logging.getLogger(__name__)


async def sync_primary_to_legacy_columns(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    entity_key: str,
    semantics: EntitySemantics,
) -> None:
    """Deprecated: connector_entity_semantics is the source of truth for labels."""
    del user_id, connector_type, connector_ref, entity_key, semantics
    return
