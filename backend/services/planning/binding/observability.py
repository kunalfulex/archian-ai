"""One-line terminal logs for plan binding pipeline."""
from __future__ import annotations

import logging

logger = logging.getLogger("edison.plan_bind")


def log_binding(stage: str, message: str, *, level: int = logging.INFO) -> None:
    logger.log(level, "[plan_bind] %s %s", stage, message)
