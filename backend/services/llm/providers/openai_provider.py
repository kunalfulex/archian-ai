from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from services.llm.types import LLMResponse


def _usage_tokens(raw: Any) -> tuple[Optional[int], Optional[int]]:
    usage = getattr(raw, "usage", None)
    if not usage:
        return None, None
    return getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)


async def openai_complete(
    *,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
    api_key: str,
    provider_id: str = "openai",
    response_format: Optional[Dict[str, Any]] = None,
) -> LLMResponse:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format

    started = time.perf_counter()

    def _call():
        return client.chat.completions.create(**kwargs)

    raw = await asyncio.to_thread(_call)
    latency_ms = int((time.perf_counter() - started) * 1000)
    content = (raw.choices[0].message.content or "").strip()
    tin, tout = _usage_tokens(raw)
    return LLMResponse(
        content=content,
        provider_id=provider_id,
        model_id=model,
        latency_ms=latency_ms,
        tokens_in=tin,
        tokens_out=tout,
        raw=raw,
    )


def openai_response_to_compat(raw: Any) -> Any:
    """Return raw OpenAI response object for legacy callers."""
    return raw
