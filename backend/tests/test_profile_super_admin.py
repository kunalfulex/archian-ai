"""GET /profile exposes is_super_admin so admin gating works without billing routes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.profile_management_service import ProfileManagementService


@pytest.mark.parametrize("raw, expected", [(True, True), (False, False), (None, False)])
def test_profile_includes_is_super_admin(raw, expected):
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "a@example.com",
        "first_name": "A",
        "last_name": "B",
        "company": None,
        "phone": None,
        "timezone": None,
        "is_super_admin": raw,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": None,
    }

    async def _run():
        with patch(
            "services.profile_management_service.database.fetch_one",
            new=AsyncMock(return_value=row),
        ) as fetch_one:
            profile = await ProfileManagementService.get_user_profile("u1")
        return profile, fetch_one.await_args.args[0]

    profile, query = asyncio.run(_run())
    assert "is_super_admin" in query
    assert profile["is_super_admin"] is expected
