"""EGA entity inclusion filters — semantics-first, connector-agnostic."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.labeling.catalog_helpers import entity_class_from_semantics
from services.planning.entity_catalog_policy import is_metadata_entity

# Junction tables are linkage tables, not cross-connector mapping endpoints.
_EGA_EXCLUDED_ENTITY_CLASSES = frozenset({"junction"})


def should_exclude_from_ega_modeling(
    entity_key: str,
    *,
    connector_type: str,
    semantics: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    True if entity should be omitted from cross-connector mapping and lake bronze inventory.

    Uses connector_entity_semantics (entity_class, category) via entity_catalog_policy.
    Name-based fallbacks apply only when semantics are missing.
    """
    key = (entity_key or "").strip()
    if not key:
        return True
    if is_metadata_entity(key, connector_type=connector_type, semantics=semantics):
        return True
    ec = entity_class_from_semantics(semantics)
    if ec in _EGA_EXCLUDED_ENTITY_CLASSES:
        return True
    return False
