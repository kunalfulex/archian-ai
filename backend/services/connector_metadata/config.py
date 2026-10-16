"""Feature flags for Connector Metadata Agent."""
from __future__ import annotations

import os

DEFAULT_SCHEMA_SYNC_CONCURRENCY = int(os.getenv("SCHEMA_SYNC_MAX_CONCURRENT", "8"))
