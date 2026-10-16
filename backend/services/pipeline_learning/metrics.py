"""Platform-wide learning / RAG metrics for super-admin dashboard."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import database
from services.pipeline_learning.store import PipelineLearningStore


def _clamp_days(days: int) -> int:
    return max(1, min(int(days), 365))


_PERIOD_FILTER = "NOW() - (CAST(:days AS INTEGER) * INTERVAL '1 day')"


class LearningMetricsService:
    @classmethod
    async def platform_summary(
        cls,
        *,
        days: int = 30,
        user_id: Optional[str] = None,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        await PipelineLearningStore.ensure_tables()
        period_days = _clamp_days(days)
        user_filter = "AND user_id = :user_id" if user_id else ""
        meta_filters: List[str] = []
        base_values: Dict[str, Any] = {}
        if user_id:
            base_values["user_id"] = str(user_id)
        if source_type:
            meta_filters.append(
                "(metadata->>'source_type' IS NULL OR metadata->>'source_type' = :source_type)"
            )
            base_values["source_type"] = source_type
        if destination_type:
            meta_filters.append(
                "(metadata->>'destination_type' IS NULL OR metadata->>'destination_type' = :destination_type)"
            )
            base_values["destination_type"] = destination_type
        meta_sql = (" AND " + " AND ".join(meta_filters)) if meta_filters else ""

        total_chunks = await database.fetch_val(
            f"""
            SELECT COUNT(*) FROM learning_chunks
            WHERE 1=1 {user_filter} {meta_sql}
            """,
            values=base_values,
        )
        period_values = {**base_values, "days": period_days}
        recent_chunks = await database.fetch_val(
            f"""
            SELECT COUNT(*) FROM learning_chunks
            WHERE created_at >= {_PERIOD_FILTER}
            {user_filter} {meta_sql}
            """,
            values=period_values,
        )
        missing_embeddings = await database.fetch_val(
            f"""
            SELECT COUNT(*) FROM learning_chunks
            WHERE embedding IS NULL {user_filter} {meta_sql}
            """,
            values=base_values,
        )
        run_values: Dict[str, Any] = {"days": period_days}
        if user_id:
            run_values["user_id"] = str(user_id)
        run_user = "AND user_id = :user_id" if user_id else ""
        active_runs = await database.fetch_val(
            f"""
            SELECT COUNT(*) FROM pipeline_runs
            WHERE status = 'active' {run_user}
            """,
            values={k: v for k, v in run_values.items() if k != "days"} if user_id else {},
        )
        total_runs = await database.fetch_val(
            f"""
            SELECT COUNT(*) FROM pipeline_runs
            WHERE created_at >= {_PERIOD_FILTER} {run_user}
            """,
            values=run_values,
        )

        retrieval_values = {"days": period_days}
        retrieval_user = ""
        if user_id:
            retrieval_values["user_id"] = str(user_id)
            retrieval_user = "AND user_id = :user_id"
        retrieval_count = await database.fetch_val(
            f"""
            SELECT COUNT(*) FROM learning_retrieval_log
            WHERE created_at >= {_PERIOD_FILTER}
            {retrieval_user}
            """,
            values=retrieval_values,
        )

        label_pending = None
        try:
            label_pending = await database.fetch_val(
                """
                SELECT COUNT(*) FROM label_evidence WHERE processed = FALSE
                """
            )
        except Exception:
            label_pending = None

        return {
            "days": days,
            "user_id": user_id,
            "total_chunks": int(total_chunks or 0),
            "chunks_in_period": int(recent_chunks or 0),
            "chunks_missing_embedding": int(missing_embeddings or 0),
            "active_pipeline_runs": int(active_runs or 0),
            "pipeline_runs_in_period": int(total_runs or 0),
            "retrievals_in_period": int(retrieval_count or 0),
            "label_evidence_pending": int(label_pending) if label_pending is not None else None,
        }

    @classmethod
    async def stage_funnel(
        cls,
        *,
        days: int = 30,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        await PipelineLearningStore.ensure_tables()
        period_days = _clamp_days(days)
        values: Dict[str, Any] = {"days": period_days}
        user_filter = ""
        if user_id:
            user_filter = "AND user_id = :user_id"
            values["user_id"] = str(user_id)

        rows = await database.fetch_all(
            f"""
            SELECT stage, chunk_type, COUNT(*) AS count
            FROM learning_chunks
            WHERE created_at >= {_PERIOD_FILTER}
            {user_filter}
            GROUP BY stage, chunk_type
            ORDER BY stage, count DESC
            """,
            values=values,
        )
        by_stage: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows or []:
            r = dict(row)
            stage = str(r.get("stage") or "unknown")
            by_stage.setdefault(stage, []).append(
                {"chunk_type": r.get("chunk_type"), "count": int(r.get("count") or 0)}
            )

        funnel_keys = [
            "plan_proposed",
            "plan_approved",
            "plan_rejected",
            "mapping_template",
            "migration_outcome",
            "migration_partial",
            "migration_failed",
            "recovery_attempt",
            "recovery_approved",
            "recovery_dismissed",
            "recovery_success",
        ]
        funnel_counts: Dict[str, int] = {}
        flat_rows = await database.fetch_all(
            f"""
            SELECT chunk_type, COUNT(*) AS count
            FROM learning_chunks
            WHERE created_at >= {_PERIOD_FILTER}
            {user_filter}
            GROUP BY chunk_type
            """,
            values=values,
        )
        type_map = {str(dict(r).get("chunk_type")): int(dict(r).get("count") or 0) for r in (flat_rows or [])}
        for key in funnel_keys:
            funnel_counts[key] = type_map.get(key, 0)

        return {
            "days": days,
            "user_id": user_id,
            "by_stage": by_stage,
            "funnel_counts": funnel_counts,
        }

    @classmethod
    async def retrieval_summary(
        cls,
        *,
        days: int = 30,
        user_id: Optional[str] = None,
        caller: Optional[str] = None,
    ) -> Dict[str, Any]:
        await PipelineLearningStore.ensure_tables()
        period_days = _clamp_days(days)
        values: Dict[str, Any] = {"days": period_days}
        filters = [f"created_at >= {_PERIOD_FILTER}"]
        if user_id:
            filters.append("user_id = :user_id")
            values["user_id"] = str(user_id)
        if caller:
            filters.append("caller = :caller")
            values["caller"] = caller
        where = " AND ".join(filters)

        by_caller_rows = await database.fetch_all(
            f"""
            SELECT
                caller,
                COUNT(*) AS retrieval_count,
                AVG(result_count)::float AS avg_result_count,
                AVG(
                    CASE
                        WHEN jsonb_array_length(scores) > 0
                        THEN (scores->>0)::float
                        ELSE NULL
                    END
                ) AS avg_top_score
            FROM learning_retrieval_log
            WHERE {where}
            GROUP BY caller
            ORDER BY retrieval_count DESC
            """,
            values=values,
        )
        by_caller = [
            {
                "caller": dict(r).get("caller"),
                "retrieval_count": int(dict(r).get("retrieval_count") or 0),
                "avg_result_count": float(dict(r)["avg_result_count"])
                if dict(r).get("avg_result_count") is not None
                else None,
                "avg_top_score": float(dict(r)["avg_top_score"])
                if dict(r).get("avg_top_score") is not None
                else None,
            }
            for r in (by_caller_rows or [])
        ]

        type_rows = await database.fetch_all(
            f"""
            SELECT ct AS chunk_type, COUNT(*) AS hit_count
            FROM learning_retrieval_log,
                 LATERAL jsonb_array_elements_text(chunk_types) AS ct
            WHERE {where}
            GROUP BY ct
            ORDER BY hit_count DESC
            LIMIT 20
            """,
            values=values,
        )
        top_chunk_types = [
            {"chunk_type": dict(r).get("chunk_type"), "hit_count": int(dict(r).get("hit_count") or 0)}
            for r in (type_rows or [])
        ]

        return {
            "days": days,
            "user_id": user_id,
            "caller_filter": caller,
            "by_caller": by_caller,
            "top_retrieved_chunk_types": top_chunk_types,
        }

    @classmethod
    async def list_chunks(
        cls,
        *,
        stage: Optional[str] = None,
        chunk_type: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        await PipelineLearningStore.ensure_tables()
        filters: List[str] = ["1=1"]
        values: Dict[str, Any] = {
            "limit": max(1, min(int(limit), 200)),
            "offset": max(0, int(offset)),
        }
        if stage:
            filters.append("stage = :stage")
            values["stage"] = stage
        if chunk_type:
            filters.append("chunk_type = :chunk_type")
            values["chunk_type"] = chunk_type
        if user_id:
            filters.append("user_id = :user_id")
            values["user_id"] = str(user_id)
        where = " AND ".join(filters)

        rows = await database.fetch_all(
            f"""
            SELECT id, user_id, pipeline_run_id, stage, chunk_type,
                   LEFT(content_text, 200) AS content_preview,
                   metadata, created_at,
                   (embedding IS NULL) AS missing_embedding
            FROM learning_chunks
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """,
            values=values,
        )
        total = await database.fetch_val(
            f"SELECT COUNT(*) FROM learning_chunks WHERE {where}",
            values={k: v for k, v in values.items() if k not in ("limit", "offset")},
        )
        items = []
        for row in rows or []:
            r = dict(row)
            meta = r.get("metadata")
            if isinstance(meta, str):
                import json

                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            items.append(
                {
                    "id": str(r.get("id")),
                    "user_id": str(r.get("user_id")) if r.get("user_id") else None,
                    "pipeline_run_id": str(r.get("pipeline_run_id"))
                    if r.get("pipeline_run_id")
                    else None,
                    "stage": r.get("stage"),
                    "chunk_type": r.get("chunk_type"),
                    "content_preview": r.get("content_preview"),
                    "metadata": meta if isinstance(meta, dict) else {},
                    "created_at": r.get("created_at").isoformat()
                    if r.get("created_at") and hasattr(r.get("created_at"), "isoformat")
                    else r.get("created_at"),
                    "missing_embedding": bool(r.get("missing_embedding")),
                }
            )
        return {"total": int(total or 0), "limit": values["limit"], "offset": values["offset"], "items": items}

    @classmethod
    async def run_timeline(cls, run_id: str) -> Dict[str, Any]:
        await PipelineLearningStore.ensure_tables()
        run = await database.fetch_one(
            """
            SELECT id, user_id, conversation_id, source_type, destination_type,
                   status, latest_job_id, latest_migration_batch_id, created_at, updated_at
            FROM pipeline_runs WHERE id = :id
            """,
            values={"id": str(run_id)},
        )
        if not run:
            return {"found": False, "run_id": run_id}

        chunks = await database.fetch_all(
            """
            SELECT id, stage, chunk_type, LEFT(content_text, 300) AS content_preview,
                   metadata, created_at
            FROM learning_chunks
            WHERE pipeline_run_id = :run_id
            ORDER BY created_at ASC
            """,
            values={"run_id": str(run_id)},
        )
        corrections = await database.fetch_all(
            """
            SELECT id, stage, stage_version, outcome, created_at
            FROM stage_corrections
            WHERE pipeline_run_id = :run_id
            ORDER BY created_at ASC
            """,
            values={"run_id": str(run_id)},
        )
        r = dict(run)
        conv = r.get("conversation_id")
        retrievals = []
        if conv:
            retrievals = await database.fetch_all(
                """
                SELECT id, caller, result_count, chunk_types, scores, created_at
                FROM learning_retrieval_log
                WHERE conversation_id = :conversation_id
                ORDER BY created_at ASC
                LIMIT 100
                """,
                values={"conversation_id": str(conv)},
            )

        def _serialize_row(row: Any) -> Dict[str, Any]:
            d = dict(row)
            for key in ("created_at", "updated_at"):
                if d.get(key) and hasattr(d[key], "isoformat"):
                    d[key] = d[key].isoformat()
            if d.get("id"):
                d["id"] = str(d["id"])
            return d

        return {
            "found": True,
            "run": _serialize_row(r),
            "chunks": [_serialize_row(c) for c in (chunks or [])],
            "corrections": [_serialize_row(c) for c in (corrections or [])],
            "retrievals": [_serialize_row(x) for x in (retrievals or [])],
        }

    @classmethod
    async def retrieval_effectiveness(
        cls,
        *,
        days: int = 30,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """RAG lift metrics for agent runtime and tool-pick learning."""
        await PipelineLearningStore.ensure_tables()
        period_days = _clamp_days(days)
        values: Dict[str, Any] = {"days": period_days}
        user_filter = ""
        if user_id:
            user_filter = "AND user_id = :user_id"
            values["user_id"] = str(user_id)

        agent_retrieval_callers = (
            "runtime",
            "agent_runtime",
            "agent_tool_pick",
            "recovery_tool_pick",
            "recovery_llm",
            "recovery_rules",
            # Legacy callers retained for historical log aggregation
            "universal_runtime",
            "universal_shadow",
            "plan_suggester",
            "dynamic_tool_pick",
        )

        caller_rows = await database.fetch_all(
            f"""
            SELECT
                caller,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE result_count > 0) AS with_results,
                AVG(result_count)::float AS avg_result_count,
                AVG(
                    CASE
                        WHEN jsonb_array_length(scores) > 0
                        THEN (scores->>0)::float
                        ELSE NULL
                    END
                ) AS avg_top_score
            FROM learning_retrieval_log
            WHERE created_at >= {_PERIOD_FILTER}
            {user_filter}
            GROUP BY caller
            ORDER BY total DESC
            """,
            values=values,
        )

        by_caller: List[Dict[str, Any]] = []
        agent_total = 0
        agent_with_results = 0
        for row in caller_rows or []:
            r = dict(row)
            caller = str(r.get("caller") or "")
            total = int(r.get("total") or 0)
            with_results = int(r.get("with_results") or 0)
            hit_rate = (with_results / total) if total else None
            entry = {
                "caller": caller,
                "total": total,
                "with_results": with_results,
                "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
                "avg_result_count": float(r["avg_result_count"])
                if r.get("avg_result_count") is not None
                else None,
                "avg_top_score": float(r["avg_top_score"])
                if r.get("avg_top_score") is not None
                else None,
            }
            by_caller.append(entry)
            if caller in agent_retrieval_callers:
                agent_total += total
                agent_with_results += with_results

        pick_row = await database.fetch_one(
            f"""
            SELECT
                COUNT(*) AS pick_count,
                COUNT(*) FILTER (
                    WHERE COALESCE((metadata->>'first_match')::boolean, false)
                ) AS first_match_count,
                COUNT(*) FILTER (
                    WHERE metadata->>'pick_source' = 'trace_replay'
                ) AS trace_replay_pick_count,
                AVG((metadata->>'decision_count')::float) AS avg_decision_count
            FROM learning_chunks
            WHERE chunk_type = 'tool_pick'
            AND created_at >= {_PERIOD_FILTER}
            {user_filter}
            """,
            values=values,
        )
        pick = dict(pick_row or {})
        pick_count = int(pick.get("pick_count") or 0)
        first_match_count = int(pick.get("first_match_count") or 0)
        trace_replay_pick_count = int(pick.get("trace_replay_pick_count") or 0)
        shadow_agreement = (first_match_count / pick_count) if pick_count else None

        replay_row = await database.fetch_one(
            f"""
            SELECT
                COUNT(*) AS attempts,
                COUNT(*) FILTER (WHERE result_count > 0) AS hits,
                COUNT(*) FILTER (WHERE result_count = 0) AS misses
            FROM learning_retrieval_log
            WHERE caller = 'trace_replay'
            AND created_at >= {_PERIOD_FILTER}
            {user_filter}
            """,
            values=values,
        )
        replay_stats = dict(replay_row or {})
        replay_attempts = int(replay_stats.get("attempts") or 0)
        replay_hits = int(replay_stats.get("hits") or 0)
        replay_misses = int(replay_stats.get("misses") or 0)
        replay_hit_rate = (replay_hits / replay_attempts) if replay_attempts else None
        llm_pick_estimate = max(0, pick_count - trace_replay_pick_count)

        trace_row = await database.fetch_one(
            f"""
            SELECT COUNT(*) AS trace_count
            FROM learning_chunks
            WHERE chunk_type IN ('tool_trace', 'tool_trace_partial')
            AND created_at >= {_PERIOD_FILTER}
            {user_filter}
            """,
            values=values,
        )

        lift_rows = await database.fetch_all(
            f"""
            WITH migration_chunks AS (
                SELECT
                    id,
                    COALESCE(metadata->>'conversation_id', '') AS conversation_id,
                    COALESCE((metadata->>'success_rate')::float, 0) AS success_rate,
                    chunk_type,
                    created_at
                FROM learning_chunks
                WHERE chunk_type IN ('migration_outcome', 'migration_partial', 'migration_failed')
                AND created_at >= {_PERIOD_FILTER}
                {user_filter}
            ),
            with_retrieval AS (
                SELECT DISTINCT m.id
                FROM migration_chunks m
                JOIN learning_retrieval_log r
                  ON r.conversation_id::text = NULLIF(m.conversation_id, '')
                 AND r.result_count > 0
                 AND r.created_at <= m.created_at
                 AND r.created_at >= m.created_at - (CAST(:days AS INTEGER) * INTERVAL '1 day')
            )
            SELECT
                CASE WHEN wr.id IS NOT NULL THEN 'with_retrieval' ELSE 'without_retrieval' END AS bucket,
                COUNT(*) AS migration_count,
                AVG(m.success_rate)::float AS avg_success_rate
            FROM migration_chunks m
            LEFT JOIN with_retrieval wr ON wr.id = m.id
            GROUP BY 1
            ORDER BY 1
            """,
            values=values,
        )
        migration_lift: Dict[str, Any] = {}
        for row in lift_rows or []:
            r = dict(row)
            bucket = str(r.get("bucket") or "unknown")
            migration_lift[bucket] = {
                "count": int(r.get("migration_count") or 0),
                "avg_success_rate": float(r["avg_success_rate"])
                if r.get("avg_success_rate") is not None
                else None,
            }

        with_rate = migration_lift.get("with_retrieval", {}).get("avg_success_rate")
        without_rate = migration_lift.get("without_retrieval", {}).get("avg_success_rate")
        lift_delta = None
        if with_rate is not None and without_rate is not None:
            lift_delta = round(with_rate - without_rate, 4)

        from services.pipeline_learning.trace_retrieval_policy import (
            platform_trace_min_success_rate,
            trace_retrieval_policy,
        )

        platform_row = await database.fetch_one(
            f"""
            SELECT COUNT(*) AS pattern_count
            FROM learning_chunks
            WHERE chunk_type = 'platform_tool_pattern'
            AND created_at >= {_PERIOD_FILTER}
            {user_filter}
            """,
            values=values,
        )
        platform_values = {**values, "platform_caller_suffix": "%:platform"}
        platform_retrieval_row = await database.fetch_one(
            f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE result_count > 0) AS with_results
            FROM learning_retrieval_log
            WHERE caller LIKE :platform_caller_suffix
            AND created_at >= {_PERIOD_FILTER}
            {user_filter}
            """,
            values=platform_values,
        )
        plat = dict(platform_retrieval_row or {})
        plat_total = int(plat.get("total") or 0)
        plat_with = int(plat.get("with_results") or 0)

        return {
            "days": days,
            "user_id": user_id,
            "agent_retrieval": {
                "total": agent_total,
                "with_results": agent_with_results,
                "hit_rate": round(agent_with_results / agent_total, 4)
                if agent_total
                else None,
            },
            "tool_pick_shadow": {
                "pick_count": pick_count,
                "first_match_count": first_match_count,
                "first_tool_agreement_rate": round(shadow_agreement, 4)
                if shadow_agreement is not None
                else None,
                "avg_decision_count": float(pick["avg_decision_count"])
                if pick.get("avg_decision_count") is not None
                else None,
            },
            "trace_replay": {
                "attempts": replay_attempts,
                "hits": replay_hits,
                "misses": replay_misses,
                "hit_rate": round(replay_hit_rate, 4) if replay_hit_rate is not None else None,
                "pick_chunks": trace_replay_pick_count,
                "llm_pick_estimate": llm_pick_estimate,
            },
            "tool_traces_captured": int(dict(trace_row or {}).get("trace_count") or 0),
            "migration_lift": migration_lift,
            "migration_success_lift_delta": lift_delta,
            "by_caller": by_caller,
            "trace_retrieval": {
                "policy": trace_retrieval_policy(),
                "platform_min_success_rate": platform_trace_min_success_rate(),
                "platform_patterns_captured": int(
                    dict(platform_row or {}).get("pattern_count") or 0
                ),
                "platform_retrieval": {
                    "total": plat_total,
                    "with_results": plat_with,
                    "hit_rate": round(plat_with / plat_total, 4) if plat_total else None,
                },
            },
        }

    @classmethod
    async def tool_usage_audit(
        cls,
        *,
        days: int = 30,
        low_usage_threshold: int = 3,
    ) -> Dict[str, Any]:
        """Compare registered tools vs observed picks/traces for deprecation signals."""
        await PipelineLearningStore.ensure_tables()
        from agent.registry import default_registry

        reg = default_registry()
        registered = sorted(reg._tools.keys())
        period_days = _clamp_days(days)

        rows = await database.fetch_all(
            f"""
            SELECT chunk_type, metadata
            FROM learning_chunks
            WHERE chunk_type IN ('tool_pick', 'tool_trace', 'tool_trace_partial')
            AND created_at >= {_PERIOD_FILTER}
            """,
            values={"days": period_days},
        )

        usage: Dict[str, int] = {}
        for row in rows or []:
            meta = dict(row).get("metadata") or {}
            if isinstance(meta, str):
                import json

                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            ids: List[str] = []
            if dict(row).get("chunk_type") == "tool_pick":
                raw = meta.get("actual_tool_ids") or []
                if isinstance(raw, list):
                    ids = [str(x) for x in raw if x]
            else:
                raw = meta.get("tool_ids") or []
                if isinstance(raw, list):
                    ids = [str(x) for x in raw if x]
            for tid in ids:
                usage[tid] = usage.get(tid, 0) + 1

        never_picked = [t for t in registered if usage.get(t, 0) == 0]
        low_usage = [
            t for t in registered if 0 < usage.get(t, 0) < low_usage_threshold
        ]
        active = [
            {"tool_id": t, "usage_count": usage[t]}
            for t in registered
            if usage.get(t, 0) >= low_usage_threshold
        ]
        active.sort(key=lambda x: x["usage_count"], reverse=True)

        unknown_in_traces = sorted(
            t for t in usage if t not in reg._tools
        )

        return {
            "days": days,
            "registered_count": len(registered),
            "never_picked": never_picked,
            "never_picked_count": len(never_picked),
            "low_usage": [{"tool_id": t, "usage_count": usage[t]} for t in low_usage],
            "low_usage_count": len(low_usage),
            "active_tools": active,
            "unknown_tool_ids_in_traces": unknown_in_traces,
            "deprecation_candidates": never_picked,
        }

    @classmethod
    async def user_summary(cls, user_id: str, *, days: int = 30) -> Dict[str, Any]:
        summary = await cls.platform_summary(days=days, user_id=user_id)
        funnel = await cls.stage_funnel(days=days, user_id=user_id)
        retrieval = await cls.retrieval_summary(days=days, user_id=user_id)
        return {
            "user_id": str(user_id),
            "days": days,
            "summary": summary,
            "funnel": funnel,
            "retrieval": retrieval,
        }
