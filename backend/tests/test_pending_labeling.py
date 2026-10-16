"""Tests for semantics-aware pending labeling scheduling."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


def test_schedule_pending_entity_labeling_queues_stale_entities():
    from services.labeling.pending_labeling import schedule_pending_entity_labeling

    async def _run():
        with patch(
            "services.labeling.pending_labeling.normalize_connector_ref",
            new=AsyncMock(return_value={"connector_id": "tok-1", "org_id": "org-1"}),
        ), patch(
            "services.labeling.pending_labeling.connector_instance_id",
            return_value="tok-1",
        ), patch(
            "services.labeling.pending_labeling.collect_entities_needing_labeling",
            new=AsyncMock(return_value=["Contact", "Account"]),
        ), patch(
            "services.labeling.pending_labeling.schedule_entity_labeling",
            new=AsyncMock(return_value="job-123"),
        ) as mock_schedule:
            count = await schedule_pending_entity_labeling(
                user_id="user-1",
                connector_type="salesforce",
                connector_ref={"connector_id": "tok-1", "org_id": "org-1"},
                current_hashes={"Contact": "h1", "Account": "h2"},
                drift_keys=[],
                legacy_keys=[],
            )
        assert count == 2
        mock_schedule.assert_awaited_once()
        call_kw = mock_schedule.await_args.kwargs
        assert call_kw["entity_keys"] == ["Contact", "Account"]
        assert call_kw["connector_type"] == "salesforce"

    asyncio.run(_run())


def test_schedule_pending_entity_labeling_returns_zero_when_none_pending():
    from services.labeling.pending_labeling import schedule_pending_entity_labeling

    async def _run():
        with patch(
            "services.labeling.pending_labeling.normalize_connector_ref",
            new=AsyncMock(return_value={"connector_id": "tok-1"}),
        ), patch(
            "services.labeling.pending_labeling.connector_instance_id",
            return_value="tok-1",
        ), patch(
            "services.labeling.pending_labeling.collect_entities_needing_labeling",
            new=AsyncMock(return_value=[]),
        ), patch(
            "services.labeling.pending_labeling.schedule_entity_labeling",
            new=AsyncMock(),
        ) as mock_schedule:
            count = await schedule_pending_entity_labeling(
                user_id="user-1",
                connector_type="sql",
                connector_ref={"connector_id": "conn-1"},
                current_hashes={"users": "abc"},
            )
        assert count == 0
        mock_schedule.assert_not_awaited()

    asyncio.run(_run())


def test_check_and_notify_does_not_mark_on_email_failure():
    from services.labeling.readiness_service import check_and_notify_agent_ready

    ready_state = {
        "phase": "ready",
        "display_name": "SF 1",
        "needs_connector_setup": False,
        "setup_connections_path": None,
    }

    async def _run():
        with patch(
            "services.labeling.readiness_service.normalize_connector_ref",
            new=AsyncMock(return_value={"connector_id": "tok-1", "org_id": "org-1"}),
        ), patch(
            "services.labeling.readiness_service.resolve_connector_instance_id",
            return_value="tok-1",
        ), patch(
            "services.labeling.readiness_service.get_readiness_state",
            new=AsyncMock(return_value=ready_state),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_business_profile",
            new=AsyncMock(return_value={"profile_status": "ready"}),
        ), patch(
            "services.labeling.readiness_service.database.fetch_one",
            new=AsyncMock(return_value={"email": "user@example.com"}),
        ), patch(
            "utils.email_utils.send_connector_agent_ready_email",
            new=AsyncMock(side_effect=Exception("Postmark error")),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.mark_agent_ready_notified",
            new=AsyncMock(),
        ) as mock_mark:
            state = await check_and_notify_agent_ready(
                user_id="user-1",
                connector_type="salesforce",
                connector_ref={"connector_id": "tok-1", "org_id": "org-1"},
            )
        assert state["phase"] == "ready"
        mock_mark.assert_not_awaited()

    asyncio.run(_run())


def test_check_and_notify_marks_after_successful_email():
    from services.labeling.readiness_service import check_and_notify_agent_ready

    ready_state = {
        "phase": "ready",
        "display_name": "SF 1",
        "needs_connector_setup": False,
        "setup_connections_path": None,
    }

    async def _run():
        with patch(
            "services.labeling.readiness_service.normalize_connector_ref",
            new=AsyncMock(return_value={"connector_id": "tok-1", "org_id": "org-1"}),
        ), patch(
            "services.labeling.readiness_service.resolve_connector_instance_id",
            return_value="tok-1",
        ), patch(
            "services.labeling.readiness_service.get_readiness_state",
            new=AsyncMock(return_value=ready_state),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.get_business_profile",
            new=AsyncMock(return_value={"profile_status": "ready"}),
        ), patch(
            "services.labeling.readiness_service.database.fetch_one",
            new=AsyncMock(return_value={"email": "user@example.com"}),
        ), patch(
            "utils.email_utils.send_connector_agent_ready_email",
            new=AsyncMock(),
        ), patch(
            "services.labeling.readiness_service.LabelingStore.mark_agent_ready_notified",
            new=AsyncMock(),
        ) as mock_mark:
            state = await check_and_notify_agent_ready(
                user_id="user-1",
                connector_type="salesforce",
                connector_ref={"connector_id": "tok-1", "org_id": "org-1"},
            )
        assert state.get("agent_ready_notified") is True
        mock_mark.assert_awaited_once()

    asyncio.run(_run())
