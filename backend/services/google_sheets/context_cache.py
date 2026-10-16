"""Redis-backed cache for SpreadsheetContext (with in-memory fallback)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from services.google_sheets.config import gsheets_context_cache_ttl_seconds
from services.google_sheets.models import SpreadsheetContext

_MEMORY: Dict[str, str] = {}
_KEY_PREFIX = "gsheets:ctx:"


def _cache_key(user_id: str, spreadsheet_id: str, sheet_name: str) -> str:
    sheet = (sheet_name or "Sheet1").strip()
    return f"{_KEY_PREFIX}{user_id}:{spreadsheet_id}:{sheet}"


def context_to_dict(ctx: SpreadsheetContext) -> Dict[str, Any]:
    return {
        "found": ctx.found,
        "spreadsheet_id": ctx.spreadsheet_id,
        "spreadsheet_name": ctx.spreadsheet_name,
        "sheet_name": ctx.sheet_name,
        "headers": list(ctx.headers),
        "key_columns": list(ctx.key_columns),
        "source": ctx.source,
        "error": ctx.error,
        "message": ctx.message,
        "estimated_row_count": ctx.estimated_row_count,
    }


def context_from_dict(data: Dict[str, Any]) -> SpreadsheetContext:
    return SpreadsheetContext(
        found=bool(data.get("found")),
        spreadsheet_id=data.get("spreadsheet_id"),
        spreadsheet_name=data.get("spreadsheet_name"),
        sheet_name=data.get("sheet_name") or "Sheet1",
        headers=list(data.get("headers") or []),
        key_columns=list(data.get("key_columns") or []),
        source=data.get("source") or "",
        error=data.get("error"),
        message=data.get("message"),
        estimated_row_count=data.get("estimated_row_count"),
    )


async def get_cached_context(
    user_id: str, spreadsheet_id: str, sheet_name: str
) -> Optional[SpreadsheetContext]:
    key = _cache_key(user_id, spreadsheet_id, sheet_name)
    raw: Optional[str] = None

    try:
        from utils.redis_service import get_redis_client

        client = await get_redis_client()
        if client:
            raw = await client.get(key)
    except Exception as e:
        print(f"⚠️ [gsheets cache] Redis get failed: {e}")

    if raw is None:
        raw = _MEMORY.get(key)

    if not raw:
        return None
    try:
        return context_from_dict(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return None


async def set_cached_context(
    user_id: str,
    ctx: SpreadsheetContext,
    ttl_seconds: Optional[int] = None,
) -> None:
    if not ctx.found or not ctx.spreadsheet_id:
        return
    ttl = ttl_seconds if ttl_seconds is not None else gsheets_context_cache_ttl_seconds()
    key = _cache_key(user_id, ctx.spreadsheet_id, ctx.sheet_name or "Sheet1")
    payload = json.dumps(context_to_dict(ctx))

    try:
        from utils.redis_service import get_redis_client

        client = await get_redis_client()
        if client:
            await client.setex(key, ttl, payload)
            return
    except Exception as e:
        print(f"⚠️ [gsheets cache] Redis set failed: {e}")

    _MEMORY[key] = payload


async def invalidate_context(
    user_id: str, spreadsheet_id: str, sheet_name: Optional[str] = None
) -> None:
    sheet = sheet_name or "Sheet1"
    key = _cache_key(user_id, spreadsheet_id, sheet)
    try:
        from utils.redis_service import get_redis_client

        client = await get_redis_client()
        if client:
            await client.delete(key)
    except Exception:
        pass
    _MEMORY.pop(key, None)
