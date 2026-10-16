"""Helpers for classifying Google Sheets OAuth / API failures."""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.google_sheets_service import GoogleSheetsAuthError

GOOGLE_SHEETS_REAUTH_MESSAGE = (
    "Your Google Sheets connection expired or was revoked. "
    "Reconnect your account on the Connections page, then try again."
)


def is_google_sheets_reauth_error(exc: BaseException) -> bool:
    """
    True if the exception indicates the user must reconnect Google (invalid_grant, RefreshError, etc.).
    """
    if isinstance(exc, GoogleSheetsAuthError):
        return True

    if type(exc).__name__ == "RefreshError":
        return True

    s = str(exc).lower()
    if "invalid_grant" in s:
        return True
    if "refresh token expired" in s or "refresh token" in s and "reconnect" in s:
        return True
    if "reconnect your google sheets" in s:
        return True

    depth = 0
    c: Any = exc
    while c is not None and depth < 10:
        if type(c).__name__ == "RefreshError":
            return True
        if "invalid_grant" in str(c).lower():
            return True
        c = getattr(c, "__cause__", None)
        depth += 1

    return False


def build_google_sheets_extraction_error_payload(
    exc: BaseException,
    *,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """SSE payload for chat extraction failures (parsed by agent stream_utils + frontend)."""
    if is_google_sheets_reauth_error(exc):
        payload: Dict[str, Any] = {
            "status": "error",
            "code": "google_reauth_required",
            "message": GOOGLE_SHEETS_REAUTH_MESSAGE,
        }
    else:
        raw = (str(exc) or "").strip()
        payload = {
            "status": "error",
            "message": raw or "Google Sheets extraction failed. Please try again.",
        }
    if job_id:
        payload["job_id"] = job_id
    payload["source_type"] = "google_sheets"
    return payload
