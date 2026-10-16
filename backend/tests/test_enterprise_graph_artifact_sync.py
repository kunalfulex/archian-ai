"""Tests for EGA artifact sync: canonical prune and assessment_stale settings."""
import asyncio
from datetime import datetime, timezone

import pytest

from services.enterprise_graph import store


def test_replace_canonical_entities_deletes_orphans(monkeypatch):
    executed: list = []

    async def mock_upsert(graph_id, entities):
        executed.append(("upsert", graph_id, len(entities)))

    async def mock_execute(query, values):
        executed.append(("execute", query.strip(), values))

    async def mock_list(graph_id):
        return [{"id": "keep-1", "graph_id": graph_id, "name": "Account"}]

    monkeypatch.setattr(store, "upsert_canonical_entities", mock_upsert)
    monkeypatch.setattr(store.database, "execute", mock_execute)
    monkeypatch.setattr(store, "list_canonical_entities", mock_list)

    async def _run():
        entities = [{"id": "keep-1", "name": "Account", "source_lineage": []}]
        return await store.replace_canonical_entities("g1", entities)

    result = asyncio.run(_run())

    assert len(result) == 1
    assert executed[0][0] == "upsert"
    delete_call = next(c for c in executed if c[0] == "execute")
    assert "DELETE FROM canonical_entities" in delete_call[1]
    assert delete_call[2]["keep_ids"] == ["keep-1"]


def test_replace_canonical_entities_empty_deletes_all(monkeypatch):
    executed: list = []

    async def mock_upsert(graph_id, entities):
        executed.append("upsert")

    async def mock_delete_all(graph_id):
        executed.append(("delete_all", graph_id))

    async def mock_list(graph_id):
        return []

    monkeypatch.setattr(store, "upsert_canonical_entities", mock_upsert)
    monkeypatch.setattr(store, "delete_canonical_entities_for_graph", mock_delete_all)
    monkeypatch.setattr(store, "list_canonical_entities", mock_list)

    async def _run():
        await store.replace_canonical_entities("g1", [])

    asyncio.run(_run())

    assert executed == ["upsert", ("delete_all", "g1")]


def test_mark_assessment_stale_merges_settings(monkeypatch):
    captured: dict = {}

    async def mock_get_graph(graph_id, user_id):
        return type(
            "G",
            (),
            {"settings": {"lake_enabled": True}},
        )()

    async def mock_update_graph(graph_id, user_id, **kwargs):
        captured.update(kwargs.get("settings") or {})

    monkeypatch.setattr(store, "get_graph", mock_get_graph)
    monkeypatch.setattr(store, "update_graph", mock_update_graph)

    async def _run():
        await store.mark_assessment_stale("g1", "u1", reason="members_changed")

    asyncio.run(_run())

    assert captured["lake_enabled"] is True
    assert captured["assessment_stale"] is True
    assert captured["assessment_stale_reason"] == "members_changed"
    assert captured["assessment_stale_at"]


def test_clear_assessment_stale_removes_keys(monkeypatch):
    captured: dict = {}

    async def mock_get_graph(graph_id, user_id):
        return type(
            "G",
            (),
            {
                "settings": {
                    "lake_enabled": True,
                    "assessment_stale": True,
                    "assessment_stale_at": datetime.now(timezone.utc).isoformat(),
                    "assessment_stale_reason": "members_changed",
                },
            },
        )()

    async def mock_update_graph(graph_id, user_id, **kwargs):
        captured.update(kwargs.get("settings") or {})

    monkeypatch.setattr(store, "get_graph", mock_get_graph)
    monkeypatch.setattr(store, "update_graph", mock_update_graph)

    async def _run():
        await store.clear_assessment_stale("g1", "u1")

    asyncio.run(_run())

    assert captured["lake_enabled"] is True
    assert "assessment_stale" not in captured
    assert "assessment_stale_at" not in captured
    assert "assessment_stale_reason" not in captured


def test_replace_members_prunes_canonical_and_marks_stale(monkeypatch):
    calls: list = []

    async def mock_get_graph(graph_id, user_id):
        return type("G", (), {"settings": {}})()

    async def mock_execute(query, values):
        calls.append(("execute", query.strip()[:40]))

    async def mock_insert(graph_id, member, sort_order=0):
        return "new-member-id"

    async def mock_delete_canonical(graph_id):
        calls.append(("delete_canonical", graph_id))

    async def mock_mark_stale(graph_id, user_id, *, reason="members_changed"):
        calls.append(("stale", graph_id, reason))

    async def mock_list_members(graph_id):
        return []

    monkeypatch.setattr(store, "get_graph", mock_get_graph)
    monkeypatch.setattr(store.database, "execute", mock_execute)
    monkeypatch.setattr(store, "_insert_member", mock_insert)
    monkeypatch.setattr(store, "delete_canonical_entities_for_graph", mock_delete_canonical)
    monkeypatch.setattr(store, "mark_assessment_stale", mock_mark_stale)
    monkeypatch.setattr(store, "list_members", mock_list_members)

    async def _run():
        await store.replace_members(
            "g1",
            "u1",
            [{"connector_type": "sql", "connector_ref": {"connector_id": "x"}}],
        )

    asyncio.run(_run())

    assert ("delete_canonical", "g1") in calls
    assert ("stale", "g1", "members_changed") in calls
