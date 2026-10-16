"""Tests for label learning merge policy and evidence helpers."""
from __future__ import annotations

from services.labeling.label_learning_service import merge_semantics_proposal
from services.labeling.evidence_connector_resolve import entity_key_from_classification


def test_merge_semantics_preserves_primary():
    existing = {
        "primary": {"category": "Contact", "description": "Customer people records."},
        "domains": ["crm"],
        "synonyms": ["customers"],
        "confidence": 0.9,
        "source": "schema_bootstrap",
    }
    proposal = {
        "add_synonyms": ["clients", "customers"],
        "add_domains": ["sales"],
        "add_processes": ["lead_management"],
    }
    merged = merge_semantics_proposal(existing, proposal)
    assert merged["primary"]["category"] == "Contact"
    assert merged["primary"]["description"] == "Customer people records."
    assert "clients" in merged["synonyms"]
    assert "sales" in merged["domains"]
    assert "lead_management" in merged["processes"]
    assert merged["source"] == "execution_learning"


def test_merge_semantics_dedupes_case_insensitive():
    existing = {"primary": {"category": "Account", "description": "x"}, "synonyms": ["Accounts"]}
    merged = merge_semantics_proposal(existing, {"add_synonyms": ["accounts", "orgs"]})
    assert merged["synonyms"] == ["Accounts", "orgs"]


def test_entity_key_from_classification_salesforce():
    clf = {"destination_object_api_name": "ServiceAppointment"}
    assert entity_key_from_classification(clf, "salesforce") == "ServiceAppointment"


def test_entity_key_from_classification_sql():
    clf = {"primary_table": "customers"}
    assert entity_key_from_classification(clf, "sql") == "customers"


def test_entity_key_from_classification_gsheets():
    clf = {
        "destination_spreadsheet_id": "abc123",
        "destination_sheet_name": "Sheet1",
    }
    assert entity_key_from_classification(clf, "google_sheets") == "abc123::Sheet1"


def test_migration_partial_captures_low_success_rate():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from services.labeling.label_learning_service import LabelLearningService

    async def _run():
        with patch.object(
            LabelLearningService, "_insert_and_schedule", new=AsyncMock()
        ) as insert:
            with patch(
                "services.labeling.label_learning_service.resolve_connector_target",
                new=AsyncMock(return_value=("salesforce", {"org_id": "o1"}, "o1")),
            ):
                await LabelLearningService.capture_migration_outcome(
                    user_id="u1",
                    conversation_id="c1",
                    batch_id="b1",
                    destination_type="salesforce",
                    destination_name="Prod",
                    total_records=100,
                    successful_records=50,
                    failed_records=50,
                    failed_fields=["LastName"],
                    error_samples=["Required fields are missing: [LastName]"],
                )
            insert.assert_called_once()
            assert insert.call_args.kwargs["event_type"] == "migration_partial"

    asyncio.run(_run())
