from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.connection import database
from services.pipeline_learning.embedding import embed_text, vector_to_pg_literal
from services.pipeline_learning.store import PipelineLearningStore


def pipeline_learning_top_k() -> int:
    try:
        return max(1, int(os.getenv("PIPELINE_LEARNING_TOP_K", "5")))
    except ValueError:
        return 5


def _recency_decay(created_at: Any) -> float:
    if created_at is None:
        return 0.9
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return 0.9
    if not hasattr(created_at, "timestamp"):
        return 0.9
    age_days = (datetime.now(timezone.utc) - created_at.replace(tzinfo=timezone.utc)).total_seconds() / 86400
    return max(0.5, 1.0 - age_days * 0.01)


def _outcome_weight(success_rate: Optional[float], *, min_runs: int, user_chunk_count: int) -> float:
    if success_rate is None:
        return 1.0
    if success_rate < 0.3 and user_chunk_count >= min_runs:
        return 0.0
    return 0.5 + 0.5 * float(success_rate)


async def retrieve_similar_chunks(
    *,
    user_id: str,
    query_text: str,
    stage: Optional[str] = None,
    chunk_type: Optional[str] = None,
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    top_k: Optional[int] = None,
    caller: Optional[str] = None,
    conversation_id: Optional[str] = None,
    log_retrieval: bool = True,
    scope: str = "user",
) -> List[Dict[str, Any]]:
    """Vector search with migration-weighted ranking."""
    if scope == "platform":
        return await _retrieve_platform_chunks(
            user_id=user_id,
            query_text=query_text,
            stage=stage,
            chunk_type=chunk_type,
            source_type=source_type,
            destination_type=destination_type,
            top_k=top_k,
            caller=caller,
            conversation_id=conversation_id,
            log_retrieval=log_retrieval,
        )
    await PipelineLearningStore.ensure_tables()
    k = top_k or pipeline_learning_top_k()
    vec = await embed_text(query_text)
    if not vec:
        fallback = await _fallback_recent(
            user_id=user_id,
            stage=stage,
            chunk_type=chunk_type,
            top_k=k,
        )
        if log_retrieval and caller:
            try:
                await PipelineLearningStore.log_retrieval(
                    user_id=user_id,
                    caller=caller,
                    query_text=query_text,
                    chunks=fallback,
                    conversation_id=conversation_id,
                    stage=stage,
                    chunk_type=chunk_type,
                    source_type=source_type,
                    destination_type=destination_type,
                )
            except Exception:
                pass
        return fallback

    vec_lit = vector_to_pg_literal(vec)
    user_chunk_count = await PipelineLearningStore.count_user_chunks(user_id)

    filters = ["user_id = :user_id", "embedding IS NOT NULL"]
    values: Dict[str, Any] = {"user_id": str(user_id), "limit": k * 3}

    if stage:
        filters.append("stage = :stage")
        values["stage"] = stage
    if chunk_type:
        filters.append("chunk_type = :chunk_type")
        values["chunk_type"] = chunk_type
    if source_type:
        filters.append("(metadata->>'source_type' IS NULL OR metadata->>'source_type' = :source_type)")
        values["source_type"] = source_type
    if destination_type:
        filters.append(
            "(metadata->>'destination_type' IS NULL OR metadata->>'destination_type' = :destination_type)"
        )
        values["destination_type"] = destination_type

    where = " AND ".join(filters)
    rows = await database.fetch_all(
        f"""
        SELECT id, pipeline_run_id, stage, chunk_type, content_text, metadata,
               created_at,
               1 - (embedding <=> '{vec_lit}'::vector) AS similarity
        FROM learning_chunks
        WHERE {where}
        ORDER BY embedding <=> '{vec_lit}'::vector
        LIMIT :limit
        """,
        values=values,
    )

    scored: List[Dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        meta = r.get("metadata") or {}
        if isinstance(meta, str):
            import json

            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        sr = meta.get("success_rate")
        ow = _outcome_weight(sr, min_runs=3, user_chunk_count=user_chunk_count)
        if ow <= 0:
            continue
        sim = float(r.get("similarity") or 0)
        decay = _recency_decay(r.get("created_at"))
        r["score"] = sim * ow * decay
        r["metadata"] = meta
        scored.append(r)

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    result = scored[:k]
    if log_retrieval and caller:
        try:
            await PipelineLearningStore.log_retrieval(
                user_id=user_id,
                caller=caller,
                query_text=query_text,
                chunks=result,
                conversation_id=conversation_id,
                stage=stage,
                chunk_type=chunk_type,
                source_type=source_type,
                destination_type=destination_type,
            )
        except Exception:
            pass
    return result


def _redact_platform_chunk(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip user-specific fields from cross-user retrieval results."""
    out = dict(row)
    out.pop("user_id", None)
    meta = dict(out.get("metadata") or {})
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    for key in ("job_id", "conversation_id", "prompt", "goal"):
        meta.pop(key, None)
    meta["retrieval_scope"] = "platform"
    meta["anonymized"] = True
    out["metadata"] = meta
    text = str(out.get("content_text") or "")
    lines = []
    for line in text.splitlines():
        if line.startswith("goal:") or line.startswith("job_id:"):
            continue
        lines.append(line)
    out["content_text"] = "\n".join(lines)
    return out


async def _retrieve_platform_chunks(
    *,
    user_id: str,
    query_text: str,
    stage: Optional[str] = None,
    chunk_type: Optional[str] = None,
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    top_k: Optional[int] = None,
    caller: Optional[str] = None,
    conversation_id: Optional[str] = None,
    log_retrieval: bool = True,
) -> List[Dict[str, Any]]:
    from services.pipeline_learning.trace_retrieval_policy import platform_trace_min_success_rate

    await PipelineLearningStore.ensure_tables()
    k = top_k or pipeline_learning_top_k()
    vec = await embed_text(query_text)
    min_sr = platform_trace_min_success_rate()
    platform_types = ("platform_tool_pattern", "tool_trace")
    type_filter = chunk_type if chunk_type in platform_types else None

    if not vec:
        return []

    vec_lit = vector_to_pg_literal(vec)
    filters = [
        "user_id != :user_id",
        "embedding IS NOT NULL",
        f"chunk_type IN ({', '.join(repr(t) for t in platform_types)})",
        """(
            (metadata->>'success_rate') IS NULL
            OR (metadata->>'success_rate')::float >= :min_sr
            OR chunk_type = 'platform_tool_pattern'
        )""",
        "(metadata->>'partial') IS DISTINCT FROM 'true'",
    ]
    values: Dict[str, Any] = {
        "user_id": str(user_id),
        "limit": k * 4,
        "min_sr": min_sr,
    }
    if stage:
        filters.append("stage = :stage")
        values["stage"] = stage
    if type_filter:
        filters.append("chunk_type = :chunk_type")
        values["chunk_type"] = type_filter
    if source_type:
        filters.append(
            "(metadata->>'source_type' IS NULL OR metadata->>'source_type' = :source_type)"
        )
        values["source_type"] = source_type
    if destination_type:
        filters.append(
            "(metadata->>'destination_type' IS NULL OR metadata->>'destination_type' = :destination_type)"
        )
        values["destination_type"] = destination_type

    where = " AND ".join(filters)
    rows = await database.fetch_all(
        f"""
        SELECT id, pipeline_run_id, stage, chunk_type, content_text, metadata,
               created_at,
               1 - (embedding <=> '{vec_lit}'::vector) AS similarity
        FROM learning_chunks
        WHERE {where}
        ORDER BY embedding <=> '{vec_lit}'::vector
        LIMIT :limit
        """,
        values=values,
    )

    scored: List[Dict[str, Any]] = []
    for row in rows or []:
        r = _redact_platform_chunk(dict(row))
        meta = r.get("metadata") or {}
        sr = meta.get("success_rate")
        if sr is not None:
            try:
                if float(sr) < min_sr:
                    continue
            except (TypeError, ValueError):
                pass
        sim = float(r.get("similarity") or 0)
        decay = _recency_decay(r.get("created_at"))
        r["score"] = sim * decay
        scored.append(r)

    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    result = scored[:k]
    log_caller = f"{caller}:platform" if caller else "platform"
    if log_retrieval and log_caller:
        try:
            await PipelineLearningStore.log_retrieval(
                user_id=user_id,
                caller=log_caller[:64],
                query_text=query_text,
                chunks=result,
                conversation_id=conversation_id,
                stage=stage,
                chunk_type=chunk_type or "platform_tool_pattern",
                source_type=source_type,
                destination_type=destination_type,
            )
        except Exception:
            pass
    return result


async def retrieve_with_policy(
    *,
    user_id: str,
    query_text: str,
    stage: Optional[str] = None,
    chunk_type: Optional[str] = None,
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    top_k: Optional[int] = None,
    caller: Optional[str] = None,
    conversation_id: Optional[str] = None,
    log_retrieval: bool = True,
) -> List[Dict[str, Any]]:
    """Merge user-scoped and platform-scoped retrieval per AGENT_TRACE_RETRIEVAL_POLICY."""
    from services.pipeline_learning.trace_retrieval_policy import (
        include_user_retrieval,
        platform_retrieval_enabled,
        trace_retrieval_policy,
    )

    policy = trace_retrieval_policy()
    k = top_k or pipeline_learning_top_k()
    merged: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _append_unique(chunks: List[Dict[str, Any]]) -> None:
        for ch in chunks:
            cid = str(ch.get("id") or "")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            merged.append(ch)
            if len(merged) >= k:
                break

    if include_user_retrieval():
        user_chunks = await retrieve_similar_chunks(
            user_id=user_id,
            query_text=query_text,
            stage=stage,
            chunk_type=chunk_type,
            source_type=source_type,
            destination_type=destination_type,
            top_k=k,
            caller=f"{caller}:user" if caller else "user",
            conversation_id=conversation_id,
            log_retrieval=log_retrieval,
            scope="user",
        )
        _append_unique(user_chunks)

    if platform_retrieval_enabled() and len(merged) < k:
        platform_chunks = await _retrieve_platform_chunks(
            user_id=user_id,
            query_text=query_text,
            stage=stage,
            chunk_type=chunk_type,
            source_type=source_type,
            destination_type=destination_type,
            top_k=k - len(merged),
            caller=caller,
            conversation_id=conversation_id,
            log_retrieval=log_retrieval,
        )
        _append_unique(platform_chunks)

    for ch in merged:
        ch.setdefault("retrieval_policy", policy)
    return merged[:k]


async def _fallback_recent(
    *,
    user_id: str,
    stage: Optional[str],
    chunk_type: Optional[str],
    top_k: int,
) -> List[Dict[str, Any]]:
    filters = ["user_id = :user_id"]
    values: Dict[str, Any] = {"user_id": str(user_id), "limit": top_k}
    if stage:
        filters.append("stage = :stage")
        values["stage"] = stage
    if chunk_type:
        filters.append("chunk_type = :chunk_type")
        values["chunk_type"] = chunk_type
    where = " AND ".join(filters)
    rows = await database.fetch_all(
        f"""
        SELECT id, pipeline_run_id, stage, chunk_type, content_text, metadata, created_at
        FROM learning_chunks
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT :limit
        """,
        values=values,
    )
    return [dict(r) for r in rows]
