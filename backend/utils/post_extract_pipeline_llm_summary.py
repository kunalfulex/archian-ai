"""
Natural-language summary of a completed post-extract pipeline (one LLM call).

Uses structured facts from rule deltas + row counts to reduce hallucination.
Fallback: join deterministic per-step summaries from summarize_operation_delta.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional


def pipeline_llm_summary_enabled() -> bool:
    v = (os.getenv("POST_EXTRACT_PIPELINE_LLM_SUMMARY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _truncate_json(payload: Any, max_chars: int = 2800) -> str:
    try:
        s = json.dumps(payload, default=str, ensure_ascii=False)
    except Exception:
        s = str(payload)
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 20] + "\n… [truncated]"


def deterministic_pipeline_narrative(
    *,
    operations_applied: List[str],
    row_count_before: int,
    row_count_after: int,
    step_summaries: List[str],
) -> str:
    parts = [s.strip() for s in step_summaries if isinstance(s, str) and s.strip()]
    unique: List[str] = []
    seen = set()
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    body = " ".join(unique) if unique else f"Applied: {', '.join(operations_applied) or 'pipeline'}."
    if row_count_before != row_count_after:
        body += f" Row count: {row_count_before} → {row_count_after}."
    return body.strip()


async def generate_pipeline_narrative_llm(
    *,
    user_prompt: str,
    operations_applied: List[str],
    row_count_before: int,
    row_count_after: int,
    extraction_rules: Optional[Dict[str, Any]],
    source_schema: Optional[List[str]],
    step_summaries: List[str],
    timeout_seconds: float = 12.0,
) -> Optional[str]:
    """Returns plain-language narrative or None on skip/failure."""
    from services.llm import tasks as llm_tasks
    from services.llm.service import task_has_api_key

    if not pipeline_llm_summary_enabled() or not await task_has_api_key(llm_tasks.PIPELINE_SUMMARY):
        return None

    model = (os.getenv("POST_EXTRACT_PIPELINE_SUMMARY_MODEL") or "gpt-4o-mini").strip()

    rules_blob = _truncate_json(extraction_rules or {})
    schema_sample = (source_schema or [])[:48]
    steps_lines = "\n".join(
        f"{i + 1}. {s}" for i, s in enumerate(step_summaries) if isinstance(s, str) and s.strip()
    ) or "(no per-step summary)"

    up = (user_prompt or "").strip()[:1200]

    prompt = f"""You write short, friendly summaries for business users who ran a data cleanup pipeline.

Facts (do not contradict these numbers or invent columns):
- User request (may be partial): {up!r}
- Operations run in order: {operations_applied}
- Row count before applying rules: {row_count_before}
- Row count after applying rules: {row_count_after}
- Final column names (sample): {schema_sample}

Technical notes from the engine (may be repetitive — merge into one coherent story):
{steps_lines}

Merged extraction_rules JSON (authoritative for filters/transforms — do not claim anything not supported here):
{rules_blob}

Write 2–4 sentences in plain English:
- What changed in the dataset (columns dropped/combined, filters like invalid emails).
- Mention the row count change only using the numbers above.
- No markdown, no bullet symbols, no JSON."""

    try:
        from services.llm import tasks as llm_tasks
        from services.llm.service import llm_complete_openai_compat

        async def _call():
            response = await llm_complete_openai_compat(
                llm_tasks.PIPELINE_SUMMARY,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You summarize data preparation steps accurately and concisely. "
                            "Never invent field names, counts, or operations not supported by the facts."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=220,
                temperature=0.35,
            )
            text = (response.choices[0].message.content or "").strip()
            text = re.sub(r"^[\"']|[\"']$", "", text)
            return text if text else None

        return await asyncio.wait_for(_call(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        print("⚠️ [PIPELINE_SUMMARY_LLM] timed out, using deterministic narrative")
        return None
    except Exception as e:
        print(f"⚠️ [PIPELINE_SUMMARY_LLM] failed: {e}")
        return None


async def build_pipeline_narrative(
    *,
    user_prompt: str,
    operations_applied: List[str],
    row_count_before: int,
    row_count_after: int,
    extraction_rules: Optional[Dict[str, Any]],
    source_schema: Optional[List[str]],
    step_summaries: List[str],
) -> str:
    llm_text = await generate_pipeline_narrative_llm(
        user_prompt=user_prompt,
        operations_applied=operations_applied,
        row_count_before=row_count_before,
        row_count_after=row_count_after,
        extraction_rules=extraction_rules,
        source_schema=source_schema,
        step_summaries=step_summaries,
    )
    if llm_text:
        return llm_text
    return deterministic_pipeline_narrative(
        operations_applied=operations_applied,
        row_count_before=row_count_before,
        row_count_after=row_count_after,
        step_summaries=step_summaries,
    )
