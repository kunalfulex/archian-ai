"""Tests for LearningMetricsService.retrieval_effectiveness (P4.1)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.pipeline_learning.metrics import LearningMetricsService


def test_retrieval_effectiveness_shape():
    async def _run():
        caller_rows = [
            {
                "caller": "agent_runtime",
                "total": 10,
                "with_results": 8,
                "avg_result_count": 2.5,
                "avg_top_score": 0.88,
            },
            {
                "caller": "goal_parser",
                "total": 5,
                "with_results": 2,
                "avg_result_count": 1.0,
                "avg_top_score": 0.75,
            },
        ]
        pick_row = {
            "pick_count": 4,
            "first_match_count": 3,
            "trace_replay_pick_count": 1,
            "avg_decision_count": 2.0,
        }
        trace_row = {"trace_count": 12}
        replay_row = {"attempts": 10, "hits": 6, "misses": 4}
        platform_row = {"pattern_count": 5}
        platform_retrieval_row = {"total": 8, "with_results": 6}
        lift_rows = [
            {"bucket": "with_retrieval", "migration_count": 6, "avg_success_rate": 0.97},
            {"bucket": "without_retrieval", "migration_count": 4, "avg_success_rate": 0.82},
        ]

        with patch(
            "services.pipeline_learning.metrics.PipelineLearningStore.ensure_tables",
            new_callable=AsyncMock,
        ), patch(
            "services.pipeline_learning.metrics.database.fetch_all",
            new_callable=AsyncMock,
            side_effect=[caller_rows, lift_rows],
        ), patch(
            "services.pipeline_learning.metrics.database.fetch_one",
            new_callable=AsyncMock,
            side_effect=[pick_row, replay_row, trace_row, platform_row, platform_retrieval_row],
        ):
            data = await LearningMetricsService.retrieval_effectiveness(days=30)
            assert data["agent_retrieval"]["total"] == 10
            assert data["agent_retrieval"]["hit_rate"] == 0.8
            assert data["tool_pick_shadow"]["first_tool_agreement_rate"] == 0.75
            assert data["trace_replay"]["hit_rate"] == 0.6
            assert data["trace_replay"]["hits"] == 6
            assert data["tool_traces_captured"] == 12
            assert data["migration_success_lift_delta"] == 0.15
            assert data["trace_retrieval"]["policy"] == "user_then_platform"
            assert data["trace_retrieval"]["platform_patterns_captured"] == 5
            assert len(data["by_caller"]) == 2

        asyncio.run(_run())


def test_platform_retrieval_query_bind_params():
    """LIKE patterns with ':platform' must not be parsed as extra bind params."""
    from sqlalchemy import text

    from services.pipeline_learning.metrics import _PERIOD_FILTER

    sql = f"""
            SELECT COUNT(*) AS total
            FROM learning_retrieval_log
            WHERE caller LIKE :platform_caller_suffix
            AND created_at >= {_PERIOD_FILTER}
            """
    stmt = text(sql)
    bound = set(stmt._bindparams.keys())
    assert bound == {"platform_caller_suffix", "days"}
