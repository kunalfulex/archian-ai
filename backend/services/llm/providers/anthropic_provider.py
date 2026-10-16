from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from services.llm.types import LLMResponse


def _split_messages(messages: List[Dict[str, str]]) -> tuple[Optional[str], List[Dict[str, str]]]:
    system_parts: List[str] = []
    rest: List[Dict[str, str]] = []
    for m in messages:
        role = (m.get("role") or "user").lower()
        content = m.get("content") or ""
        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant"):
            rest.append({"role": role, "content": content})
        else:
            rest.append({"role": "user", "content": content})
    system = "\n\n".join(system_parts).strip() or None
    if not rest:
        rest = [{"role": "user", "content": ""}]
    return system, rest


class _CompatMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _CompatChoice:
    def __init__(self, content: str) -> None:
        self.message = _CompatMessage(content)


class _CompatResponse:
    """Mimics OpenAI chat completion for legacy code paths."""

    def __init__(self, content: str, raw: Any = None) -> None:
        self.choices = [_CompatChoice(content)]
        self._raw = raw


async def anthropic_complete(
    *,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
    api_key: str,
    provider_id: str = "anthropic",
) -> LLMResponse:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    system, msgs = _split_messages(messages)
    max_tok = max_tokens if max_tokens is not None else 4096

    started = time.perf_counter()

    def _call():
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tok,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        return client.messages.create(**kwargs)

    raw = await asyncio.to_thread(_call)
    latency_ms = int((time.perf_counter() - started) * 1000)
    parts = []
    for block in raw.content:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    content = "".join(parts).strip()
    tin = getattr(getattr(raw, "usage", None), "input_tokens", None)
    tout = getattr(getattr(raw, "usage", None), "output_tokens", None)
    return LLMResponse(
        content=content,
        provider_id=provider_id,
        model_id=model,
        latency_ms=latency_ms,
        tokens_in=tin,
        tokens_out=tout,
        raw=raw,
    )


def anthropic_to_openai_compat(resp: LLMResponse) -> _CompatResponse:
    return _CompatResponse(resp.content, resp.raw)
