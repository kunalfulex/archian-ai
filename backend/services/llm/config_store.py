"""Load LLM task config from Postgres with in-process cache."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from db.connection import database
from services.llm.keys import resolve_api_key
from services.llm.types import TaskConfig

logger = logging.getLogger(__name__)


def _row_dict(row: Any) -> Dict[str, Any]:
    """databases.Record supports row[key] but not row.get()."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


_CACHE_TTL_SEC = float(os.getenv("LLM_CONFIG_CACHE_TTL_SEC", "60"))
_cache: Dict[str, Any] = {"at": 0.0, "tasks": {}, "providers": {}, "models": {}}
_seeded = False


async def ensure_schema_seeded() -> None:
    """Run seed inserts if tables exist but are empty (dev without migration runner)."""
    global _seeded
    if _seeded:
        return
    try:
        row = await database.fetch_one("SELECT COUNT(*) AS c FROM llm_task_config")
        if row and int(row["c"] or 0) > 0:
            _seeded = True
            return
        migration_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "db", "migrations", "add_llm_model_layer.sql"
        )
        if not os.path.isfile(migration_path):
            _seeded = True
            return
        # Tables missing — caller should run migration SQL manually
        _seeded = True
    except Exception as e:
        logger.debug("[LLM] ensure_schema_seeded skipped: %s", e)
        _seeded = True


def _invalidate_cache() -> None:
    _cache["at"] = 0.0
    _cache["tasks"] = {}
    _cache["providers"] = {}
    _cache["models"] = {}


async def _refresh_cache_if_needed() -> None:
    now = time.time()
    if _cache["tasks"] and (now - _cache["at"]) < _CACHE_TTL_SEC:
        return
    await ensure_schema_seeded()
    try:
        providers = await database.fetch_all(
            "SELECT id, display_name, module_type, env_key_name, is_active FROM llm_providers WHERE is_active = TRUE"
        )
        models = await database.fetch_all(
            """
            SELECT m.provider_id, m.model_id, m.display_name, p.module_type, p.env_key_name
            FROM llm_models m
            JOIN llm_providers p ON p.id = m.provider_id
            WHERE m.is_active = TRUE AND p.is_active = TRUE
            """
        )
        tasks = await database.fetch_all(
            """
            SELECT
                t.task_key, t.display_name, t.provider_id, t.model_id,
                t.fallback_provider_id, t.fallback_model_id,
                t.temperature, t.max_tokens, t.max_concurrent, t.batch_size, t.is_active,
                p.module_type, p.env_key_name
            FROM llm_task_config t
            JOIN llm_providers p ON p.id = t.provider_id
            WHERE t.is_active = TRUE
            """
        )
        _cache["providers"] = {_row_dict(r)["id"]: _row_dict(r) for r in providers}
        _cache["models"] = {
            (_row_dict(r)["provider_id"], _row_dict(r)["model_id"]): _row_dict(r) for r in models
        }
        _cache["tasks"] = {}
        for raw in tasks:
            r = _row_dict(raw)
            fb_mod = None
            if r.get("fallback_provider_id"):
                fp = _cache["providers"].get(r["fallback_provider_id"])
                if fp:
                    fb_mod = fp.get("module_type")
            _cache["tasks"][r["task_key"]] = TaskConfig(
                task_key=r["task_key"],
                display_name=r["display_name"],
                provider_id=r["provider_id"],
                model_id=r["model_id"],
                module_type=r["module_type"],
                env_key_name=r["env_key_name"],
                temperature=float(r["temperature"] or 0),
                max_tokens=r["max_tokens"],
                max_concurrent=int(r["max_concurrent"] or 5),
                batch_size=int(r["batch_size"] or 1),
                fallback_provider_id=r.get("fallback_provider_id"),
                fallback_model_id=r.get("fallback_model_id"),
                fallback_module_type=fb_mod,
                is_active=bool(r["is_active"]),
            )
        _cache["at"] = now
    except Exception as e:
        logger.warning(
            "[LLM] config cache refresh failed, using env fallback: %s: %s",
            type(e).__name__,
            e,
        )
        _apply_env_fallback_cache()


def _apply_env_fallback_cache() -> None:
    """When DB unavailable, minimal in-memory defaults."""
    from services.llm import tasks as T

    default_model = (os.getenv("LLM_FALLBACK_MODEL") or "gpt-4o-mini").strip()
    provider = (os.getenv("LLM_FALLBACK_PROVIDER") or "openai").strip()
    _cache["providers"] = {
        "openai": {
            "id": "openai",
            "module_type": "openai_chat",
            "env_key_name": "OPENAI_API_KEY",
        },
        "anthropic": {
            "id": "anthropic",
            "module_type": "anthropic_messages",
            "env_key_name": "ANTHROPIC_API_KEY",
        },
    }
    task_keys = list(T.ALL_TASK_KEYS)
    _cache["tasks"] = {}
    for key in task_keys:
        model = default_model
        if key == T.GENERAL_CHAT:
            model = "gpt-4"
        elif key in (T.SQL_QUERY_GENERATION, T.SQL_CONNECTION_DETECT, T.SCHEMA_CATEGORIZATION):
            model = "gpt-4o" if key != T.SCHEMA_CATEGORIZATION else "gpt-4o"
        _cache["tasks"][key] = TaskConfig(
            task_key=key,
            display_name=key,
            provider_id=provider,
            model_id=model,
            module_type="openai_chat",
            env_key_name="OPENAI_API_KEY",
        )
    _cache["at"] = time.time()


async def get_task_config(task_key: str) -> TaskConfig:
    await _refresh_cache_if_needed()
    cfg = _cache["tasks"].get(task_key)
    if cfg:
        return cfg
    cfg = _cache["tasks"].get("general")
    if cfg:
        return cfg
    _apply_env_fallback_cache()
    return _cache["tasks"].get(task_key) or _cache["tasks"].get("general") or TaskConfig(
        task_key=task_key,
        display_name=task_key,
        provider_id="openai",
        model_id="gpt-4o-mini",
        module_type="openai_chat",
        env_key_name="OPENAI_API_KEY",
    )


async def list_all_task_configs() -> List[Dict[str, Any]]:
    await _refresh_cache_if_needed()
    try:
        rows = await database.fetch_all(
            """
            SELECT task_key, display_name, description, provider_id, model_id,
                   fallback_provider_id, fallback_model_id,
                   temperature, max_tokens, max_concurrent, batch_size,
                   is_active, updated_at, notes
            FROM llm_task_config
            ORDER BY display_name
            """
        )
        return [_row_dict(r) for r in rows]
    except Exception:
        return [
            {
                "task_key": c.task_key,
                "display_name": c.display_name,
                "provider_id": c.provider_id,
                "model_id": c.model_id,
            }
            for c in _cache["tasks"].values()
        ]


async def list_providers() -> List[Dict[str, Any]]:
    await _refresh_cache_if_needed()
    try:
        rows = await database.fetch_all(
            """
            SELECT id, display_name, module_type, supports_streaming, supports_json,
                   env_key_name, is_active
            FROM llm_providers ORDER BY display_name
            """
        )
        out = []
        for r in rows:
            d = _row_dict(r)
            d["api_key_configured"] = (
                resolve_api_key(d["id"], env_key_name=d.get("env_key_name")) is not None
            )
            out.append(d)
        return out
    except Exception:
        return []


async def list_models(provider_id: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        if provider_id:
            rows = await database.fetch_all(
                """
                SELECT id, provider_id, model_id, display_name, context_window,
                       max_output_tokens, is_active
                FROM llm_models WHERE provider_id = :pid AND is_active = TRUE
                ORDER BY display_name
                """,
                {"pid": provider_id},
            )
        else:
            rows = await database.fetch_all(
                """
                SELECT id, provider_id, model_id, display_name, context_window,
                       max_output_tokens, is_active
                FROM llm_models WHERE is_active = TRUE
                ORDER BY provider_id, display_name
                """
            )
        return [_row_dict(r) for r in rows]
    except Exception:
        return []


async def update_task_config(
    task_key: str,
    *,
    provider_id: str,
    model_id: str,
    fallback_provider_id: Optional[str] = None,
    fallback_model_id: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    max_concurrent: Optional[int] = None,
    batch_size: Optional[int] = None,
    notes: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> None:
    row = await database.fetch_one(
        "SELECT * FROM llm_task_config WHERE task_key = :k", {"k": task_key}
    )
    if not row:
        raise ValueError(f"Unknown task_key: {task_key}")

    prev = _row_dict(row)

    await database.execute(
        """
        INSERT INTO llm_task_config_history (
            task_key, provider_id, model_id, fallback_provider_id, fallback_model_id,
            temperature, max_tokens, updated_by, notes
        )
        VALUES (
            :task_key, :provider_id, :model_id, :fallback_provider_id, :fallback_model_id,
            :temperature, :max_tokens, :updated_by, :notes
        )
        """,
        {
            "task_key": task_key,
            "provider_id": prev["provider_id"],
            "model_id": prev["model_id"],
            "fallback_provider_id": prev.get("fallback_provider_id"),
            "fallback_model_id": prev.get("fallback_model_id"),
            "temperature": prev.get("temperature"),
            "max_tokens": prev.get("max_tokens"),
            "updated_by": updated_by,
            "notes": notes or prev.get("notes"),
        },
    )

    await database.execute(
        """
        UPDATE llm_task_config SET
            provider_id = :provider_id,
            model_id = :model_id,
            fallback_provider_id = :fallback_provider_id,
            fallback_model_id = :fallback_model_id,
            temperature = COALESCE(:temperature, temperature),
            max_tokens = COALESCE(:max_tokens, max_tokens),
            max_concurrent = COALESCE(:max_concurrent, max_concurrent),
            batch_size = COALESCE(:batch_size, batch_size),
            notes = COALESCE(:notes, notes),
            updated_by = :updated_by,
            updated_at = NOW()
        WHERE task_key = :task_key
        """,
        {
            "task_key": task_key,
            "provider_id": provider_id,
            "model_id": model_id,
            "fallback_provider_id": fallback_provider_id,
            "fallback_model_id": fallback_model_id,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_concurrent": max_concurrent,
            "batch_size": batch_size,
            "notes": notes,
            "updated_by": updated_by,
        },
    )
    _invalidate_cache()


def provider_has_api_key(env_key_name: str) -> bool:
    return bool((os.getenv(env_key_name) or "").strip())
