"""Agent Runtime rollout flags."""
from __future__ import annotations

import logging

from agent.recovery_policy import (
    MAX_RECOVERY_ITERATIONS,
    MIN_SUCCESS_RATE_DIAGNOSE,
    MIN_SUCCESS_RATE_OK,
    RUNTIME_CAPABILITIES,
)
from services.planning.rollout import deployment_stage

logger = logging.getLogger(__name__)


def log_agent_runtime_rollout_status() -> None:
    stage = deployment_stage()
    logger.info(
        "agent_runtime_v2 stage=%s execution=plan_scoped capabilities=%s thresholds={ok:%s,diagnose:%s,max_recovery:%s}",
        stage,
        RUNTIME_CAPABILITIES,
        MIN_SUCCESS_RATE_OK,
        MIN_SUCCESS_RATE_DIAGNOSE,
        MAX_RECOVERY_ITERATIONS,
    )
