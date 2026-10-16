"""Readiness reflects in-flight metadata/schema sync (reconnect/resync)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.labeling.readiness_service import (
    PHASE_METADATA,
    PHASE_READY,
    get_readiness_state,
)


def test_readiness_metadata_sync_started_blocks_ready():
    profile = {
        "metadata_sync_started_at": "2026-01-01T00:00:00Z",
        "metadata_sync_completed_at": "2025-06-01T00:00:00Z",
        "profile_status": "ready",
    }

    async def _run():
        with patch(
            "services.labeling.readiness_service.LabelingStore.get_business_profile",
            new=AsyncMock(return_value=profile),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_entity_labeling_stats",
            new=AsyncMock(return_value={"ready": 10, "total": 10, "pending": 0, "processing": 0}),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_active_job",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.labeling.readiness_service.get_connector_display_name",
            new=AsyncMock(return_value="SF 1"),
        ), patch(
            "services.labeling.readiness_service.get_connector_setup_status",
            new=AsyncMock(
                return_value={
                    "needs_connector_setup": False,
                    "needs_org_name": False,
                    "needs_org_description": False,
                }
            ),
        ), patch(
            "services.connector_metadata.store.get_active_job",
            new=AsyncMock(return_value=None),
        ):
            return await get_readiness_state(
                user_id="user-1",
                connector_type="salesforce",
                connector_instance_id="tok-1",
                connector_ref={"connector_id": "tok-1", "org_id": "org-1"},
            )

    state = asyncio.run(_run())
    assert state["phase"] == PHASE_METADATA
    assert state["is_ready"] is False
    assert state["metadata_sync_in_progress"] is True
    assert state["metadata_sync_complete"] is False


def test_readiness_exposes_metadata_sync_progress():
    profile = {
        "metadata_sync_started_at": "2026-01-01T00:00:00Z",
        "metadata_sync_progress": {"processed": 5, "total": 20},
        "profile_status": "pending",
    }

    async def _run():
        with patch(
            "services.labeling.readiness_service.LabelingStore.get_business_profile",
            new=AsyncMock(return_value=profile),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_entity_labeling_stats",
            new=AsyncMock(return_value={"ready": 0, "total": 0, "pending": 0, "processing": 0}),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_active_job",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.labeling.readiness_service.get_connector_display_name",
            new=AsyncMock(return_value="SF 1"),
        ), patch(
            "services.labeling.readiness_service.get_connector_setup_status",
            new=AsyncMock(
                return_value={
                    "needs_connector_setup": False,
                    "needs_org_name": False,
                    "needs_org_description": False,
                }
            ),
        ), patch(
            "services.connector_metadata.store.get_active_job",
            new=AsyncMock(return_value=None),
        ):
            return await get_readiness_state(
                user_id="user-1",
                connector_type="salesforce",
                connector_instance_id="tok-1",
                connector_ref={"connector_id": "tok-1", "org_id": "org-1"},
            )

    state = asyncio.run(_run())
    assert state["metadata_sync_progress"] == {
        "processed": 5,
        "total": 20,
        "percent": None,
        "message": None,
    }


def test_readiness_sql_analysis_incomplete_blocks_ready():
    async def _run():
        with patch(
            "services.labeling.readiness_service.LabelingStore.get_business_profile",
            new=AsyncMock(return_value={"profile_status": "ready", "metadata_sync_completed_at": "2025-01-01"}),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_entity_labeling_stats",
            new=AsyncMock(return_value={"ready": 5, "total": 5, "pending": 0, "processing": 0}),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_active_job",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.labeling.readiness_service.get_connector_display_name",
            new=AsyncMock(return_value="Test DB"),
        ), patch(
            "services.labeling.readiness_service.get_connector_setup_status",
            new=AsyncMock(
                return_value={
                    "needs_connector_setup": False,
                    "needs_org_name": False,
                    "needs_org_description": False,
                }
            ),
        ), patch(
            "services.connector_metadata.store.get_active_job",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.labeling.readiness_service.database.fetch_one",
            new=AsyncMock(return_value={"analysis_completed": False}),
        ):
            return await get_readiness_state(
                user_id="user-1",
                connector_type="sql",
                connector_instance_id="sql-1",
                connector_ref={"connector_id": "sql-1"},
            )

    state = asyncio.run(_run())
    assert state["phase"] == PHASE_METADATA
    assert state["is_ready"] is False
    assert state["metadata_sync_in_progress"] is True


def test_readiness_ready_when_idle_and_profile_ready():
    async def _run():
        with patch(
            "services.labeling.readiness_service.LabelingStore.get_business_profile",
            new=AsyncMock(
                return_value={
                    "profile_status": "ready",
                    "metadata_sync_completed_at": "2025-01-01",
                    "agent_ready_notified_at": "2025-01-02",
                }
            ),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_entity_labeling_stats",
            new=AsyncMock(return_value={"ready": 3, "total": 3, "pending": 0, "processing": 0}),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_active_job",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.labeling.readiness_service.get_connector_display_name",
            new=AsyncMock(return_value="Test DB"),
        ), patch(
            "services.labeling.readiness_service.get_connector_setup_status",
            new=AsyncMock(
                return_value={
                    "needs_connector_setup": False,
                    "needs_org_name": False,
                    "needs_org_description": False,
                }
            ),
        ), patch(
            "services.connector_metadata.store.get_active_job",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.labeling.readiness_service._infer_metadata_complete",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.labeling.readiness_service.database.fetch_one",
            new=AsyncMock(return_value={"analysis_completed": True}),
        ):
            return await get_readiness_state(
                user_id="user-1",
                connector_type="sql",
                connector_instance_id="sql-1",
                connector_ref={"connector_id": "sql-1"},
            )

    state = asyncio.run(_run())
    assert state["phase"] == PHASE_READY
    assert state["is_ready"] is True
    assert state["metadata_sync_in_progress"] is False
