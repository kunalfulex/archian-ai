"""Trace retrieval scope policies (user vs platform / cross-user anonymized)."""
from __future__ import annotations

import os

VALID_POLICIES = frozenset({"user_only", "user_then_platform", "platform_only"})


def trace_retrieval_policy() -> str:
    raw = (os.getenv("AGENT_TRACE_RETRIEVAL_POLICY") or "user_then_platform").strip().lower()
    return raw if raw in VALID_POLICIES else "user_then_platform"


def platform_trace_min_success_rate() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("AGENT_PLATFORM_TRACE_MIN_SUCCESS_RATE", "0.85"))))
    except ValueError:
        return 0.85


def platform_retrieval_enabled() -> bool:
    return trace_retrieval_policy() != "user_only"


def include_user_retrieval() -> bool:
    return trace_retrieval_policy() != "platform_only"
