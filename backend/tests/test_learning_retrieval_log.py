"""Tests for learning retrieval audit log helpers."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.pipeline_learning.retrieve import retrieve_similar_chunks


def test_retrieve_logs_when_caller_set():
    fake_chunks = [
        {
            "id": "chunk-1",
            "chunk_type": "plan_approved",
            "content_text": "example",
            "score": 0.92,
            "similarity": 0.91,
        }
    ]

    async def _run():
        with patch(
            "services.pipeline_learning.retrieve.PipelineLearningStore.ensure_tables",
            new_callable=AsyncMock,
        ), patch(
            "services.pipeline_learning.retrieve.embed_text",
            new_callable=AsyncMock,
            return_value=[0.1] * 1536,
        ), patch(
            "services.pipeline_learning.retrieve.vector_to_pg_literal",
            return_value="[0.1]",
        ), patch(
            "services.pipeline_learning.retrieve.database.fetch_all",
            new_callable=AsyncMock,
            return_value=fake_chunks,
        ), patch(
            "services.pipeline_learning.retrieve.PipelineLearningStore.count_user_chunks",
            new_callable=AsyncMock,
            return_value=5,
        ), patch(
            "services.pipeline_learning.retrieve.PipelineLearningStore.log_retrieval",
            new_callable=AsyncMock,
        ) as log_mock:
            result = await retrieve_similar_chunks(
                user_id="user-1",
                query_text="migrate contacts",
                caller="goal_parser",
                conversation_id="conv-1",
                stage="planning",
            )
            assert len(result) == 1
            log_mock.assert_awaited_once()
            kwargs = log_mock.await_args.kwargs
            assert kwargs["caller"] == "goal_parser"
            assert kwargs["conversation_id"] == "conv-1"
            assert len(kwargs["chunks"]) == 1

    asyncio.run(_run())


def test_retrieve_skips_log_without_caller():
    async def _run():
        with patch(
            "services.pipeline_learning.retrieve.PipelineLearningStore.ensure_tables",
            new_callable=AsyncMock,
        ), patch(
            "services.pipeline_learning.retrieve.embed_text",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "services.pipeline_learning.retrieve._fallback_recent",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "services.pipeline_learning.retrieve.PipelineLearningStore.log_retrieval",
            new_callable=AsyncMock,
        ) as log_mock:
            await retrieve_similar_chunks(
                user_id="user-1",
                query_text="test",
                log_retrieval=True,
            )
            log_mock.assert_not_awaited()

    asyncio.run(_run())
