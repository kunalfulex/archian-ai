"""API keys resolve through services.llm.keys; any configured provider (BYOM) can serve a task."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.llm import service as llm_service
from services.llm.keys import provider_env_key, resolve_api_key
from services.llm.types import LLMRequest, LLMResponse, TaskConfig

KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CUSTOM_LLM_KEY", "MISTRAL_API_KEY")


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch):
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def _cfg(**overrides) -> TaskConfig:
    base = dict(
        task_key="general",
        display_name="general",
        provider_id="openai",
        model_id="gpt-4o-mini",
        module_type="openai_chat",
        env_key_name="OPENAI_API_KEY",
    )
    base.update(overrides)
    return TaskConfig(**base)


def test_resolve_api_key_reads_provider_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-openai  ")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    assert resolve_api_key("openai") == "sk-openai"
    assert resolve_api_key("anthropic") == "sk-ant"


def test_resolve_api_key_missing_or_blank(monkeypatch):
    assert resolve_api_key("openai") is None
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert resolve_api_key("openai") is None


def test_resolve_api_key_env_override_and_unknown_provider(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_KEY", "k1")
    monkeypatch.setenv("MISTRAL_API_KEY", "k2")
    assert resolve_api_key("openai", env_key_name="CUSTOM_LLM_KEY") == "k1"
    assert provider_env_key("mistral") == "MISTRAL_API_KEY"
    assert resolve_api_key("mistral") == "k2"


@pytest.mark.parametrize(
    "env, cfg_overrides, expected",
    [
        ({}, {}, False),
        ({"OPENAI_API_KEY": "x"}, {}, True),
        ({"ANTHROPIC_API_KEY": "x"}, {}, False),
        (
            {"ANTHROPIC_API_KEY": "x"},
            {"fallback_provider_id": "anthropic", "fallback_model_id": "claude"},
            True,
        ),
    ],
)
def test_task_has_api_key(monkeypatch, env, cfg_overrides, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with patch.object(llm_service, "get_task_config", new=AsyncMock(return_value=_cfg(**cfg_overrides))):
        assert asyncio.run(llm_service.task_has_api_key("general")) is expected


def test_llm_complete_uses_fallback_when_only_fallback_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    cfg = _cfg(fallback_provider_id="anthropic", fallback_model_id="claude-x")
    resp = LLMResponse(content="ok", provider_id="anthropic", model_id="claude-x", latency_ms=1)
    call = AsyncMock(return_value=resp)
    with patch.object(llm_service, "get_task_config", new=AsyncMock(return_value=cfg)), patch.object(
        llm_service, "_call_provider", new=call
    ), patch.object(llm_service, "log_invocation_background"):
        out = asyncio.run(
            llm_service.llm_complete(LLMRequest(task_key="general", messages=[{"role": "user", "content": "hi"}]))
        )
    assert out is resp
    assert call.await_count == 1
    kwargs = call.await_args.kwargs
    assert kwargs["provider_id"] == "anthropic"
    assert kwargs["env_key_name"] == "ANTHROPIC_API_KEY"
    assert kwargs["module_type"] == "anthropic_messages"


def test_llm_complete_raises_when_no_key():
    with patch.object(llm_service, "get_task_config", new=AsyncMock(return_value=_cfg())):
        with pytest.raises(RuntimeError, match="not configured"):
            asyncio.run(llm_service.llm_complete(LLMRequest(task_key="general", messages=[])))


def test_async_openai_call_requires_task():
    from services.architect_agent import async_openai_call

    with pytest.raises(ValueError, match="task"):
        asyncio.run(async_openai_call(None, model="gpt-4o", messages=[]))


def test_async_openai_call_routes_through_llm_service():
    from services.architect_agent import async_openai_call

    with patch(
        "services.llm.service.llm_complete_openai_compat", new=AsyncMock(return_value="resp")
    ) as compat:
        out = asyncio.run(
            async_openai_call(None, task="general", model="ignored", messages=[{"role": "user", "content": "x"}])
        )
    assert out == "resp"
    assert compat.await_args.args == ("general",)
    assert compat.await_args.kwargs["messages"] == [{"role": "user", "content": "x"}]


def test_embeddings_skip_without_openai_key():
    from services.pipeline_learning.embedding import embed_texts

    assert asyncio.run(embed_texts(["hello"])) == []
