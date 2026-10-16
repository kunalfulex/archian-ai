"""Tests for CORS origin allowlist."""
from __future__ import annotations

from utils.cors_origins import is_allowed_cors_origin, normalize_origin


def test_render_dev_frontend_allowed():
    assert is_allowed_cors_origin("https://edisonai-frontend-dev.onrender.com")


def test_render_uat_frontend_allowed():
    assert is_allowed_cors_origin("https://edisonai-frontend-uat.onrender.com")


def test_random_onrender_not_allowed():
    assert not is_allowed_cors_origin("https://someone-else.onrender.com")


def test_archian_subdomain_allowed():
    assert is_allowed_cors_origin("https://app.archian.ai")


def test_normalize_origin_strips_trailing_slash():
    assert normalize_origin("https://app.archian.ai/") == "https://app.archian.ai"
