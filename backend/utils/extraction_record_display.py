"""Strip extraction-rule blobs accidentally merged onto row payloads for API/display."""
from __future__ import annotations

from typing import Any, Dict

# Keys embedded onto row 0 by post_extract/session._embed_rules_in_records
DISPLAY_METADATA_KEYS = frozenset(
    {
        "data_cleanup_rules",
        "transformation_rules",
        "filter_rules",
        "deduplication_rules",
        "ordering_rules",
        "mapping_context_summary",
        "document_type",
        "document_description",
        "object_name",
        "source_schema",
        # Alternate shapes occasionally echoed by LLM merges
        "merge_actions",
        "remove_fields",
    }
)


def strip_row_display_metadata(record: Any) -> Any:
    if not isinstance(record, dict):
        return record
    return {k: v for k, v in record.items() if k not in DISPLAY_METADATA_KEYS}
