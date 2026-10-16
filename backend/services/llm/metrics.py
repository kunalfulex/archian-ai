"""Aggregate LLM invocation metrics for super-admin dashboard."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import database


def _row_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


async def get_metrics_summary(
    *,
    hours: int = 24,
    task_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    hours = max(1, min(hours, 24 * 30))
    filter_sql = ""
    values: Dict[str, Any] = {"hours": hours}
    if task_key:
        filter_sql = " AND task_key = :task_key"
        values["task_key"] = task_key

    try:
        rows = await database.fetch_all(
            f"""
            SELECT
                task_key,
                provider_id,
                model_id,
                COUNT(*)::int AS call_count,
                COUNT(*) FILTER (WHERE success)::int AS success_count,
                COUNT(*) FILTER (WHERE NOT success)::int AS error_count,
                ROUND(AVG(latency_ms)::numeric, 1) AS avg_latency_ms,
                ROUND(
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 1
                ) AS p95_latency_ms,
                COALESCE(SUM(tokens_in), 0)::bigint AS tokens_in,
                COALESCE(SUM(tokens_out), 0)::bigint AS tokens_out,
                COUNT(*) FILTER (WHERE json_parse_ok = TRUE)::int AS json_ok_count,
                COUNT(*) FILTER (WHERE json_parse_ok = FALSE)::int AS json_fail_count
            FROM llm_invocations
            WHERE created_at >= NOW() - (:hours || ' hours')::interval
            {filter_sql}
            GROUP BY task_key, provider_id, model_id
            ORDER BY call_count DESC
            """,
            values,
        )
        out = []
        for r in rows:
            d = _row_dict(r)
            cc = int(d.get("call_count") or 0)
            sc = int(d.get("success_count") or 0)
            d["success_rate_pct"] = round(100.0 * sc / cc, 1) if cc else 0.0
            out.append(d)
        return out
    except Exception:
        return []


async def get_metrics_timeseries(
    *,
    hours: int = 24,
    bucket_hours: int = 1,
) -> List[Dict[str, Any]]:
    hours = max(1, min(hours, 24 * 7))
    bucket_hours = max(1, min(bucket_hours, 24))
    try:
        rows = await database.fetch_all(
            """
            SELECT
                date_trunc('hour', created_at) AS bucket,
                COUNT(*)::int AS call_count,
                COUNT(*) FILTER (WHERE NOT success)::int AS error_count,
                ROUND(AVG(latency_ms)::numeric, 1) AS avg_latency_ms
            FROM llm_invocations
            WHERE created_at >= NOW() - (:hours || ' hours')::interval
            GROUP BY 1
            ORDER BY 1
            """,
            {"hours": hours},
        )
        return [
            {
                "bucket": d["bucket"].isoformat() if d.get("bucket") else None,
                "call_count": d["call_count"],
                "error_count": d["error_count"],
                "avg_latency_ms": float(d["avg_latency_ms"] or 0),
            }
            for d in (_row_dict(r) for r in rows)
        ]
    except Exception:
        return []
