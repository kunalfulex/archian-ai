"""Backward-compatible re-exports; use split_field_utils for new code."""
from utils.split_field_utils import (
    destination_field_is_required_from_schema,
    metadata_keys_for_split_mapping,
    resolve_split_required_indices,
    split_value_into_parts,
)

__all__ = [
    "destination_field_is_required_from_schema",
    "metadata_keys_for_split_mapping",
    "resolve_split_required_indices",
    "split_value_into_parts",
]
