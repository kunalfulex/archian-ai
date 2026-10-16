"""Tests for migrated pipeline stage in job context and session progress."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.job_context import JobContext, ResourcePin, load_job_context
from services.intent.route_turn import route_planning_path
from services.intent.session_continuation import (
    SessionContinuationDecision,
    _apply_progress_guardrails,
)
from services.planning.conversation_session import build_session_progress_summary
from services.planning.resource_catalog import ResourceCatalog


def _migrated_history() -> list:
    return [
        {
            "type": "system",
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-abc",
                "source_type": "salesforce",
                "source_name": "SF 1",
                "object_api_name": "Contact",
                "record_count": 10,
            },
        },
        {
            "type": "assistant",
            "metadata": {
                "event_type": "mapping_started",
                "job_id": "job-abc",
                "destination": "salesforce",
                "destination_name": "SF 2",
                "destination_object_api_name": "Contact",
                "mapping_config": {"Email": {"object": "Contact", "field": "Email"}},
            },
        },
        {
            "type": "system",
            "metadata": {
                "event_type": "migration_completed",
                "job_id": "job-abc",
                "status": "success",
                "success_count": 10,
                "error_count": 0,
            },
        },
    ]


def test_progress_after_migration_includes_pipeline_stage():
    progress = build_session_progress_summary(_migrated_history())
    assert progress["last_completed_step"] == "migrate"
    assert progress["next_logical_step"] == "none"
    assert progress["pipeline_stage"] == "migrated"
    assert progress["steps"]["migrate"]["status"] == "completed"


def test_job_context_s01_migrated_progress_dict():
    job = JobContext(
        job_id="job-1",
        pipeline_stage="migrated",
        last_completed_step="migrate",
        next_logical_step="none",
        mapping_ready=True,
        source=ResourcePin(connector_type="salesforce", display_name="SF 1", entity_key="Contact"),
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 2",
            entity_key="Contact",
        ),
    )
    progress = job.s01_progress_dict()
    assert progress["pipeline_stage"] == "migrated"
    assert progress["steps"]["migrate"]["status"] == "completed"
    assert progress["next_logical_step"] == "none"


def test_load_job_context_promotes_chat_migration_over_db_mapped():
    async def _run():
        history = _migrated_history()
        db_row = {
            "source_type": "salesforce",
            "source_name": "SF 1",
            "document_type": "Contact",
            "record_count": 10,
            "pipeline_stage": "mapped",
            "has_mapping_transform": True,
            "extraction_metadata": {},
        }
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new_callable=AsyncMock,
            return_value=db_row,
        ):
            job = await load_job_context(
                user_id="u1",
                job_id="job-abc",
                conversation_history=history,
            )
        assert job is not None
        assert job.pipeline_stage == "migrated"
        assert job.last_completed_step == "migrate"
        assert job.next_logical_step == "none"

    asyncio.run(_run())


def test_route_trusts_s01_fresh_without_keyword_match():
    async def _run():
        decision = SessionContinuationDecision(
            session_action="fresh_extract",
            continuation_phase="none",
            reuse_job_id=False,
            job_id="job-1",
            confidence=0.92,
            decision_source="llm",
            reasoning_short="Typo extract from SF 1",
        )
        job = JobContext(
            job_id="job-1",
            pipeline_stage="migrated",
            last_completed_step="migrate",
            mapping_ready=True,
            source=ResourcePin(connector_type="salesforce", display_name="SF 1"),
            destination=ResourcePin(connector_type="salesforce", display_name="SF 2"),
        )
        route = await route_planning_path(
            session_decision=decision,
            prompt="can you exarct contact data from SF 1 org",
            user_id="u1",
            catalog=ResourceCatalog(),
            job_context=job,
        )
        assert route == "fresh_path"

    asyncio.run(_run())


def test_progress_guardrail_preserves_llm_fresh_extract():
    progress = build_session_progress_summary(_migrated_history())
    decision = SessionContinuationDecision(
        session_action="fresh_extract",
        continuation_phase="none",
        reuse_job_id=False,
        job_id="job-abc",
        confidence=0.91,
        decision_source="llm",
        reasoning_short="New extract despite migrated job",
    )
    out = _apply_progress_guardrails(decision, progress)
    assert out.session_action == "fresh_extract"
    assert not out.reuse_job_id
    assert out.decision_source == "llm"
