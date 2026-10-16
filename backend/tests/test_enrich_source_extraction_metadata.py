"""Source-only extraction metadata enrich (no destination catalog overwrite)."""

import asyncio

from utils.document_category_match import (
    destination_requires_pick,
    enrich_source_extraction_metadata,
)


def test_pending_destination_preserves_source_labels():
    meta = asyncio.run(
        enrich_source_extraction_metadata(
            "user-1",
            "unknown",
            None,
            "Contact",
            "Contact records",
            {"email": "a@b.com"},
        )
    )
    assert meta["requires_destination"] is True
    assert meta["source_document_type"] == "Contact"
    assert meta["canonical_document_type"] == "Contact"
    assert meta["category_match_tier"] == "pending_destination"


def test_known_destination_preserves_source_labels_without_dest_reconcile():
    meta = asyncio.run(
        enrich_source_extraction_metadata(
            "user-1",
            "salesforce",
            "Production 1",
            "Contact",
            "Contact records",
            {"FirstName": "Rose"},
            source_type="salesforce",
            source_name="Production 2",
        )
    )
    assert meta["requires_destination"] is False
    assert meta["source_document_type"] == "Contact"
    assert destination_requires_pick("salesforce", "Production 1") is False


def test_destination_requires_pick_unknown():
    assert destination_requires_pick(None) is True
    assert destination_requires_pick("unknown") is True
    assert destination_requires_pick("salesforce") is True
    assert destination_requires_pick("salesforce", "SF 1") is False
