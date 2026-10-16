"""Typed application settings (pydantic-settings).

Edition and core URLs are the primary consumers today. Broader ``os.getenv``
migration continues in Phase 2; callers may still read env directly.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Env vars only (no env_file): main/tests call load_dotenv() first so .env is
    # already in os.environ. Skipping env_file keeps monkeypatched env in tests correct.
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
    )

    # Edition: community (default) or cloud
    archian_edition: str = Field(default="community", alias="ARCHIAN_EDITION")

    # Core URLs
    frontend_url: str = Field(default="https://app.archian.ai", alias="FRONTEND_URL")
    backend_url: Optional[str] = Field(default=None, alias="BACKEND_URL")
    backend_host: Optional[str] = Field(default=None, alias="BACKEND_HOST")

    # Database / Redis (optional here — required at runtime by connection layer)
    db_url: Optional[str] = Field(default=None, alias="DB_URL")
    redis_url: Optional[str] = Field(default=None, alias="REDIS_URL")

    # Auth
    jwt_secret: Optional[str] = Field(default=None, alias="JWT_SECRET")
    jwt_expiry_seconds: int = Field(default=86400, alias="JWT_EXPIRY_SECONDS")

    # LLM
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")

    # Private cloud package pin (Render build arg / env)
    archian_cloud_version: Optional[str] = Field(default=None, alias="ARCHIAN_CLOUD_VERSION")

    # Salesforce OAuth (connected app)
    salesforce_client_id: Optional[str] = Field(default=None, alias="SALESFORCE_CLIENT_ID")
    salesforce_client_secret: Optional[str] = Field(default=None, alias="SALESFORCE_CLIENT_SECRET")
    salesforce_edison_client_id: Optional[str] = Field(
        default=None, alias="SALESFORCE_EDISON_CLIENT_ID"
    )
    salesforce_edison_client_secret: Optional[str] = Field(
        default=None, alias="SALESFORCE_EDISON_CLIENT_SECRET"
    )

    def resolved_edition(self) -> str:
        raw = (self.archian_edition or "").strip().lower()
        if raw in ("cloud", "community"):
            return raw
        return "community"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
