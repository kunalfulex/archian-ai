"""Keys that may appear on extraction rows but are not migratable source columns."""

HIDDEN_EXTRACTION_RECORD_KEYS = frozenset(
    {
        "data_cleanup_rules",
        "transformation_rules",
        "filter_rules",
        "ordering_rules",
        "deduplication_rules",
        "mapping_context_summary",
        "source_schema",
        "document_type",
        "document_description",
        "object_name",
        "merge_actions",
        "remove_fields",
    }
)


def is_hidden_extraction_metadata_key(key: str) -> bool:
    return isinstance(key, str) and (
        key.startswith("_") or key in HIDDEN_EXTRACTION_RECORD_KEYS
    )
