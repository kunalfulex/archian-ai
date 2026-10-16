"""Continuation routing and metadata coercion tests."""
from __future__ import annotations

from services.planning.conversation_session import is_unknown_resource


def test_is_unknown_resource_accepts_dict_destination():
    assert is_unknown_resource({"name": "My SQL DB"}) is False
    assert is_unknown_resource({"id": "conn-1"}) is False
    assert is_unknown_resource({}) is True
    assert is_unknown_resource("unknown") is True
