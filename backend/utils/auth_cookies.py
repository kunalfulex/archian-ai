"""HttpOnly session cookies + double-submit CSRF for browser auth."""
from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Response

SESSION_COOKIE = "edison_session"
PRE_MFA_COOKIE = "edison_pre_mfa"
CSRF_COOKIE = "edison_csrf"
CSRF_HEADER = "X-CSRF-Token"

# Mutating requests skip CSRF on these path prefixes (login flow, webhooks, public forms).
CSRF_EXEMPT_PREFIXES = (
    "/api/login",
    "/api/verify-code",
    "/api/send-code",
    "/api/signup",
    "/api/forgot-password",
    "/api/reset-password",
    "/api/auth/google",
    "/contact-form",
    "/webhooks/",
    "/stripe/",
)


def _cookie_secure() -> bool:
    explicit = os.getenv("AUTH_COOKIE_SECURE", "").lower()
    if explicit in ("true", "1", "yes"):
        return True
    if explicit in ("false", "0", "no"):
        return False
    if _cookie_samesite() == "none":
        return True
    stage = os.getenv("DEPLOYMENT_STAGE", "development").lower()
    return stage in ("production", "staging", "uat")


def _cookie_samesite() -> str:
    explicit = (os.getenv("AUTH_COOKIE_SAMESITE") or "").lower()
    if explicit in ("none", "lax", "strict"):
        return explicit
    frontend = os.getenv("FRONTEND_URL", "")
    backend = os.getenv("BACKEND_URL", "")
    if "localhost" in frontend and "localhost" in backend:
        return "none"
    return "lax"


def _cookie_domain() -> Optional[str]:
    domain = (os.getenv("AUTH_COOKIE_DOMAIN") or "").strip()
    return domain or None


def _set_cookie(
    response: Response,
    *,
    key: str,
    value: str,
    max_age: int,
    httponly: bool,
) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age,
        httponly=httponly,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        domain=_cookie_domain(),
        path="/",
    )


def _delete_cookie(response: Response, key: str) -> None:
    response.delete_cookie(
        key=key,
        path="/",
        domain=_cookie_domain(),
    )


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def attach_session_auth(response: Response, session_token: str) -> str:
    """Set httpOnly session JWT + readable CSRF cookie."""
    from utils.auth_tokens import JWT_EXPIRY_SECONDS

    csrf = issue_csrf_token()
    _set_cookie(
        response,
        key=SESSION_COOKIE,
        value=session_token,
        max_age=JWT_EXPIRY_SECONDS,
        httponly=True,
    )
    _set_cookie(
        response,
        key=CSRF_COOKIE,
        value=csrf,
        max_age=JWT_EXPIRY_SECONDS,
        httponly=False,
    )
    _clear_pre_mfa_cookie(response)
    return csrf


def attach_pre_mfa_auth(response: Response, pre_mfa_token: str) -> None:
    from utils.auth_tokens import JWT_PRE_MFA_EXPIRY_SECONDS

    _set_cookie(
        response,
        key=PRE_MFA_COOKIE,
        value=pre_mfa_token,
        max_age=JWT_PRE_MFA_EXPIRY_SECONDS,
        httponly=True,
    )


def clear_all_auth_cookies(response: Response) -> None:
    for key in (SESSION_COOKIE, PRE_MFA_COOKIE, CSRF_COOKIE):
        _delete_cookie(response, key)


def _clear_pre_mfa_cookie(response: Response) -> None:
    _delete_cookie(response, PRE_MFA_COOKIE)


def read_session_token(request: Request, authorization: Optional[str] = None) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return token
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1]
    return None


def read_pre_mfa_token(request: Request, authorization: Optional[str] = None) -> Optional[str]:
    token = request.cookies.get(PRE_MFA_COOKIE)
    if token:
        return token
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1]
    return None


def is_csrf_exempt_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PREFIXES)


def validate_csrf(request: Request) -> None:
    """Raise 403 when CSRF checks fail for cookie-authenticated browser sessions."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if is_csrf_exempt_path(request.url.path):
        return
    if not request.cookies.get(SESSION_COOKIE):
        return

    cookie_csrf = request.cookies.get(CSRF_COOKIE)
    header_csrf = request.headers.get(CSRF_HEADER)
    if cookie_csrf and header_csrf and cookie_csrf == header_csrf:
        return

    # Cross-origin Render / split API hosts: frontend JS cannot read API-domain CSRF cookies.
    from utils.cors_origins import request_trusted_origin

    if request_trusted_origin(request):
        return

    raise HTTPException(status_code=403, detail="CSRF validation failed")
