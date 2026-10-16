"""Tests for labeling models and adapter scheduling."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from services.labeling.config import LabelingConfig
from services.labeling.catalog_helpers import merge_catalog_entry, primary_from_semantics
from services.labeling.models import EntitySemantics


def test_entity_semantics_from_multi_label_json():
    data = {
        "primary": {
            "category": "Service Appointment",
            "description": "Field service scheduling records.",
        },
        "domains": ["service", "appointments"],
        "processes": ["scheduling"],
        "synonyms": ["appointments"],
        "confidence": 0.9,
    }
    sem = EntitySemantics.from_llm_dict(data)
    assert sem.primary.category == "Service Appointment"
    assert "service" in sem.domains
    assert sem.legacy_ai_categorization()["document_type"] == "Service Appointment"
    assert sem.legacy_ai_categorization()["confidence_score"] == 90


def test_entity_semantics_legacy_shape_fallback():
    data = {
        "document_type": "Customer Records",
        "document_description": "Customer table",
        "confidence_score": 75,
    }
    sem = EntitySemantics.from_llm_dict(data)
    assert sem.primary.category == "Customer Records"
    assert sem.confidence == 0.75


def test_labeling_config_concurrency():
    assert LabelingConfig.max_concurrency() >= 1
    assert LabelingConfig.batch_size() >= 1


def test_merge_catalog_entry_prefers_semantics():
    semantics = {
        "primary": {"category": "Appointments", "description": "Rich desc"},
        "domains": ["service"],
        "synonyms": ["visits"],
    }
    entry = {"table_name": "svc_appt", "category": "Old", "description": "Old desc"}
    merged = merge_catalog_entry(entry, semantics=semantics)
    assert merged["category"] == "Appointments"
    assert merged["description"] == "Rich desc"
    assert merged["domains"] == ["service"]
    assert primary_from_semantics(semantics)["category"] == "Appointments"


def test_sql_adapter_queues_labeling(monkeypatch):
    scheduled = {}

    def fake_schedule(**kwargs):
        scheduled.update(kwargs)

    monkeypatch.setattr(
        "services.connector_metadata.adapters.sql_adapter.schedule_entity_labeling_fire_and_forget",
        fake_schedule,
    )
    monkeypatch.setattr(
        "services.connector_metadata.adapters.sql_adapter.collect_entities_needing_labeling",
        AsyncMock(return_value=["customers", "orders"]),
    )

    adapter = __import__(
        "services.connector_metadata.adapters.sql_adapter", fromlist=["SqlMetadataAdapter"]
    ).SqlMetadataAdapter(user_id="user-1")

    drift = MagicMock()
    drift.entities_needing_categorization.return_value = ["customers", "orders"]

    adapter._bg.get_connector_details = AsyncMock(
        return_value={"connector_type": "postgresql", "id": "c1"}
    )
    adapter._bg.prepare_connector_config = MagicMock(return_value={})

    async def _run():
        with patch.object(adapter, "_load_previous_hashes", return_value={}), patch.object(
            adapter, "_upsert_table_schema", new=AsyncMock()
        ), patch(
            "services.connector_metadata.adapters.sql_adapter.SQLConnectorFactory"
        ) as factory:
            inst = factory.create_connector.return_value
            inst.get_tables = AsyncMock(return_value=["customers", "orders"])
            inst.get_table_schema = AsyncMock(return_value={"columns": [{"name": "id"}]})
            inst.close_connection = AsyncMock()

            with patch(
                "services.connector_metadata.adapters.sql_adapter.compute_drift_from_maps",
                return_value=drift,
            ), patch(
                "services.connector_metadata.empty_catalog_cleanup.purge_sql_table_catalog_entry",
                new_callable=AsyncMock,
            ), patch(
                "services.connector_metadata.empty_catalog_cleanup.purge_sql_tables_without_columns",
                new_callable=AsyncMock,
            ), patch("services.connector_metadata.adapters.sql_adapter.database") as db:
                db.execute = AsyncMock()
                db.fetch_all = AsyncMock(return_value=[])
                db.fetch_one = AsyncMock(return_value=None)

                return await adapter.sync_schema(
                    {"connector_id": "conn-1"},
                    sync_mode="schema_and_categorize",
                )

    import asyncio

    result = asyncio.run(_run())
    assert result is drift
    assert scheduled.get("connector_type") == "sql"
    assert scheduled.get("entity_keys") == ["customers", "orders"]
