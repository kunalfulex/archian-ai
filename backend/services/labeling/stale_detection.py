"""Detect entities whose stored labels are stale vs current schema hashes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.labeling.schedule import schedule_entity_labeling
from services.labeling.store import LabelingStore


async def collect_entities_needing_labeling(
    *,
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
    drift_keys: List[str],
    current_hashes: Dict[str, str],
    legacy_keys: Optional[List[str]] = None,
) -> List[str]:
    """Merge drift, legacy uncategorized, and schema-hash stale entities."""
    stale = await LabelingStore.find_stale_entity_keys(
        user_id=user_id,
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
        current_hashes=current_hashes,
    )
    merged = list(
        dict.fromkeys([*(drift_keys or []), *(legacy_keys or []), *stale])
    )
    return [k for k in merged if k in current_hashes or k in (legacy_keys or [])]


def hashes_for_keys(
    entity_keys: List[str],
    current_hashes: Dict[str, str],
) -> Dict[str, str]:
    return {k: current_hashes[k] for k in entity_keys if k in current_hashes}


async def schedule_labeling_with_hashes(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    entity_keys: List[str],
    current_hashes: Dict[str, str],
) -> Optional[str]:
    if not entity_keys:
        return None
    return await schedule_entity_labeling(
        user_id=user_id,
        connector_type=connector_type,
        connector_ref=connector_ref,
        entity_keys=entity_keys,
        entity_hashes=hashes_for_keys(entity_keys, current_hashes),
    )
