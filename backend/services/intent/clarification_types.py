"""Canonical clarification types — must stay stable for frontend pickers."""
from __future__ import annotations

from typing import FrozenSet

# Connector / workbook (pre-planning, S06)
CONNECTOR_GAP_TYPES: FrozenSet[str] = frozenset(
    {
        "pick_source_connector",
        "pick_destination_connector",
        "pick_salesforce_org",
        "pick_sql_connector",
        "pick_google_account",
        "pick_workbook",
        "pick_workbook_ambiguous",
        "pick_connector_by_label",
        "pick_tab",
        "create_workbook_name",
    }
)

# Entity / field (post-planning, S07–S08)
ENTITY_FIELD_GAP_TYPES: FrozenSet[str] = frozenset(
    {
        "pick_source_entity",
        "pick_destination_entity",
        "pick_extract_fields",
        "pick_create_headers",
        "migration_intent_separate_pipelines",
        "migration_intent_entity_ambiguity",
    }
)

ALL_CLARIFICATION_TYPES: FrozenSet[str] = CONNECTOR_GAP_TYPES | ENTITY_FIELD_GAP_TYPES | frozenset(
    {"pick_flow"}
)
