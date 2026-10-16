"""Attached file upload forces fresh session (not continuation)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.file_extraction.pending_upload import (
    attached_file_forces_fresh_session,
    fresh_session_decision_for_attached_file,
    promote_file_upload_session_from_classification,
)
from services.intent.classify_and_enrich import classify_and_enrich
from services.planning.resource_catalog import ResourceCatalog


def test_attached_file_forces_fresh_session():
    assert attached_file_forces_fresh_session("file") is True
    assert attached_file_forces_fresh_session("FILE") is True
    assert attached_file_forces_fresh_session(None) is False
    assert attached_file_forces_fresh_session("salesforce") is False


def test_fresh_session_decision_defaults_to_fresh_extract():
    decision = fresh_session_decision_for_attached_file()
    assert decision.session_action == "fresh_extract"
    assert decision.reuse_job_id is False
    assert decision.decision_source == "file_upload_guardrail"


def test_promote_file_upload_after_classifier_migrate():
    decision = fresh_session_decision_for_attached_file()
    promoted = promote_file_upload_session_from_classification(
        decision,
        {
            "intent": "migrate",
            "mode": "migrate",
            "destination": "salesforce",
            "destination_name": "SF 1",
        },
    )
    assert promoted.session_action == "fresh_full_pipeline"


def test_fresh_session_decision_extract_only():
    decision = fresh_session_decision_for_attached_file()
    assert decision.session_action == "fresh_extract"


def test_classify_and_enrich_skips_continuation_when_file_attached():
    catalog = ResourceCatalog()

    async def _run():
        from services.intent.pipeline_turn_routing import PipelineTurnDecision
        from services.intent.session_continuation import SessionContinuationDecision

        with patch(
            "services.intent.classify_and_enrich.load_classify_context",
            new_callable=AsyncMock,
            return_value=type("Ctx", (), {"catalog": catalog, "google_sheet_rows": []})(),
        ), patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=type(
                "Gate0",
                (),
                {
                    "plan_route": "pipeline",
                    "confidence": 1.0,
                    "decision_source": "llm",
                },
            )(),
        ), patch(
            "services.intent.pipeline_turn_routing.resolve_pipeline_turn",
            new_callable=AsyncMock,
            return_value=PipelineTurnDecision(
                branch="fresh",
                confidence=0.95,
                decision_source="llm",
            ),
        ), patch(
            "services.intent.classify_and_enrich.resolve_session_continuation",
            new_callable=AsyncMock,
            return_value=SessionContinuationDecision(
                session_action="continue_active_job",
                continuation_phase="migrate",
                reuse_job_id=True,
                job_id="old-job",
                decision_source="llm",
                confidence=1.0,
            ),
        ) as mock_s01, patch(
            "services.intent.continuation_planning.plan_from_job_context",
            new_callable=AsyncMock,
        ) as mock_continuation_plan, patch(
            "services.intent.route_turn.route_planning_path",
            new_callable=AsyncMock,
            return_value="fresh_path",
        ), patch(
            "services.intent.classify_and_enrich.apply_post_classification_pipeline",
            new_callable=AsyncMock,
            side_effect=lambda cls, **kwargs: cls,
        ):
            out = await classify_and_enrich(
                "migrate them to SF 1 org",
                user_id="user-1",
                conversation_history=[],
                conversation_id="conv-1",
                explicit_source="file",
                explicit_source_name="Customer Contact Details.xlsx",
            )

        mock_s01.assert_not_called()
        mock_continuation_plan.assert_not_called()
        assert out.get("session_action") == "fresh_extract"
        assert out.get("_file_upload_pinned") is True

    asyncio.run(_run())


def test_classify_and_enrich_skips_job_continuation_when_file_attached():
    """job_continuation from pipeline_turn must not bypass the file upload guardrail."""
    catalog = ResourceCatalog()

    async def _run():
        from services.intent.pipeline_turn_routing import PipelineTurnDecision

        with patch(
            "services.intent.classify_and_enrich.load_classify_context",
            new_callable=AsyncMock,
            return_value=type("Ctx", (), {"catalog": catalog, "google_sheet_rows": []})(),
        ), patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=type(
                "Gate0",
                (),
                {
                    "plan_route": "pipeline",
                    "confidence": 1.0,
                    "decision_source": "llm",
                },
            )(),
        ), patch(
            "services.intent.pipeline_turn_routing.resolve_pipeline_turn",
            new_callable=AsyncMock,
            return_value=PipelineTurnDecision(
                branch="job_continuation",
                confidence=0.9,
                decision_source="llm",
            ),
        ) as mock_pipeline_turn, patch(
            "services.intent.continuation_planning.plan_from_job_context",
            new_callable=AsyncMock,
            return_value={"plan_route": "continuation_path"},
        ) as mock_job_plan, patch(
            "services.intent.classify_and_enrich.resolve_session_continuation",
            new_callable=AsyncMock,
        ) as mock_s01, patch(
            "services.intent.route_turn.route_planning_path",
            new_callable=AsyncMock,
            return_value="fresh_path",
        ), patch(
            "services.intent.classify_and_enrich.apply_post_classification_pipeline",
            new_callable=AsyncMock,
            side_effect=lambda cls, **kwargs: cls,
        ):
            out = await classify_and_enrich(
                "move the data to new google sheet, remove invalid records from the file",
                user_id="user-1",
                conversation_history=[{"metadata": {"job_id": "active-job-1"}}],
                conversation_id="conv-1",
                explicit_source="file",
                explicit_source_name="Customer Contact Details.xlsx",
            )

        mock_pipeline_turn.assert_not_called()
        mock_job_plan.assert_not_called()
        mock_s01.assert_not_called()
        assert out.get("session_action") == "fresh_extract"
        assert out.get("_file_upload_pinned") is True
        assert out.get("source") == "file"
        assert out.get("source_name") == "Customer Contact Details.xlsx"

    asyncio.run(_run())


def test_classify_and_enrich_skips_planning_continuation_when_file_attached():
    catalog = ResourceCatalog()

    async def _run():
        from services.intent.pipeline_turn_routing import PipelineTurnDecision

        with patch(
            "services.intent.classify_and_enrich.load_classify_context",
            new_callable=AsyncMock,
            return_value=type("Ctx", (), {"catalog": catalog, "google_sheet_rows": []})(),
        ), patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=type(
                "Gate0",
                (),
                {
                    "plan_route": "pipeline",
                    "confidence": 1.0,
                    "decision_source": "llm",
                },
            )(),
        ), patch(
            "services.intent.pipeline_turn_routing.resolve_pipeline_turn",
            new_callable=AsyncMock,
            return_value=PipelineTurnDecision(
                branch="planning_continuation",
                confidence=0.95,
                decision_source="llm",
            ),
        ) as mock_pipeline_turn, patch(
            "services.intent.pipeline_turn.continue_planning_from_prompt",
            new_callable=AsyncMock,
            return_value={"needs_clarification": True},
        ) as mock_planning_cont, patch(
            "services.intent.route_turn.route_planning_path",
            new_callable=AsyncMock,
            return_value="fresh_path",
        ), patch(
            "services.intent.classify_and_enrich.apply_post_classification_pipeline",
            new_callable=AsyncMock,
            side_effect=lambda cls, **kwargs: cls,
        ):
            out = await classify_and_enrich(
                "Sales Leads SF 1",
                user_id="user-1",
                conversation_history=[],
                conversation_id="conv-1",
                explicit_source="file",
                explicit_source_name="leads.xlsx",
            )

        mock_pipeline_turn.assert_not_called()
        mock_planning_cont.assert_not_called()
        assert out.get("_file_upload_pinned") is True

    asyncio.run(_run())
