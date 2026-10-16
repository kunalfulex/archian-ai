"""Early routing for general/info vs pipeline turns."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.classify_and_enrich import classify_and_enrich
from services.intent.turn_type_routing import TurnTypeRoutingDecision
from services.planning.connector_llm_resolver import LlmInfoPick
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef


def _gate0_info():
    return TurnTypeRoutingDecision(
        turn_family="info",
        plan_route="info_path",
        confidence=0.95,
        decision_source="llm",
    )


def _gate0_general(*, conversational=True, off_topic=False):
    return TurnTypeRoutingDecision(
        turn_family="general",
        plan_route="general_path",
        confidence=0.95,
        conversational_turn=conversational,
        off_topic=off_topic,
        decision_source="llm",
    )


def _gate0_pipeline():
    return TurnTypeRoutingDecision(
        turn_family="pipeline",
        plan_route=None,
        confidence=0.95,
        decision_source="llm",
    )


def test_classify_and_enrich_hi_skips_s01_and_classifier():
    async def _run():
        with patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=_gate0_general(),
        ), patch(
            "services.intent.session_continuation.resolve_session_continuation",
            new_callable=AsyncMock,
        ) as mock_s01, patch(
            "services.intent.enrich.apply_post_classification_pipeline",
            new_callable=AsyncMock,
        ) as mock_enrich:
            out = await classify_and_enrich(
                "Hi",
                "user-1",
                conversation_history=[
                    {
                        "metadata": {
                            "event_type": "extraction_completed",
                            "job_id": "job-1",
                            "source": "salesforce",
                            "source_name": "SF 1",
                        }
                    }
                ],
            )
            mock_s01.assert_not_called()
            mock_enrich.assert_not_called()
            return out

    out = asyncio.run(_run())
    assert out.get("plan_route") == "general_path"
    assert out.get("mode") == "general"
    assert out.get("conversational_turn") is True
    assert "execution_plan" not in out
    assert "active_job_id" not in out


def test_classify_and_enrich_info_skips_s01_and_classifier():
    catalog = ResourceCatalog()
    catalog.salesforce_connections.append(
        SalesforceConnectionRef(name="SF 1", token_id="tok-1")
    )

    async def _run():
        with patch(
            "services.intent.classify_and_enrich.load_classify_context",
            new_callable=AsyncMock,
            return_value=type("Ctx", (), {"catalog": catalog, "google_sheet_rows": []})(),
        ), patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=_gate0_info(),
        ), patch(
            "services.intent.session_continuation.resolve_session_continuation",
            new_callable=AsyncMock,
        ) as mock_s01, patch(
            "services.intent.enrich.apply_post_classification_pipeline",
            new_callable=AsyncMock,
        ) as mock_enrich, patch(
            "services.labeling.catalog_helpers.fetch_semantics_map",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "services.planning.connector_llm_resolver.llm_resolve_info_connector",
            new_callable=AsyncMock,
            return_value=LlmInfoPick(
                connector_type="salesforce",
                display_name="SF 1",
                connector_id="tok-1",
                confidence=0.95,
            ),
        ):
            out = await classify_and_enrich(
                "What Salesforce objects are available in SF 1?",
                "user-1",
                conversation_history=[
                    {
                        "metadata": {
                            "event_type": "extraction_completed",
                            "job_id": "job-1",
                        }
                    }
                ],
            )
            mock_s01.assert_not_called()
            mock_enrich.assert_not_called()
            return out

    out = asyncio.run(_run())
    assert out.get("plan_route") == "info_path"
    assert out.get("intent") == "info"
    assert out.get("source") == "salesforce"
    assert out.get("source_name") == "SF 1"
    assert "execution_plan" not in out


def test_classify_and_enrich_golden_info_skips_s01_and_classifier():
    catalog = ResourceCatalog()
    catalog.salesforce_connections.append(
        SalesforceConnectionRef(name="SF 1", token_id="tok-1")
    )
    prompt = "where do we store customer information in salesforce sf 1 org?"

    async def _run():
        with patch(
            "services.intent.classify_and_enrich.load_classify_context",
            new_callable=AsyncMock,
            return_value=type("Ctx", (), {"catalog": catalog, "google_sheet_rows": []})(),
        ), patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=_gate0_info(),
        ), patch(
            "services.intent.session_continuation.resolve_session_continuation",
            new_callable=AsyncMock,
        ) as mock_s01, patch(
            "services.intent.enrich.apply_post_classification_pipeline",
            new_callable=AsyncMock,
        ) as mock_enrich, patch(
            "services.labeling.catalog_helpers.fetch_semantics_map",
            new_callable=AsyncMock,
            return_value={},
        ):
            out = await classify_and_enrich(
                prompt,
                "user-1",
                conversation_history=[
                    {
                        "metadata": {
                            "event_type": "extraction_completed",
                            "job_id": "job-1",
                        }
                    }
                ],
            )
            mock_s01.assert_not_called()
            mock_enrich.assert_not_called()
            return out

    out = asyncio.run(_run())
    assert out.get("plan_route") == "info_path"
    assert out.get("intent") == "info"
    assert "execution_plan" not in out


def test_classify_and_enrich_pipeline_still_calls_s01():
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
            return_value=_gate0_pipeline(),
        ), patch(
            "services.intent.pipeline_turn_routing.resolve_pipeline_turn",
            new_callable=AsyncMock,
            return_value=PipelineTurnDecision(
                branch="fresh",
                confidence=0.95,
                decision_source="llm",
            ),
        ), patch(
            "services.intent.classify_and_enrich.get_active_intent_state",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "services.intent.classify_and_enrich.latest_extraction_session_for_classifier",
            return_value=None,
        ), patch(
            "services.intent.classify_and_enrich.resolve_session_continuation",
            new_callable=AsyncMock,
            return_value=SessionContinuationDecision(
                session_action="no_prior_job",
                continuation_phase="none",
                reuse_job_id=False,
                decision_source="no_job",
                confidence=1.0,
            ),
        ) as mock_s01, patch(
            "services.intent.route_turn.route_planning_path",
            new_callable=AsyncMock,
            return_value="fresh_path",
        ), patch(
            "services.intent.classify_and_enrich.apply_post_classification_pipeline",
            new_callable=AsyncMock,
            side_effect=lambda c, **kw: {**c, "plan_route": "fresh_path"},
        ) as mock_enrich:
            await classify_and_enrich(
                "extract contacts from SF 1",
                "user-1",
            )
            mock_s01.assert_called_once()
            mock_enrich.assert_called_once()

    asyncio.run(_run())
