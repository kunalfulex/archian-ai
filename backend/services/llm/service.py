"""Unified LLM completion — resolves task config from DB and calls provider adapters."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from services.llm.config_store import get_task_config
from services.llm.keys import provider_env_key, resolve_api_key
from services.llm.logging import log_invocation_background
from services.llm.providers.anthropic_provider import (
    anthropic_complete,
    anthropic_to_openai_compat,
)
from services.llm.providers.openai_provider import openai_complete
from services.llm.types import LLMRequest, LLMResponse, TaskConfig

logger = logging.getLogger(__name__)

_GLOBAL_DISABLED = (os.getenv("LLM_GLOBAL_DISABLED") or "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


async def _call_provider(
    cfg: TaskConfig,
    *,
    provider_id: str,
    model_id: str,
    module_type: str,
    env_key_name: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: Optional[int],
    response_format: Optional[Dict[str, Any]] = None,
) -> LLMResponse:
    api_key = resolve_api_key(provider_id, env_key_name=env_key_name)
    if not api_key:
        raise RuntimeError(f"API key not configured for provider {provider_id} ({env_key_name})")

    if module_type == "anthropic_messages":
        return await anthropic_complete(
            model=model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            provider_id=provider_id,
        )
    return await openai_complete(
        model=model_id,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
        provider_id=provider_id,
        response_format=response_format,
    )


def _task_has_api_key(cfg: TaskConfig) -> bool:
    if resolve_api_key(cfg.provider_id, env_key_name=cfg.env_key_name):
        return True
    if cfg.fallback_provider_id and cfg.fallback_model_id:
        return resolve_api_key(cfg.fallback_provider_id) is not None
    return False


async def task_has_api_key(task_key: str) -> bool:
    """True when the task's primary or fallback provider has an API key."""
    if _GLOBAL_DISABLED:
        return False
    return _task_has_api_key(await get_task_config(task_key))


async def llm_complete(request: LLMRequest) -> LLMResponse:
    if _GLOBAL_DISABLED:
        raise RuntimeError("LLM calls are globally disabled (LLM_GLOBAL_DISABLED)")

    cfg = await get_task_config(request.task_key)
    if not _task_has_api_key(cfg):
        raise RuntimeError(
            f"Provider {cfg.provider_id} is not configured ({cfg.env_key_name} missing)"
        )

    temperature = request.temperature if request.temperature is not None else cfg.temperature
    max_tokens = request.max_tokens if request.max_tokens is not None else cfg.max_tokens
    messages = request.messages

    last_error: Optional[Exception] = None
    for attempt, use_fallback in enumerate((False, True)):
        if use_fallback:
            if not cfg.fallback_provider_id or not cfg.fallback_model_id:
                break
            pid = cfg.fallback_provider_id
            mid = cfg.fallback_model_id
            mod = cfg.fallback_module_type or (
                "anthropic_messages" if pid == "anthropic" else "openai_chat"
            )
            env = provider_env_key(pid)
        else:
            pid, mid, mod, env = cfg.provider_id, cfg.model_id, cfg.module_type, cfg.env_key_name

        if not resolve_api_key(pid, env_key_name=env):
            last_error = RuntimeError(f"Missing API key for {pid}")
            continue

        try:
            resp = await _call_provider(
                cfg,
                provider_id=pid,
                model_id=mid,
                module_type=mod,
                env_key_name=env,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=request.response_format,
            )
            log_invocation_background(
                task_key=request.task_key,
                provider_id=resp.provider_id,
                model_id=resp.model_id,
                latency_ms=resp.latency_ms,
                success=True,
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
                user_id=request.user_id,
                job_id=request.job_id,
                conversation_id=request.conversation_id,
            )
            return resp
        except Exception as e:
            last_error = e
            logger.warning(
                "[LLM] task=%s provider=%s attempt=%s failed: %s",
                request.task_key,
                pid,
                attempt,
                e,
            )
            log_invocation_background(
                task_key=request.task_key,
                provider_id=pid,
                model_id=mid,
                latency_ms=0,
                success=False,
                error_code=type(e).__name__,
                user_id=request.user_id,
                job_id=request.job_id,
                conversation_id=request.conversation_id,
            )

    raise last_error or RuntimeError("LLM call failed")


async def _resolve_call_params(
    task_key: str,
    *,
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> tuple[TaskConfig, str, str, str, str, float, Optional[int]]:
    cfg = await get_task_config(task_key)
    temp = temperature if temperature is not None else cfg.temperature
    mt = max_tokens if max_tokens is not None else cfg.max_tokens
    return cfg, cfg.provider_id, cfg.model_id, cfg.module_type, cfg.env_key_name, temp, mt


async def llm_complete_stream_compat(
    task_key: str,
    *,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    user_id: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """OpenAI-compatible streaming iterator (sync iter over chunks)."""
    if _GLOBAL_DISABLED:
        raise RuntimeError("LLM calls are globally disabled (LLM_GLOBAL_DISABLED)")

    cfg, pid, mid, mod, env, temp, mt = await _resolve_call_params(
        task_key, temperature=temperature, max_tokens=max_tokens
    )
    api_key = resolve_api_key(pid, env_key_name=env)
    if not api_key:
        raise RuntimeError(f"API key not configured for provider {pid} ({env})")

    started = time.perf_counter()

    if mod == "anthropic_messages":
        import anthropic
        from services.llm.providers.anthropic_provider import _split_messages

        system, msgs = _split_messages(messages)
        client = anthropic.Anthropic(api_key=api_key)

        def _anthropic_sync_stream():
            skw: Dict[str, Any] = {
                "model": mid,
                "messages": msgs,
                "max_tokens": mt or 4096,
                "temperature": temp,
            }
            if system:
                skw["system"] = system
            with client.messages.stream(**skw) as stream:
                for text in stream.text_stream:
                    yield _stream_chunk(text)

        stream_result = await asyncio.to_thread(
            lambda: list(_anthropic_sync_stream())
        )
        log_invocation_background(
            task_key=task_key,
            provider_id=pid,
            model_id=mid,
            latency_ms=int((time.perf_counter() - started) * 1000),
            success=True,
            user_id=user_id,
        )

        class _ListStream:
            def __iter__(self):
                return iter(stream_result)

        return _ListStream()

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    kw: Dict[str, Any] = {
        "model": mid,
        "messages": messages,
        "temperature": temp,
        "stream": True,
    }
    if mt is not None:
        kw["max_tokens"] = mt

    def _create():
        return client.chat.completions.create(**kw)

    stream = await asyncio.to_thread(_create)
    log_invocation_background(
        task_key=task_key,
        provider_id=pid,
        model_id=mid,
        latency_ms=int((time.perf_counter() - started) * 1000),
        success=True,
        user_id=user_id,
    )
    return stream


def _stream_chunk(text: str) -> Any:
    class _Delta:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    return _Chunk(text)


async def llm_complete_openai_compat(
    task_key: str,
    *,
    messages: List[Dict[str, str]],
    user_id: Optional[str] = None,
    job_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs: Any,
) -> Any:
    """
    Drop-in replacement for OpenAI chat.completions.create result.
    Ignores legacy kwargs except temperature/max_tokens/stream.
    """
    if kwargs.get("stream"):
        return await llm_complete_stream_compat(
            task_key,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens or kwargs.get("max_tokens"),
            user_id=user_id,
        )

    resp = await llm_complete(
        LLMRequest(
            task_key=task_key,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens or kwargs.get("max_tokens"),
            user_id=user_id,
            job_id=job_id,
            conversation_id=conversation_id,
            response_format=kwargs.get("response_format"),
        )
    )
    cfg = await get_task_config(task_key)
    if cfg.module_type == "anthropic_messages" or resp.provider_id == "anthropic":
        return anthropic_to_openai_compat(resp)
    return resp.raw if resp.raw is not None else anthropic_to_openai_compat(resp)


async def get_task_batch_settings(task_key: str) -> Dict[str, int]:
    cfg = await get_task_config(task_key)
    return {
        "max_concurrent": cfg.max_concurrent,
        "batch_size": cfg.batch_size,
    }
