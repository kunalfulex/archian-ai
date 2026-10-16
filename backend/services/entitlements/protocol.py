"""Entitlements interface used by core (non-billing) code.

Core code asks "may this user do X?" and "record that X happened" through this
interface instead of calling billing services directly, so the community edition
can run without any billing tables or Stripe configuration.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable

ValidationResult = Tuple[bool, str, Dict[str, Any]]

UNLIMITED = -1

# Record count reported as "can extract" when billing is off; JSON/JS-safe and
# larger than any real extraction, so slicing and numeric comparisons pass through.
UNLIMITED_EXTRACTION_RECORDS = 2_147_483_647


@runtime_checkable
class Entitlements(Protocol):
    edition: str
    billing_enabled: bool

    async def get_user_plan(self, user_id: str) -> Optional[Dict[str, Any]]: ...

    async def get_user_usage(
        self, user_id: str, month_year: Optional[str] = None
    ) -> Optional[Dict[str, Any]]: ...

    async def validate_file_upload(self, user_id: str, file_size_mb: float) -> ValidationResult: ...

    async def track_file_upload_usage(self, user_id: str, file_size_mb: float) -> bool: ...

    async def validate_extraction(self, user_id: str, record_count: int) -> ValidationResult: ...

    async def validate_migration(self, user_id: str, record_count: int) -> ValidationResult: ...

    async def track_successful_migration(self, user_id: str, record_count: int) -> bool: ...

    async def validate_file_attachment_upload(
        self, user_id: str, file_size_mb: float, file_count: int = 1
    ) -> ValidationResult: ...

    async def track_file_attachment_upload(
        self, user_id: str, file_count: int = 1, total_size_mb: float = 0.0
    ) -> bool: ...

    async def get_balance(self, user_id: str) -> int: ...

    async def has_sufficient_balance(self, user_id: str, amount: int) -> bool: ...

    async def deduct(
        self,
        user_id: str,
        amount: int,
        reference_type: str,
        reference_id: Optional[str] = None,
    ) -> bool: ...

    async def grant_free_signup(self, user_id: str) -> bool: ...

    # Account lifecycle: let billing pause, resume or cancel subscriptions.
    async def on_account_deleted(self, user_id: str) -> None: ...

    async def on_account_reactivated(self, user_id: str) -> None: ...

    async def cleanup_expired_deletions(self) -> None: ...

    async def subscription_details(self, subscription_id: str) -> Optional[Dict[str, Any]]: ...
