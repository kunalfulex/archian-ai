"""Tests for JWT auth token helpers."""
from __future__ import annotations

import os
import time

import jwt
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

from utils.auth_tokens import (  # noqa: E402
    TOKEN_TYPE_PRE_MFA,
    TOKEN_TYPE_SESSION,
    assert_pre_mfa_token,
    assert_session_token,
    create_pre_mfa_token,
    create_session_token,
    decode_token,
    is_pre_mfa_token,
    is_session_token,
)


def test_pre_mfa_token_rejected_as_session():
    token = create_pre_mfa_token("user@example.com", "user-1")
    payload = decode_token(token)
    assert is_pre_mfa_token(payload)
    assert not is_session_token(payload)
    with pytest.raises(jwt.InvalidTokenError):
        assert_session_token(payload)


def test_session_token_accepted():
    token = create_session_token("user@example.com", "user-1")
    payload = decode_token(token)
    assert payload["typ"] == TOKEN_TYPE_SESSION
    assert is_session_token(payload)
    assert_session_token(payload)


def test_pre_mfa_requires_correct_type():
    token = create_pre_mfa_token("user@example.com", "user-1")
    payload = decode_token(token)
    assert payload["typ"] == TOKEN_TYPE_PRE_MFA
    assert_pre_mfa_token(payload)


def test_legacy_token_without_typ_is_session():
    from utils.auth_tokens import JWT_SECRET, JWT_ALGORITHM

    payload = {
        "email": "legacy@example.com",
        "user_id": "legacy-user",
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    decoded = decode_token(token)
    assert is_session_token(decoded)
    assert_session_token(decoded)
