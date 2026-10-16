"""Tests for persisted extraction_metadata builder."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.extraction_metadata import build_persisted_extraction_metadata
from services.intent.job_context import load_job_context
from services.intent.continuation_planning import _base_classification_from_job
from services.intent.session_continuation import SessionContinuationDecision
from services.intent.job_context import JobContext, ResourcePin


def test_build_persisted_from_migration_intent_related():
    mi = {
        "driving_entity_key": "Contact",
        "driving_entity_label": "Contact",
        "include_related_data": True,
        "parent_entity_key": "Account",
        "parent_entity_label": "Account",
        "related_field_hints": ["name", "phone"],
        "lookup_field_path": "AccountId",
        "lookup_relationship_name": "Account",
        "field_hints": ["FirstName", "Email"],
        "extract_field_scope": "explicit",
    }
    meta = build_persisted_extraction_metadata(migration_intent=mi, destination="salesforce")
    assert meta["include_related_data"] is True
    assert meta["parent_entity_key"] == "Account"
    assert meta["related_field_hints"] == ["name", "phone"]
    assert meta["lookup_field_path"] == "AccountId"
    assert meta["lookup_relationship_name"] == "Account"
    assert meta["source_entity_key"] == "Contact"


def test_build_persisted_merges_scope_lookup_fields():
    scope = {
        "include_related_data": True,
        "parent_entity_keys": ["Account"],
        "related_field_hints": ["name"],
        "lookup_field_path": "Account.Name",
        "lookup_relationship_name": "Account",
    }
    meta = build_persisted_extraction_metadata(extract_field_scope=scope)
    assert meta["include_related_data"] is True
    assert meta["parent_entity_key"] == "Account"
    assert meta["related_field_hints"] == ["name"]
    assert meta["lookup_field_path"] == "Account.Name"


def test_load_job_context_reads_related_metadata():
    async def _run():
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                return_value={
                    "job_id": "job-rel-1",
                    "source_type": "salesforce",
                    "source_name": "SF 2",
                    "record_count": 5,
                    "extraction_metadata": {
                        "include_related_data": True,
                        "parent_entity_key": "Account",
                        "parent_entity_label": "Account",
                        "related_field_hints": ["name", "phone"],
                        "lookup_field_path": "AccountId",
                        "lookup_relationship_name": "Account",
                        "object_api_name": "Contact",
                        "destination": "salesforce",
                        "destination_name": "SF 1",
                    },
                }
            ),
        ), patch(
            "utils.job_stores.extraction_jobs.get",
            new=AsyncMock(return_value={}),
        ):
            return await load_job_context(user_id="user-1", job_id="job-rel-1")

    job = asyncio.run(_run())
    assert job is not None
    assert job.include_related_data is True
    assert job.parent_entity_key == "Account"
    assert job.related_field_hints == ["name", "phone"]
    assert job.lookup_field_path == "AccountId"


def test_build_persisted_stores_full_related_entity_keys_list():
    meta = build_persisted_extraction_metadata(
        related_entity_keys=["user_plans", "users"],
        migration_intent={"include_related_data": True, "driving_entity_key": "users"},
    )
    assert meta["related_entity_keys"] == ["user_plans", "users"]
    assert meta["include_related_data"] is True


def test_sql_persisted_metadata_connector_and_table():
    from services.sql_background_processing_service import _sql_persisted_extraction_metadata

    task_data = {
        "migration_intent": {
            "include_related_data": True,
            "driving_entity_key": "users",
        },
        "related_entity_keys": ["user_plans"],
        "source_entity_key": "users",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "table_and_query": {
            "table_names": ["users", "user_plans"],
            "query": "SELECT * FROM users JOIN user_plans",
            "extraction_metadata": {
                "selected_columns": ["email", "plan_name"],
                "extract_contract": {"primary_table": "users"},
            },
        },
    }
    meta = _sql_persisted_extraction_metadata(
        task_data,
        table_name="users",
        table_names=["users", "user_plans"],
        connector_id="conn-uuid",
        connector_name="Test DB",
        sql_query="SELECT * FROM users JOIN user_plans",
    )
    assert meta["source_name"] == "Test DB"
    assert meta["source_connector_id"] == "conn-uuid"
    assert meta["table_name"] == "users"
    assert meta["table_names"] == ["users", "user_plans"]
    assert meta["source_entity_key"] == "users"
    assert meta["related_entity_keys"] == ["user_plans"]
    assert meta["field_hints"] == ["email", "plan_name"]


def test_load_job_context_sql_connector_id_from_source_identifier():
    async def _run():
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                return_value={
                    "job_id": "job-sql-1",
                    "source_type": "sql",
                    "source_name": "Test DB",
                    "source_identifier": "conn-uuid-99",
                    "record_count": 4,
                    "extraction_metadata": {
                        "source_entity_key": "users",
                        "table_name": "users",
                        "table_names": ["users", "user_plans"],
                        "related_entity_keys": ["user_plans"],
                        "include_related_data": True,
                        "destination": "salesforce",
                        "destination_name": "SF 1",
                    },
                }
            ),
        ), patch(
            "utils.job_stores.extraction_jobs.get",
            new=AsyncMock(return_value={}),
        ):
            return await load_job_context(user_id="user-1", job_id="job-sql-1")

    job = asyncio.run(_run())
    assert job is not None
    assert job.source.display_name == "Test DB"
    assert job.source.connector_id == "conn-uuid-99"
    assert job.source.entity_key == "users"
    assert job.related_entity_keys == ["user_plans"]
    assert job.include_related_data is True


def test_continuation_rebuilds_sql_migration_intent_and_contract():
    job = JobContext(
        job_id="job-sql-2",
        pipeline_stage="extracted",
        last_completed_step="extract",
        source=ResourcePin(connector_type="sql", display_name="Test DB", entity_key="users", connector_id="conn-1"),
        destination=ResourcePin(connector_type="salesforce", display_name="SF 1", entity_key="Contact"),
        include_related_data=True,
        related_entity_keys=["user_plans"],
        field_hints=["email"],
        extract_field_scope="explicit",
    )
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="map",
        confidence=0.9,
    )
    cls = _base_classification_from_job(decision, job)
    assert cls["source_entity_key"] == "users"
    assert cls["source_table_name"] == "users"
    assert cls["source_connector_id"] == "conn-1"
    contract = cls.get("plan_contract") or {}
    assert contract.get("source", {}).get("entity_key") == "users"
    assert contract.get("source", {}).get("display_name") == "Test DB"
    mi = cls.get("migration_intent") or {}
    assert mi.get("include_related_data") is True
    assert mi.get("related_entity_keys") == ["user_plans"]


def test_continuation_rebuilds_related_migration_intent():
    job = JobContext(
        job_id="job-1",
        pipeline_stage="extracted",
        last_completed_step="extract",
        source=ResourcePin(connector_type="salesforce", display_name="SF 2", entity_key="Contact"),
        destination=ResourcePin(connector_type="salesforce", display_name="SF 1", entity_key="Contact"),
        include_related_data=True,
        parent_entity_key="Account",
        parent_entity_label="Account",
        related_field_hints=["name", "phone"],
        lookup_field_path="AccountId",
        lookup_relationship_name="Account",
        field_hints=["FirstName", "Email"],
        extract_field_scope="explicit",
    )
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="map",
        confidence=0.9,
    )
    cls = _base_classification_from_job(decision, job)
    mi = cls.get("migration_intent") or {}
    assert mi.get("include_related_data") is True
    assert mi.get("parent_entity_key") == "Account"
    assert mi.get("related_field_hints") == ["name", "phone"]
    assert mi.get("lookup_field_path") == "AccountId"
