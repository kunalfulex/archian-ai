"""Planner rollout helpers — stage detection and startup logging."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_STAGES = ("development", "staging", "production")

# Logged at startup; capabilities are fixed in code (not env-gated).
PLANNER_CAPABILITIES: Dict[str, str] = {
    "planning_pipeline": "on",
    "intent_pipeline": "on",
    "llm_goal_parser": "on",
    "schema_gates": "on",
    "plan_approval": "client_ask_act",
}


def deployment_stage() -> str:
    """Infer rollout stage from DEPLOYMENT_STAGE, RENDER_SERVICE_NAME, or default dev."""
    raw = (
        os.getenv("DEPLOYMENT_STAGE")
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or ""
    ).strip().lower()
    if raw in VALID_STAGES:
        return raw
    if raw in ("dev", "local", "development"):
        return "development"
    if raw in ("stage", "stg", "staging"):
        return "staging"
    if raw in ("prod", "production"):
        return "production"
    render = (os.getenv("RENDER_SERVICE_NAME") or "").lower()
    if "prod" in render:
        return "production"
    if "stag" in render:
        return "staging"
    return "development"


def current_planner_capabilities() -> Dict[str, str]:
    """Effective planner capabilities (always on; approval via client plan_auto_run)."""
    return dict(PLANNER_CAPABILITIES)


def validate_planner_rollout(*, stage: Optional[str] = None) -> List[str]:
    """Legacy hook for startup warnings; no env flag mismatches to report."""
    return []


def log_planner_rollout_status() -> None:
    """Log stage and planner capabilities (safe at startup)."""
    stage = deployment_stage()
    logger.info(
        "planning_pipeline rollout stage=%s capabilities=%s",
        stage,
        current_planner_capabilities(),
    )


def rollout_summary() -> Dict[str, Any]:
    stage = deployment_stage()
    return {
        "stage": stage,
        "planning_pipeline_enabled": True,
        "capabilities": current_planner_capabilities(),
        "warnings": validate_planner_rollout(stage=stage),
    }


# Backward-compatible aliases for tests / callers
def current_planner_flags() -> Dict[str, str]:
    return current_planner_capabilities()


def recommended_planner_flags(stage: Optional[str] = None) -> Dict[str, str]:
    return current_planner_capabilities()
