"""Community edition: no billing, every action allowed, nothing charged or tracked."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.entitlements.protocol import UNLIMITED, ValidationResult

COMMUNITY_PLAN_TYPE = "community"


class CommunityEntitlements:
    edition = "community"
    billing_enabled = False

    async def get_user_plan(self, user_id: str) -> Optional[Dict[str, Any]]:
        return {
            "plan_type": COMMUNITY_PLAN_TYPE,
            "status": "active",
            "billing_interval": None,
            "max_file_size_mb": UNLIMITED,
            "credits_per_month": UNLIMITED,
            "credit_balance": 0,
            "price_monthly": None,
            "price_yearly": None,
            "features": None,
            "stripe_subscription_id": None,
            "stripe_customer_id": None,
            "current_period_start": None,
            "current_period_end": None,
            "payment_issue": False,
            "cancel_at_period_end": False,
        }

    async def get_user_usage(
        self, user_id: str, month_year: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return {
            "records_migrated": 0,
            "total_file_size_mb": 0.0,
            "upload_count": 0,
            "migration_count": 0,
            "file_attachment_upload_count": 0,
            "credits_used": 0,
        }

    async def validate_file_upload(self, user_id: str, file_size_mb: float) -> ValidationResult:
        return True, "File upload allowed (unlimited)", {
            "plan_type": COMMUNITY_PLAN_TYPE,
            "max_file_size_mb": "unlimited",
        }

    async def track_file_upload_usage(self, user_id: str, file_size_mb: float) -> bool:
        return True

    async def validate_extraction(self, user_id: str, record_count: int) -> ValidationResult:
        return True, "Extraction allowed (unlimited)", {
            "plan_type": COMMUNITY_PLAN_TYPE,
            "requested_records": record_count,
            "can_extract": record_count,
            "remaining_records": "unlimited",
        }

    async def validate_migration(self, user_id: str, record_count: int) -> ValidationResult:
        return True, "Migration allowed (unlimited)", {
            "plan_type": COMMUNITY_PLAN_TYPE,
            "requested_records": record_count,
        }

    async def track_successful_migration(self, user_id: str, record_count: int) -> bool:
        return True

    async def validate_file_attachment_upload(
        self, user_id: str, file_size_mb: float, file_count: int = 1
    ) -> ValidationResult:
        return True, "Upload allowed (unlimited)", {
            "plan_type": COMMUNITY_PLAN_TYPE,
            "max_file_size_mb": "unlimited",
        }

    async def track_file_attachment_upload(
        self, user_id: str, file_count: int = 1, total_size_mb: float = 0.0
    ) -> bool:
        return True

    async def get_balance(self, user_id: str) -> int:
        return 0

    async def has_sufficient_balance(self, user_id: str, amount: int) -> bool:
        return True

    async def deduct(
        self,
        user_id: str,
        amount: int,
        reference_type: str,
        reference_id: Optional[str] = None,
    ) -> bool:
        return True

    async def grant_free_signup(self, user_id: str) -> bool:
        return True

    async def on_account_deleted(self, user_id: str) -> None:
        return None

    async def on_account_reactivated(self, user_id: str) -> None:
        return None

    async def cleanup_expired_deletions(self) -> None:
        return None

    async def subscription_details(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        return None
