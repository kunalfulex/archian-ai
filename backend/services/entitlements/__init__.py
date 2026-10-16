"""Edition-aware entitlements.

``ARCHIAN_EDITION`` selects the implementation: ``community`` (no billing) or
``cloud`` (plan + credit billing). Unset or unrecognised values resolve to
``community``; hosted deployments must set ``ARCHIAN_EDITION=cloud``.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from services.entitlements.protocol import (
    UNLIMITED,
    UNLIMITED_EXTRACTION_RECORDS,
    Entitlements,
    ValidationResult,
)
from services.settings import get_settings

logger = logging.getLogger(__name__)

EDITION_ENV_VAR = "ARCHIAN_EDITION"
EDITIONS = ("cloud", "community")
DEFAULT_EDITION = "community"


def get_edition() -> str:
    raw = (get_settings().archian_edition or "").strip().lower()
    if not raw:
        return DEFAULT_EDITION
    if raw not in EDITIONS:
        logger.warning(
            "Unknown %s=%r; expected one of %s. Falling back to %r.",
            EDITION_ENV_VAR,
            raw,
            EDITIONS,
            DEFAULT_EDITION,
        )
        return DEFAULT_EDITION
    return raw


@lru_cache(maxsize=1)
def get_entitlements() -> Entitlements:
    edition = get_edition()
    logger.info(
        "Archian AI edition: %s (billing %s)",
        edition,
        "enabled" if edition == "cloud" else "disabled",
    )
    if edition == "community":
        from services.entitlements.community import CommunityEntitlements

        return CommunityEntitlements()
    try:
        from archian_cloud.entitlements import CloudEntitlements
    except ImportError as e:
        raise RuntimeError(
            f"{EDITION_ENV_VAR}=cloud requires the private archian_cloud package; "
            f"unset {EDITION_ENV_VAR} to run the community edition"
        ) from e

    return CloudEntitlements()


__all__ = [
    "DEFAULT_EDITION",
    "EDITIONS",
    "EDITION_ENV_VAR",
    "UNLIMITED",
    "UNLIMITED_EXTRACTION_RECORDS",
    "Entitlements",
    "ValidationResult",
    "get_edition",
    "get_entitlements",
]
