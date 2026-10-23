"""CORS / trusted frontend origin checks (CSRF fallback + middleware).

Origins are env-driven. Defaults are local only — hosted deployments must set
FRONTEND_URL and optionally CORS_ALLOWED_ORIGINS / TRUSTED_HOSTS. No product
hostnames are baked into this module.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from utils.url_defaults import DEFAULT_BACKEND_URL, DEFAULT_FRONTEND_URL

# Re-export for callers / tests.
__all__ = (
    "DEFAULT_BACKEND_URL",
    "DEFAULT_FRONTEND_URL",
    "normalize_origin",
    "host_from_url",
    "get_allowed_origins",
    "is_allowed_cors_origin",
    "get_trusted_hosts",
    "request_trusted_origin",
    "cors_headers_for_origin",
)

_LOCAL_FRONTEND_ORIGINS = (
    "http://localhost:3000",
    "https://localhost:3000",
    "http://127.0.0.1:3000",
    "https://127.0.0.1:3000",
)


def normalize_origin(url: str) -> str:
    return (url or "").rstrip("/")


def host_from_url(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    return parsed.hostname or None


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_allowed_origins() -> list[str]:
    frontend_url = normalize_origin(os.getenv("FRONTEND_URL", DEFAULT_FRONTEND_URL))
    origins: list[str] = []
    if frontend_url:
        origins.append(frontend_url)
    if "localhost" in frontend_url or "127.0.0.1" in frontend_url:
        origins.extend(_LOCAL_FRONTEND_ORIGINS)
    for part in _split_csv(os.getenv("CORS_ALLOWED_ORIGINS")):
        origins.append(normalize_origin(part))
    return list(dict.fromkeys(o for o in origins if o))


def is_allowed_cors_origin(origin: str | None) -> bool:
    if not origin:
        return False
    return normalize_origin(origin) in get_allowed_origins()


def get_trusted_hosts() -> list[str]:
    """Host header allowlist for TrustedHostMiddleware."""
    hosts: list[str] = ["localhost", "127.0.0.1", "0.0.0.0", "testserver"]
    for key, default in (
        ("FRONTEND_URL", DEFAULT_FRONTEND_URL),
        ("BACKEND_URL", DEFAULT_BACKEND_URL),
    ):
        host = host_from_url(os.getenv(key, default))
        if host:
            hosts.append(host)
    backend_host = (os.getenv("BACKEND_HOST") or "").strip()
    if backend_host:
        hosts.append(backend_host)
    # Extra hosts: bare hostnames or "*.example.com" wildcards (Starlette style).
    for part in _split_csv(os.getenv("TRUSTED_HOSTS")):
        if part.startswith("*."):
            hosts.append(part)
        else:
            host = host_from_url(part) if "://" in part else part
            if host:
                hosts.append(host)
    return list(dict.fromkeys(h for h in hosts if h))


def request_trusted_origin(request) -> str | None:
    """Origin or Referer base URL when it matches the allowlist."""
    origin = request.headers.get("origin")
    if origin and is_allowed_cors_origin(origin):
        return normalize_origin(origin)

    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            referer_origin = f"{parsed.scheme}://{parsed.netloc}"
            if is_allowed_cors_origin(referer_origin):
                return normalize_origin(referer_origin)
    return None


def cors_headers_for_origin(origin: str | None) -> dict[str, str]:
    if origin and is_allowed_cors_origin(origin):
        return {
            "Access-Control-Allow-Origin": normalize_origin(origin),
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": (
                "Authorization, Content-Type, X-CSRF-Token, X-Requested-With, Accept, Origin"
            ),
        }
    return {}
