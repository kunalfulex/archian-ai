"""Tests for lake member validation."""
import asyncio

import pytest
from fastapi import HTTPException

from services.enterprise_graph import validation


def test_validate_lake_rejects_duplicate_lake_members(monkeypatch):
    async def _run():
        async def _noop(*_a, **_k):
            return None

        monkeypatch.setattr(validation, "database", type("DB", (), {"fetch_one": staticmethod(_noop)})())
        members = [
            {"role": "lake", "connector_type": "sql", "connector_ref": {"connector_id": "a"}},
            {"role": "lake", "connector_type": "sql", "connector_ref": {"connector_id": "b"}},
        ]
        with pytest.raises(HTTPException) as exc:
            await validation.validate_lake_member_constraints("user-1", members)
        assert exc.value.status_code == 400

    asyncio.run(_run())


def test_validate_lake_rejects_non_snowflake(monkeypatch):
    async def _run():
        async def _fetch_one(_q, _p):
            return {"connector_type": "postgres"}

        monkeypatch.setattr(validation, "database", type("DB", (), {"fetch_one": staticmethod(_fetch_one)})())
        members = [
            {"role": "lake", "connector_type": "sql", "connector_ref": {"connector_id": "a"}},
        ]
        with pytest.raises(HTTPException) as exc:
            await validation.validate_lake_member_constraints("user-1", members)
        assert exc.value.status_code == 400
        assert "Snowflake" in exc.value.detail

    asyncio.run(_run())


def test_validate_lake_accepts_snowflake(monkeypatch):
    async def _run():
        async def _fetch_one(_q, _p):
            return {"connector_type": "snowflake"}

        monkeypatch.setattr(validation, "database", type("DB", (), {"fetch_one": staticmethod(_fetch_one)})())
        members = [
            {"role": "lake", "connector_type": "sql", "connector_ref": {"connector_id": "a"}},
        ]
        await validation.validate_lake_member_constraints("user-1", members)

    asyncio.run(_run())
