import asyncio

import pytest

from services.migration.context_repository import get_context
from services.migration.db_row import row_get


class _FakeRecord:
    """Minimal databases.Record stand-in (supports [] only, not .get)."""

    def __init__(self, **kwargs):
        self._data = kwargs

    def __getitem__(self, key):
        return self._data[key]


def test_row_get_on_fake_record():
    row = _FakeRecord(mapping_version=3, mapping_id="m-1")
    assert row_get(row, "mapping_version") == 3
    assert row_get(row, "missing", "x") == "x"
    with pytest.raises(AttributeError):
        row.get("mapping_version")  # noqa: B018 — intentional regression guard


def test_get_context_parses_record_row(monkeypatch):
    storage = {
        "user_id": "u1",
        "extraction_job_id": "job-1",
        "mapping": {"a": {"column": "a"}},
        "document_type": "t",
        "destination": "sql",
        "connector_id": "c1",
        "table_name": "users",
    }

    async def fake_fetch_one(*_a, **_k):
        return _FakeRecord(
            context_json=storage,
            mapping_version=2,
            mapping_id="mapping-abc",
        )

    async def fake_ensure():
        return None

    monkeypatch.setattr(
        "services.migration.context_repository.ensure_schema", fake_ensure
    )
    monkeypatch.setattr(
        "services.migration.context_repository.database.fetch_one", fake_fetch_one
    )

    async def run():
        ctx = await get_context(
            user_id="u1",
            conversation_id="conv-1",
            extraction_job_id="job-1",
        )
        assert ctx is not None
        assert ctx.mapping_version == 2
        assert ctx.mapping_id == "mapping-abc"
        assert ctx.connector_id == "c1"
        assert ctx.table_name == "users"

    asyncio.run(run())
