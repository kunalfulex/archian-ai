"""JWT creation and validation for Edison auth."""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional

import jwt

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise ValueError("JWT_SECRET environment variable is required for security")

TOKEN_TYPE_SESSION = "session"
TOKEN_TYPE_PRE_MFA = "pre_mfa"
JWT_ISSUER = "edison-ai"
JWT_AUDIENCE = "edison-frontend"
JWT_ALGORITHM = "HS256"

JWT_EXPIRY_SECONDS = int(os.getenv("JWT_EXPIRY_SECONDS", "86400"))
JWT_PRE_MFA_EXPIRY_SECONDS = int(os.getenv("JWT_PRE_MFA_EXPIRY_SECONDS", "600"))


def _now_ts() -> int:
    return int(time.time())


def _base_payload(*, email: str, user_id: str, token_type: str, expiry_seconds: int) -> Dict[str, Any]:
    now = _now_ts()
    return {
        "email": email.lower(),
        "user_id": str(user_id),
        "typ": token_type,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "exp": now + expiry_seconds,
    }


def create_pre_mfa_token(email: str, user_id: str) -> str:
    payload = _base_payload(
        email=email,
        user_id=user_id,
        token_type=TOKEN_TYPE_PRE_MFA,
        expiry_seconds=JWT_PRE_MFA_EXPIRY_SECONDS,
    )
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_session_token(email: str, user_id: str) -> str:
    payload = _base_payload(
        email=email,
        user_id=user_id,
        token_type=TOKEN_TYPE_SESSION,
        expiry_seconds=JWT_EXPIRY_SECONDS,
    )
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode JWT; legacy tokens without iss/aud still validate."""
    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        options={
            "verify_aud": False,
            "verify_iss": False,
            "require": ["exp", "user_id", "email"],
        },
    )


def token_type(payload: Dict[str, Any]) -> Optional[str]:
    return payload.get("typ")


def is_pre_mfa_token(payload: Dict[str, Any]) -> bool:
    return token_type(payload) == TOKEN_TYPE_PRE_MFA


def is_session_token(payload: Dict[str, Any]) -> bool:
    typ = token_type(payload)
    if typ == TOKEN_TYPE_PRE_MFA:
        return False
    if typ in (None, TOKEN_TYPE_SESSION):
        return True
    return False


def assert_pre_mfa_token(payload: Dict[str, Any]) -> None:
    if not is_pre_mfa_token(payload):
        raise jwt.InvalidTokenError("Token is not a pre-MFA token")


def assert_session_token(payload: Dict[str, Any]) -> None:
    if not is_session_token(payload):
        raise jwt.InvalidTokenError("Token is not a session token")
