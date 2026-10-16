"""Feature flags and limits for Enterprise Graph."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

CONNECTOR_TYPES = frozenset({"salesforce", "sql", "google_sheets"})
MEMBER_ROLES = frozenset({"source", "warehouse", "reference", "lake"})
GRAPH_STATUSES = frozenset({"draft", "active", "archived"})
RUN_DEPTHS = frozenset({"quick", "partial", "full"})

# User-facing product name in warehouse DDL and reports (rebrand: Archian AI).
PRODUCT_DISPLAY_NAME = "Archian AI"
DEFAULT_WAREHOUSE_DATASET = "archian_warehouse"
DEFAULT_LAKE_BRONZE_SCHEMA = "archian_bronze"
DEFAULT_LAKE_SILVER_SCHEMA = "archian_silver"

LAKE_DIALECTS = frozenset({"logical", "snowflake"})


def lake_enabled(settings: Optional[Dict[str, Any]] = None) -> bool:
    return bool((settings or {}).get("lake_enabled"))


def should_run_lake_phase(graph_settings: Dict[str, Any], members: List[Any]) -> bool:
    if not lake_enabled(graph_settings):
        return False
    return any((getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "") or "").lower() == "lake" for m in members)


def should_use_lake_silver_for_warehouse(
    graph_settings: Optional[Dict[str, Any]],
    lake_proposal: Optional[Dict[str, Any]],
) -> bool:
    """Warehouse lineage reads silver views when lake is enabled and a proposal exists."""
    if not lake_enabled(graph_settings):
        return False
    if not lake_proposal:
        return False
    lm = lake_proposal.get("logical_model") or {}
    return bool(lm.get("silver_views"))


def max_members_per_graph() -> int:
    try:
        return max(2, int(os.getenv("ENTERPRISE_GRAPH_MAX_MEMBERS", "5")))
    except ValueError:
        return 5


def min_members_per_graph() -> int:
    return 2


def depth_to_assessment_scope(depth: str) -> str:
    d = (depth or "partial").strip().lower()
    if d == "quick":
        return "quick"
    if d == "full":
        return "full"
    return "standard"


def assessment_poll_interval_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("ENTERPRISE_GRAPH_ASSESSMENT_POLL_SECONDS", "2")))
    except ValueError:
        return 2.0


def assessment_job_timeout_seconds() -> int:
    try:
        return max(60, int(os.getenv("ENTERPRISE_GRAPH_ASSESSMENT_TIMEOUT_SECONDS", "3600")))
    except ValueError:
        return 3600


def daa_concurrency() -> int:
    try:
        return max(1, int(os.getenv("ENTERPRISE_GRAPH_DAA_CONCURRENCY", "3")))
    except ValueError:
        return 3


WAREHOUSE_DIALECTS = frozenset({"logical", "bigquery", "snowflake", "postgres"})


def link_confidence_threshold() -> float:
    try:
        return min(1.0, max(0.0, float(os.getenv("EGA_LINK_CONFIDENCE_THRESHOLD", "0.75"))))
    except ValueError:
        return 0.75
