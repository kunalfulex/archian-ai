"""Helpers to consume legacy handler SSE streams into ToolResult chunks + parsed metadata."""

from __future__ import annotations

import inspect
import json
import asyncio
from typing import Any, AsyncIterable, Dict, List, Tuple


def _mark_extraction_complete(meta: Dict[str, Any], job_id: Any, payload: Dict[str, Any]) -> None:
    """Commit job_id to session meta only after extraction reaches a terminal success state."""
    if not job_id:
        return
    meta["job_id"] = job_id
    meta["_extraction_complete"] = True
    if payload.get("extraction_version") is not None:
        try:
            meta["extraction_version"] = int(payload["extraction_version"])
        except (TypeError, ValueError):
            meta["extraction_version"] = payload["extraction_version"]
    result = payload.get("result")
    if isinstance(result, dict):
        if result.get("extraction_version") is not None and "extraction_version" not in meta:
            try:
                meta["extraction_version"] = int(result["extraction_version"])
            except (TypeError, ValueError):
                meta["extraction_version"] = result["extraction_version"]


def apply_sse_meta_from_line(line: str, meta: Dict[str, Any]) -> None:
    """Update meta dict from a single ``data: {...}`` SSE line."""
    stripped = (line or "").strip()
    if not stripped.startswith("data:"):
        return
    payload_raw = stripped[5:].strip()
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return

    event_type = payload.get("event_type")
    if event_type == "extraction_job_created":
        pending = payload.get("job_id")
        if pending:
            meta["_pending_job_id"] = pending
    elif event_type == "extraction_job_finished":
        job_status = payload.get("job_status") if isinstance(payload.get("job_status"), dict) else {}
        status = (job_status.get("status") or payload.get("status") or "").strip().lower()
        jid = payload.get("job_id") or job_status.get("job_id")
        if status == "completed" and jid:
            merged = {**job_status, **payload}
            _mark_extraction_complete(meta, jid, merged)
            meta["_extraction_finished_sse_emitted"] = True
            js_result = job_status.get("result") if isinstance(job_status.get("result"), dict) else {}
            for key in (
                "operations_applied",
                "extraction_version",
                "record_count",
                "pipeline_steps",
                "pipeline_narrative",
                "parent_job_id",
            ):
                if job_status.get(key) is not None:
                    meta[key] = job_status[key]
                elif js_result.get(key) is not None:
                    meta[key] = js_result[key]
        elif status in ("failed", "cancelled"):
            meta["_stream_status"] = "error"
            meta["_stream_error_message"] = (
                job_status.get("error")
                or job_status.get("message")
                or payload.get("message")
                or f"Extraction {status}"
            )
    elif event_type == "extraction_progress":
        pending = payload.get("job_id")
        if pending:
            meta.setdefault("_pending_job_id", pending)
    else:
        status_val = (payload.get("status") or "").strip().lower()
        jid = payload.get("job_id")
        if status_val in ("processing", "starting") and jid:
            meta.setdefault("_pending_job_id", jid)
        elif status_val in ("success", "completed") and jid:
            _mark_extraction_complete(meta, jid, payload)
        elif jid and status_val not in ("processing", "starting", "plan_limit_reached"):
            # Legacy handlers may emit job_id on intermediate payloads; do not treat as done.
            meta.setdefault("_pending_job_id", jid)
    sheet_name = payload.get("spreadsheet_name")
    if sheet_name:
        meta["spreadsheet_name"] = sheet_name
        meta["destination_name"] = payload.get("destination_name") or sheet_name
    tab_name = payload.get("sheet_name")
    if tab_name:
        meta["sheet_name"] = tab_name
    if event_type:
        meta["event_type"] = event_type
    if payload.get("destination"):
        meta["destination"] = payload["destination"]
    if payload.get("spreadsheet_id"):
        meta["spreadsheet_id"] = payload["spreadsheet_id"]
    if payload.get("extraction_version") is not None:
        try:
            meta["extraction_version"] = int(payload["extraction_version"])
        except (TypeError, ValueError):
            meta["extraction_version"] = payload["extraction_version"]
    if payload.get("extraction_rules"):
        meta["extraction_rules"] = payload["extraction_rules"]
    if payload.get("source_schema"):
        meta["source_schema"] = payload["source_schema"]
    if payload.get("action") == "schema_gate_proposal":
        meta["action"] = "schema_gate_proposal"
        if payload.get("object_api_name"):
            meta["object_api_name"] = payload["object_api_name"]
        if payload.get("fields_missing_on_destination") is not None:
            meta["fields_missing_on_destination"] = payload["fields_missing_on_destination"]
        if payload.get("deploy_recommended") is not None:
            meta["deploy_recommended"] = payload["deploy_recommended"]
    if payload.get("status") == "error" or payload.get("event_type") == "error":
        meta.setdefault("_stream_status", "error")
        if payload.get("message"):
            meta["_stream_error_message"] = payload["message"]
    elif payload.get("status") == "clarification":
        meta["_stream_status"] = "clarification"
        if payload.get("message"):
            meta["_stream_error_message"] = payload["message"]


_WIRE_ONLY_META_KEYS = frozenset(
    {
        "event_type",
        "job_status",
        "progress",
        "status",
    }
)


def session_delta_from_sse_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Business fields for session.merge_result — omit wire-protocol SSE keys."""
    out = {k: v for k, v in (meta or {}).items() if not str(k).startswith("_")}
    for key in _WIRE_ONLY_META_KEYS:
        out.pop(key, None)
    return out


def is_raw_handler_clarification_sse_line(line: str) -> bool:
    """True for legacy handler clarification payloads (not planner v2 events)."""
    stripped = (line or "").strip()
    if not stripped.startswith("data:"):
        return False
    try:
        payload = json.loads(stripped[5:].strip())
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("status") != "clarification":
        return False
    return payload.get("event_type") not in (
        "planner_clarification_required",
        "planner_plan_execution_clarification",
    )


async def iter_openai_compat_stream(stream: Any) -> AsyncIterable[Any]:
    """Iterate unified LLM streams (OpenAI sync Stream, async streams, Anthropic list wrapper)."""
    if hasattr(stream, "__aiter__"):
        async for chunk in stream:
            yield chunk
        return
    if hasattr(stream, "__iter__"):
        for chunk in stream:
            yield chunk
            await asyncio.sleep(0)
        return
    raise TypeError(f"Unsupported LLM stream type: {type(stream)!r}")


async def consume_legacy_sse_stream(stream: AsyncIterable[Any]) -> Tuple[List[str], Dict[str, Any]]:
    chunks: List[str] = []
    meta: Dict[str, Any] = {}
    async for item in stream:
        line = item if isinstance(item, str) else str(item)
        chunks.append(line)
        apply_sse_meta_from_line(line, meta)
    return chunks, meta


async def iter_legacy_handler_stream(stream: Any) -> AsyncIterable[Any]:
    """
    Iterate chunks from a legacy handler return value.

    Handlers may return an async generator, a coroutine that resolves to one,
    or (older) a zero-arg factory that returns either.
    """
    opened = stream
    if inspect.iscoroutine(opened):
        opened = await opened
    if callable(opened) and not inspect.isasyncgen(opened):
        opened = opened()
        if inspect.iscoroutine(opened):
            opened = await opened
    async for chunk in opened:
        yield chunk


async def resolve_tool_stream_handler(tool: Any, session: Any, args: Dict[str, Any]) -> AsyncIterable[Any]:
    """
    Open a tool stream_handler whether it is a coroutine or an async generator function.
    """
    stream = tool.stream_handler(session, args)
    if inspect.isasyncgen(stream):
        return stream
    if inspect.iscoroutine(stream):
        opened = await stream
        if inspect.isasyncgen(opened):
            return opened
        return opened
    return stream


# Tools that must stream SSE incrementally (not buffer until the turn completes).
LIVE_SSE_STREAMING_TOOL_IDS = frozenset(
    {
        "extract_salesforce",
        "extract_sql",
        "extract_google_sheets",
        "extract_file",
        "google_sheets_create_from_prompt",
        "file_reextract",
        "run_data_ops_plan",
        "gsheets_execute_operation",
        "schema_gate_assess",
        "schema_gate_propose",
        "metadata_deploy_salesforce",
        "general_ai_response",
        "salesforce_info_request",
        "sql_table_info",
        "google_sheets_info",
    }
)

LIVE_SSE_EXTRACT_TOOL_IDS = LIVE_SSE_STREAMING_TOOL_IDS
