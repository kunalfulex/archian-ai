"""Migration service errors with HTTP-oriented status codes."""
from __future__ import annotations

from typing import Any, Dict, Optional


class MigrationServiceError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}
