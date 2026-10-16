"""Single place to resolve provider API keys (BYOM)."""
from __future__ import annotations

import os
from typing import Optional

PROVIDER_ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def provider_env_key(provider_id: str) -> str:
    return PROVIDER_ENV_KEYS.get(provider_id, f"{provider_id.upper()}_API_KEY")


def resolve_api_key(
    provider_id: str,
    *,
    env_key_name: Optional[str] = None,
    org_id: Optional[str] = None,
) -> Optional[str]:
    """Return the API key for ``provider_id``, or None when it is not configured.

    ``env_key_name`` overrides the default env var (provider rows in the DB carry
    their own). ``org_id`` is reserved for per-tenant keys and is not used yet.
    """
    key = (os.getenv(env_key_name or provider_env_key(provider_id)) or "").strip()
    return key or None
