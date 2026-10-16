"""Feature flags for schema-aware inventory and semantic mapping."""
from __future__ import annotations

import os


def warehouse_llm_fallback_enabled() -> bool:
    return (os.getenv("EGA_WAREHOUSE_LLM_FALLBACK") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def warehouse_llm_model() -> str:
    """Sync fallback; prefer ``warehouse_llm_model_async`` for DB-backed config."""
    return (os.getenv("EGA_WAREHOUSE_LLM_MODEL") or "gpt-4o-mini").strip()


async def warehouse_llm_model_async() -> str:
    try:
        from services.llm.config_store import get_task_config
        from services.llm import tasks as llm_tasks

        cfg = await get_task_config(llm_tasks.EGA_WAREHOUSE_DESIGN)
        return cfg.model_id
    except Exception:
        return warehouse_llm_model()


def warehouse_max_tables() -> int:
    try:
        return max(5, int(os.getenv("EGA_WAREHOUSE_MAX_TABLES", "40")))
    except ValueError:
        return 40


def warehouse_max_columns_per_table() -> int:
    try:
        return max(5, int(os.getenv("EGA_WAREHOUSE_MAX_COLUMNS_PER_TABLE", "50")))
    except ValueError:
        return 50


def linker_llm_max_pairs() -> int:
    try:
        return max(1, int(os.getenv("EGA_LLM_MAX_PAIRS", "25")))
    except ValueError:
        return 25


def linker_ambiguous_low() -> float:
    try:
        return float(os.getenv("EGA_LINKER_AMBIGUOUS_LOW", "0.55"))
    except ValueError:
        return 0.55


def linker_ambiguous_high() -> float:
    try:
        return float(os.getenv("EGA_LINKER_AMBIGUOUS_HIGH", "0.88"))
    except ValueError:
        return 0.88


def entity_linking_llm_enabled() -> bool:
    return (os.getenv("EGA_ENTITY_LINKING_LLM") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def entity_linking_llm_fallback_enabled() -> bool:
    return (os.getenv("EGA_ENTITY_LINKING_LLM_FALLBACK") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def lake_llm_enabled() -> bool:
    return (os.getenv("EGA_LAKE_LLM") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def lake_llm_fallback_enabled() -> bool:
    return (os.getenv("EGA_LAKE_LLM_FALLBACK") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def ega_max_entities_per_member() -> int:
    try:
        return max(3, int(os.getenv("EGA_MAX_ENTITIES_PER_MEMBER", "40")))
    except ValueError:
        return 40


def ega_max_columns_per_entity() -> int:
    try:
        return max(5, int(os.getenv("EGA_MAX_COLUMNS_PER_ENTITY", "30")))
    except ValueError:
        return 30


def ega_mapping_min_confidence() -> float:
    try:
        return float(os.getenv("EGA_MAPPING_MIN_CONFIDENCE", "0.55"))
    except ValueError:
        return 0.55
