"""Shared CORS / trusted frontend origin checks (CSRF fallback + middleware)."""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

# Explicit Render frontends only — never allow all of onrender.com.
RENDER_FRONTEND_ORIGINS = (
    "https://edisonai-frontend-dev.onrender.com",
    "https://edisonai-frontend-uat.onrender.com",
)

ARCHIAN_ORIGIN_REGEX = r"^https://([a-zA-Z0-9-]+\.)*archian\.ai$"
RENDER_ORIGIN_REGEX = r"^https://edisonai-frontend-(dev|uat)\.onrender\.com$"
COMBINED_ORIGIN_REGEX = (
    r"^https://("
    r"([a-zA-Z0-9-]+\.)*archian\.ai"
    r"|edisonai-frontend-(dev|uat)\.onrender\.com"
    r")$"
)

_STATIC_ALLOWED_ORIGINS = (
    "https://edisonai.io",
    "https://app.archian.ai",
    "https://www.archian.ai",
    "https://archian.ai",
    "http://localhost:3000",
    "https://localhost:3000",
)


def normalize_origin(url: str) -> str:
    return url.rstrip("/")


def get_allowed_origins() -> list[str]:
    frontend_url = normalize_origin(os.getenv("FRONTEND_URL", "https://app.archian.ai"))
    origins = [frontend_url, *_STATIC_ALLOWED_ORIGINS, *RENDER_FRONTEND_ORIGINS]
    return list(dict.fromkeys(origins))


def is_allowed_cors_origin(origin: str | None) -> bool:
    if not origin:
        return False
    normalized = normalize_origin(origin)
    if normalized in get_allowed_origins():
        return True
    if re.match(ARCHIAN_ORIGIN_REGEX, normalized):
        return True
    if re.match(RENDER_ORIGIN_REGEX, normalized):
        return True
    return False


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
