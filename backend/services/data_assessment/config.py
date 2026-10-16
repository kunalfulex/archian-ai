"""Feature flags and defaults for connector data assessment."""
from __future__ import annotations

import os

DEFAULT_SAMPLE_ROW_CAP = int(os.getenv("ASSESSMENT_SAMPLE_ROW_CAP", "5000"))
DEFAULT_SF_BULK_CONCURRENCY = int(os.getenv("ASSESSMENT_SF_BULK_CONCURRENCY", "2"))
ASSESSMENT_CHUNK_SIZE = int(os.getenv("ASSESSMENT_CHUNK_SIZE", "500"))

# Core entity packs for quick scope
SF_QUICK_OBJECTS = [
    "Account",
    "Contact",
    "Lead",
    "Opportunity",
    "Case",
    "User",
    "Task",
    "Event",
    "Campaign",
    "Product2",
]
