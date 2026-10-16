"""
SSE payloads for extraction/post-extract completion — mirrors GET /job-status shape
for `extraction_job_finished` so the frontend uses one completion handler (IW1).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from services.extraction_results_service import ExtractionResultsService

logger = logging.getLogger(__name__)


def extraction_setup_progress_sse_line(
    *,
    message: str,
    phase: str,
    source_type: str,
    job_id: Optional[str] = None,
    progress: int = 0,
) -> str:
    """
    User-visible milestone during extraction setup (before background job progress).

    Emits ``operation_phase`` (unified loader) and ``extraction_progress`` (legacy footer).
    """
    from agent.events import operation_phase
    from utils.operation_phase_map import operation_from_setup_phase

    op_id, op_label = operation_from_setup_phase(
        phase, message=message, source_type=source_type
    )
    prog = progress if progress is not None and progress > 0 else None
    lines = [
        operation_phase(
            op_id,
            "running",
            label=op_label,
            progress=prog,
            detail=message,
            meta={"setup_phase": phase, "source_type": source_type, "job_id": job_id},
        ),
    ]
    payload: Dict[str, Any] = {
        "event_type": "extraction_progress",
        "progress": progress,
        "message": message,
        "phase": phase,
        "source_type": source_type,
    }
    if job_id:
        payload["job_id"] = job_id
    lines.append(f"data: {json.dumps(payload)}\n\n")
    return "".join(lines)


def _query_from_redis_job_dict(jd: Any) -> Optional[str]:
    """Recover SOQL / SQL text from a Redis extraction_jobs payload."""
    if not jd or not isinstance(jd, dict):
        return None
    res = jd.get("result")
    res_d = res if isinstance(res, dict) else {}
    payload = jd.get("soql_payload")
    payload_d = payload if isinstance(payload, dict) else {}
    for candidate in (
        jd.get("soql"),
        jd.get("query"),
        res_d.get("soql"),
        res_d.get("query"),
        payload_d.get("soql"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


async def resolve_source_query_for_sse_result(
    *,
    job_id: str,
    user_id: str,
    ops_meta: Optional[Dict[str, Any]],
) -> Optional[str]:
    """
    Original Salesforce/SQL queries are not stored on extraction_results rows.
    Post-extract versions only inherit parent_job_id — recover SOQL/SQL from ops_meta
    or by walking parent_job_id and reading Redis extraction_jobs (still holds result.soql).
    """
    if isinstance(ops_meta, dict):
        q = ops_meta.get("query") or ops_meta.get("soql")
        if isinstance(q, str) and q.strip():
            return q.strip()

    try:
        from utils.job_stores import extraction_jobs
    except Exception as exc:
        logger.warning("resolve_source_query_for_sse_result: job store import failed: %s", exc)
        return None

    visited: set[str] = set()
    cursor: Optional[str] = job_id
    for _ in range(32):
        if not cursor or cursor in visited:
            break
        visited.add(cursor)
        try:
            if await extraction_jobs.exists(cursor):
                jd = await extraction_jobs.get(cursor, {}) or {}
                hit = _query_from_redis_job_dict(jd)
                if hit:
                    return hit
        except Exception as exc:
            logger.debug("resolve_source_query_for_sse_result redis read failed job=%s: %s", cursor, exc)

        row_cur = await ExtractionResultsService.get_extraction_result(cursor, user_id)
        if not row_cur:
            break
        nxt = row_cur.get("parent_job_id")
        if not nxt or nxt == cursor:
            break
        cursor = str(nxt)

    return None


async def resolve_salesforce_meta_for_sse(
    *,
    job_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """
    Post-extract job_status rows omit salesforce_token_id / object_api_name.
    Walk parent_job_id and Redis extraction_jobs to recover them for the frontend.
    """
    out: Dict[str, Any] = {}
    try:
        from utils.job_stores import extraction_jobs
    except Exception as exc:
        logger.warning("resolve_salesforce_meta_for_sse: job store import failed: %s", exc)
        return out

    visited: set[str] = set()
    cursor: Optional[str] = job_id
    for _ in range(32):
        if not cursor or cursor in visited:
            break
        visited.add(cursor)
        try:
            if await extraction_jobs.exists(cursor):
                jd = await extraction_jobs.get(cursor, {}) or {}
                token_id = jd.get("salesforce_token_id")
                if token_id and not out.get("salesforce_token_id"):
                    out["salesforce_token_id"] = str(token_id)
                obj = jd.get("object_api_name")
                res_d = jd.get("result") if isinstance(jd.get("result"), dict) else {}
                if not obj and isinstance(res_d, dict):
                    obj = res_d.get("object_api_name") or res_d.get("table_name")
                if obj and not out.get("object_api_name"):
                    out["object_api_name"] = obj
                if out.get("salesforce_token_id") and out.get("object_api_name"):
                    return out
        except Exception as exc:
            logger.debug("resolve_salesforce_meta_for_sse redis read failed job=%s: %s", cursor, exc)

        row_cur = await ExtractionResultsService.get_extraction_result(cursor, user_id)
        if not row_cur:
            break
        nxt = row_cur.get("parent_job_id")
        if not nxt or nxt == cursor:
            break
        cursor = str(nxt)

    return out


async def build_job_status_after_db_save(
    *,
    job_id: str,
    user_id: str,
    ops_meta: Dict[str, Any],
    classification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build `job_status` compatible with GET /job-status extraction_jobs branch.

    `ops_meta` is the dict returned by PostExtractOrchestrator.run / tool visible payload.
    """
    classification = classification or {}
    row = await ExtractionResultsService.get_extraction_result(job_id, user_id)

    source_type_raw = (
        (ops_meta.get("source_type") if isinstance(ops_meta, dict) else None)
        or (row.get("source_type") if row else None)
        or classification.get("source")
        or "unknown"
    )
    source_name = (
        ops_meta.get("source_name")
        if isinstance(ops_meta, dict)
        else None
    ) or (row.get("source_name") if row else None) or classification.get("source_name")

    destination = classification.get("destination") or "unknown"
    destination_name = classification.get("destination_name")

    from utils.document_category_match import destination_requires_pick

    requires_destination = destination_requires_pick(destination, destination_name)

    record_count = int(
        ops_meta.get("record_count")
        or (row.get("record_count") if row else 0)
        or 0
    )

    extraction_rules = ops_meta.get("extraction_rules") if isinstance(ops_meta, dict) else None

    result_inner: Dict[str, Any] = {
        "status": "success",
        "record_count": record_count,
        "total_records": record_count,
        "data": None,
        "extraction_rules": extraction_rules,
        "operations_applied": ops_meta.get("operations_applied") if isinstance(ops_meta, dict) else None,
    }
    if isinstance(ops_meta, dict) and ops_meta.get("pipeline_narrative"):
        result_inner["pipeline_narrative"] = ops_meta["pipeline_narrative"]

    if isinstance(ops_meta, dict):
        ps = ops_meta.get("pipeline_steps")
        if isinstance(ps, list) and ps:
            result_inner["pipeline_steps"] = ps
        if ops_meta.get("data_quality_report") is not None:
            result_inner["data_quality_report"] = ops_meta["data_quality_report"]
        if ops_meta.get("operation_summary"):
            result_inner["operation_summary"] = ops_meta["operation_summary"]

    source_q = await resolve_source_query_for_sse_result(
        job_id=job_id, user_id=user_id, ops_meta=ops_meta if isinstance(ops_meta, dict) else None
    )
    if source_q:
        result_inner["query"] = source_q
        result_inner["soql"] = source_q
    if row:
        result_inner["document_type"] = row.get("document_type")
        result_inner["document_description"] = row.get("document_description")
        canonical_type = row.get("document_type")
        canonical_desc = row.get("document_description")
        if canonical_type:
            try:
                from utils.document_category_match import enrich_source_extraction_metadata

                ext_meta = row.get("extraction_metadata") if row else None
                if isinstance(ext_meta, str):
                    import json as _json

                    try:
                        ext_meta = _json.loads(ext_meta)
                    except (_json.JSONDecodeError, TypeError):
                        ext_meta = {}
                cat_meta = await enrich_source_extraction_metadata(
                    user_id,
                    destination,
                    destination_name,
                    row.get("document_type"),
                    row.get("document_description"),
                    None,
                    source_type=source_type_raw,
                    source_name=source_name,
                    extraction_metadata=ext_meta if isinstance(ext_meta, dict) else None,
                )
                canonical_type = cat_meta.get("source_document_type") or cat_meta.get("canonical_document_type") or canonical_type
                canonical_desc = cat_meta.get("source_document_description") or cat_meta.get("canonical_document_description") or canonical_desc
                result_inner["category_match_tier"] = cat_meta.get("category_match_tier")
                result_inner["matched_object_or_table"] = cat_meta.get("matched_object_or_table")
                result_inner["source_document_type"] = canonical_type
                result_inner["source_document_description"] = canonical_desc
                if cat_meta.get("semantic_data_profile"):
                    result_inner["semantic_data_profile"] = cat_meta["semantic_data_profile"]
            except Exception as exc:
                logger.debug("build_job_status category enrich failed: %s", exc)
        result_inner["canonical_document_type"] = canonical_type
        result_inner["canonical_document_description"] = canonical_desc
        result_inner["requires_destination"] = requires_destination

    st_lower = str(source_type_raw).lower()
    sf_meta: Dict[str, Any] = {}
    if st_lower == "salesforce":
        result_inner["connection_name"] = source_name
        sf_meta = await resolve_salesforce_meta_for_sse(job_id=job_id, user_id=user_id)
        if sf_meta.get("salesforce_token_id"):
            result_inner["salesforce_token_id"] = sf_meta["salesforce_token_id"]
        if sf_meta.get("object_api_name"):
            result_inner["object_api_name"] = sf_meta["object_api_name"]
            result_inner["table_name"] = sf_meta["object_api_name"]
    elif st_lower == "sql":
        result_inner["table_name"] = row.get("source_name") if row else None
    elif st_lower == "google_sheets":
        result_inner["spreadsheet_name"] = source_name

    payload = {
        "job_id": job_id,
        "status": "completed",
        "progress": 100,
        "result": result_inner,
        "error": None,
        "error_explanation": None,
        "error_solution": None,
        "created_at": row.get("created_at") if row else None,
        "extraction_goals": row.get("extraction_goals") if row else None,
        "flow_id": None,
        "destination": destination,
        "destination_name": destination_name,
        "requires_destination": requires_destination,
        "job_type": "post_extract",
        "source_type": st_lower,
        "parent_job_id": ops_meta.get("parent_job_id") if isinstance(ops_meta, dict) else None,
        "connection_name": source_name if st_lower == "salesforce" else None,
        "org_id": row.get("source_identifier") if row else None,
        "spreadsheet_name": row.get("source_name") if st_lower == "google_sheets" else None,
        "file_key": None,
    }
    if st_lower == "salesforce" and sf_meta:
        if sf_meta.get("salesforce_token_id"):
            payload["salesforce_token_id"] = sf_meta["salesforce_token_id"]
        if sf_meta.get("object_api_name"):
            payload["object_api_name"] = sf_meta["object_api_name"]
    return json.loads(json.dumps(payload, default=str))


def extraction_job_finished_sse(job_id: str, job_status: Dict[str, Any]) -> str:
    """Single SSE line for terminal extraction completion."""
    body = {
        "event_type": "extraction_job_finished",
        "job_id": job_id,
        "job_status": job_status,
    }
    return f"data: {json.dumps(body, default=str)}\n\n"
