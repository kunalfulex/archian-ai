"""Order entities for assessment runs."""
from __future__ import annotations

from typing import List

from services.data_assessment.config import SF_QUICK_OBJECTS
from services.data_assessment.models import EntityRef

SF_ORDER = {name: i for i, name in enumerate(SF_QUICK_OBJECTS)}


def plan_entity_order(entities: List[EntityRef], connector_type: str) -> List[EntityRef]:
    if connector_type == "salesforce":
        return sorted(
            entities,
            key=lambda e: (SF_ORDER.get(e.entity_id, 999), e.entity_id),
        )
    return sorted(entities, key=lambda e: e.label.lower())
