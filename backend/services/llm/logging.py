"""Async-safe invocation logging to llm_invocations."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from db.connection import database

logger = logging.getLogger(__name__)


async def log_invocation(
    *,
    task_key: str,
    provider_id: str,
    model_id: str,
    latency_ms: int,
    success: bool,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    error_code: Optional[str] = None,
    json_parse_ok: Optional[bool] = None,
    user_id: Optional[str] = None,
    job_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> None:
    try:
        await database.execute(
            """
            INSERT INTO llm_invocations (
                task_key, provider_id, model_id, latency_ms,
                tokens_in, tokens_out, success, error_code, json_parse_ok,
                user_id, job_id, conversation_id
            ) VALUES (
                :task_key, :provider_id, :model_id, :latency_ms,
                :tokens_in, :tokens_out, :success, :error_code, :json_parse_ok,
                :user_id, :job_id, :conversation_id
            )
            """,
            {
                "task_key": task_key,
                "provider_id": provider_id,
                "model_id": model_id,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "success": success,
                "error_code": error_code,
                "json_parse_ok": json_parse_ok,
                "user_id": user_id,
                "job_id": job_id,
                "conversation_id": conversation_id,
            },
        )
    except Exception as e:
        logger.debug("[LLM] log_invocation failed: %s", e)


def log_invocation_background(**kwargs) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(log_invocation(**kwargs))
    except RuntimeError:
        pass
