"""Tests for env-driven CORS / trusted-host allowlists."""
from __future__ import annotations

import pytest

from utils.cors_origins import (
    get_allowed_origins,
    get_trusted_hosts,
    is_allowed_cors_origin,
    normalize_origin,
)


def test_localhost_defaults_allowed(monkeypatch):
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert is_allowed_cors_origin("http://localhost:3000")
    assert is_allowed_cors_origin("https://localhost:3000")
    assert "http://localhost:3000" in get_allowed_origins()


def test_extra_cors_origins_from_env(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://www.example.com, https://admin.example.com/",
    )
    assert is_allowed_cors_origin("https://app.example.com")
    assert is_allowed_cors_origin("https://www.example.com")
    assert is_allowed_cors_origin("https://admin.example.com")
    assert not is_allowed_cors_origin("https://evil.example.com")


def test_random_onrender_not_allowed(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert not is_allowed_cors_origin("https://someone-else.onrender.com")


def test_normalize_origin_strips_trailing_slash():
    assert normalize_origin("https://app.example.com/") == "https://app.example.com"


def test_trusted_hosts_from_urls_and_extras(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")
    monkeypatch.setenv("BACKEND_URL", "https://api.example.com")
    monkeypatch.setenv("TRUSTED_HOSTS", "*.example.com,custom-host.internal")
    hosts = get_trusted_hosts()
    assert "app.example.com" in hosts
    assert "api.example.com" in hosts
    assert "*.example.com" in hosts
    assert "custom-host.internal" in hosts
    assert "localhost" in hosts
