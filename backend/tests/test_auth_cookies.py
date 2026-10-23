"""Tests for CSRF cookie validation."""
from __future__ import annotations

import pytest
from starlette.requests import Request

from utils.auth_cookies import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, validate_csrf


def _make_request(*, method: str, path: str, cookies: dict, headers: dict | None = None):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    req = Request(scope)
    req._cookies = cookies  # type: ignore[attr-defined]
    return req


def test_csrf_skipped_for_get():
    validate_csrf(_make_request(method="GET", path="/api/chat", cookies={SESSION_COOKIE: "x"}))


def test_csrf_skipped_for_login():
    validate_csrf(_make_request(method="POST", path="/api/login", cookies={}))


def test_csrf_required_when_session_present():
    with pytest.raises(Exception) as exc:
        validate_csrf(
            _make_request(
                method="POST",
                path="/api/chat",
                cookies={SESSION_COOKIE: "jwt", CSRF_COOKIE: "abc"},
                headers={},
            )
        )
    assert exc.value.status_code == 403


def test_csrf_passes_when_header_matches():
    validate_csrf(
        _make_request(
            method="POST",
            path="/api/chat",
            cookies={SESSION_COOKIE: "jwt", CSRF_COOKIE: "abc"},
            headers={CSRF_HEADER: "abc"},
        )
    )


def test_csrf_passes_with_trusted_origin_without_header(monkeypatch):
    """Cross-origin: session cookie sent but JS cannot read API-domain CSRF cookie."""
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    validate_csrf(
        _make_request(
            method="POST",
            path="/chat/conversations/abc/messages",
            cookies={SESSION_COOKIE: "jwt", CSRF_COOKIE: "abc"},
            headers={"Origin": "https://app.example.com"},
        )
    )


def test_csrf_rejects_untrusted_origin():
    with pytest.raises(Exception) as exc:
        validate_csrf(
            _make_request(
                method="POST",
                path="/chat/conversations/abc/messages",
                cookies={SESSION_COOKIE: "jwt", CSRF_COOKIE: "abc"},
                headers={"Origin": "https://evil.example.com"},
            )
        )
    assert exc.value.status_code == 403
