"""Edition selection, community no-op behaviour, and GET /features (cloud tests live in archian_cloud)."""
from __future__ import annotations

import asyncio
import inspect
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.entitlements import (
    EDITION_ENV_VAR,
    UNLIMITED,
    Entitlements,
    get_edition,
    get_entitlements,
)
from services.entitlements.community import CommunityEntitlements
from services.entitlements.protocol import Entitlements as EntitlementsProtocol


@pytest.fixture(autouse=True)
def _reset_entitlements_cache():
    from services.settings import get_settings

    get_entitlements.cache_clear()
    get_settings.cache_clear()
    yield
    get_entitlements.cache_clear()
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "community"),
        ("", "community"),
        ("cloud", "cloud"),
        (" Cloud ", "cloud"),
        ("community", "community"),
        ("enterprise", "community"),
    ],
)
def test_get_edition(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(EDITION_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(EDITION_ENV_VAR, value)
    assert get_edition() == expected


def test_get_entitlements_defaults_to_community(monkeypatch):
    monkeypatch.delenv(EDITION_ENV_VAR, raising=False)
    entitlements = get_entitlements()
    assert isinstance(entitlements, CommunityEntitlements)
    assert entitlements.billing_enabled is False


def test_community_satisfies_protocol():
    impl = CommunityEntitlements()
    assert isinstance(impl, Entitlements)
    for name, member in inspect.getmembers(EntitlementsProtocol, inspect.isfunction):
        if name.startswith("_"):
            continue
        impl_method = getattr(impl, name)
        assert inspect.iscoroutinefunction(impl_method), name
        expected = list(inspect.signature(member).parameters)[1:]
        assert list(inspect.signature(impl_method).parameters) == expected, name


def test_community_allows_everything_and_never_charges():
    community = CommunityEntitlements()

    async def _run():
        plan = await community.get_user_plan("u1")
        assert plan["credits_per_month"] == UNLIMITED
        assert plan["max_file_size_mb"] == UNLIMITED
        assert (await community.get_user_usage("u1"))["records_migrated"] == 0
        for ok, _msg, details in (
            await community.validate_file_upload("u1", 10_000.0),
            await community.validate_extraction("u1", 10**9),
            await community.validate_migration("u1", 10**9),
            await community.validate_file_attachment_upload("u1", 10_000.0, 500),
        ):
            assert ok is True
            assert details["plan_type"] == "community"
        assert await community.has_sufficient_balance("u1", 10**9) is True
        assert await community.deduct("u1", 10**9, "migration", "job-1") is True
        assert await community.track_successful_migration("u1", 5) is True
        assert await community.track_file_upload_usage("u1", 1.0) is True
        assert await community.track_file_attachment_upload("u1", 2, 1.0) is True
        assert await community.grant_free_signup("u1") is True

    asyncio.run(_run())


def test_community_does_not_import_billing_modules(monkeypatch):
    for mod in ("archian_cloud.plan_management_service", "archian_cloud.credit_service", "archian_cloud.stripe_service"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setenv(EDITION_ENV_VAR, "community")
    community = get_entitlements()

    async def _run():
        await community.get_user_plan("u1")
        await community.validate_migration("u1", 1)
        await community.deduct("u1", 1, "migration", "job-1")
        assert await community.on_account_deleted("u1") is None
        assert await community.on_account_reactivated("u1") is None
        assert await community.cleanup_expired_deletions() is None
        assert await community.subscription_details("sub_1") is None

    asyncio.run(_run())
    assert "archian_cloud.plan_management_service" not in sys.modules
    assert "archian_cloud.credit_service" not in sys.modules
    assert "archian_cloud.stripe_service" not in sys.modules


def test_extraction_limit_unlimited_in_community(monkeypatch):
    from services.architect_agent import get_user_extraction_limit
    from services.entitlements import UNLIMITED_EXTRACTION_RECORDS

    monkeypatch.setenv(EDITION_ENV_VAR, "community")
    limit = asyncio.run(get_user_extraction_limit("u1"))
    assert limit["limit_reached"] is False
    assert limit["show_upgrade_option"] is False
    assert limit["extraction_cap"] is None
    assert limit["can_extract"] == UNLIMITED_EXTRACTION_RECORDS
    assert list(range(5))[: limit["can_extract"]] == list(range(5))


@pytest.mark.parametrize(
    "edition, expected",
    [
        (None, {"edition": "community", "billing": False}),
        ("community", {"edition": "community", "billing": False}),
    ],
)
def test_features_endpoint(monkeypatch, edition, expected):
    from uuid import uuid4

    from routes.auth import get_current_user
    from routes.features import router

    if edition is None:
        monkeypatch.delenv(EDITION_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(EDITION_ENV_VAR, edition)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: uuid4()
    response = TestClient(app).get("/features")
    assert response.status_code == 200
    assert response.json() == expected


def test_features_endpoint_requires_auth():
    from routes.features import router

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/features")
    assert response.status_code == 401


def test_user_management_uses_entitlement_hooks(monkeypatch):
    from services.user_management_service import UserManagementService

    hooks = MagicMock()
    hooks.on_account_deleted = AsyncMock()
    hooks.cleanup_expired_deletions = AsyncMock()

    async def _run():
        with patch("services.user_management_service.get_entitlements", return_value=hooks), patch(
            "services.user_management_service.database.execute", new=AsyncMock()
        ), patch("services.user_management_service.database.fetch_all", new=AsyncMock(return_value=[])):
            ok, _ = await UserManagementService.soft_delete_user("u1")
            await UserManagementService.cleanup_expired_deletions()
        return ok

    assert asyncio.run(_run()) is True
    hooks.on_account_deleted.assert_awaited_once_with("u1")
    hooks.cleanup_expired_deletions.assert_awaited_once_with()


def test_user_management_community_never_loads_stripe(monkeypatch):
    monkeypatch.delitem(sys.modules, "archian_cloud.stripe_service", raising=False)
    monkeypatch.setenv(EDITION_ENV_VAR, "community")
    from services.user_management_service import UserManagementService

    async def _run():
        with patch("services.user_management_service.database.execute", new=AsyncMock()):
            return await UserManagementService.soft_delete_user("u1")

    ok, _ = asyncio.run(_run())
    assert ok is True
    assert "archian_cloud.stripe_service" not in sys.modules


def test_cloud_without_package_fails_clearly(monkeypatch):
    monkeypatch.setenv(EDITION_ENV_VAR, "cloud")
    monkeypatch.setitem(sys.modules, "archian_cloud.entitlements", None)
    with pytest.raises(RuntimeError, match="archian_cloud"):
        get_entitlements()
