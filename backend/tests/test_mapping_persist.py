"""Tests for mapping persist layer (Phase 4)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.session import AgentSession
from services.mapping.persist import (
    document_type_for_persist,
    persist_mapping_execution_result,
    resolve_sheet_name_for_persist,
)


def test_document_type_for_persist_prefers_pinned_entity():
    dt = document_type_for_persist(
        row={"document_type": ""},
        destination_entity="Lead",
        destination_document_type="Contact",
    )
    assert dt == "Lead"


def test_resolve_sheet_name_from_plan_pin():
    sheet = resolve_sheet_name_for_persist(
        destination="google_sheets",
        result={"available_sheets": {"Leads": {}, "Contacts": {}}},
        classification_snapshot={"destination_sheet_name": "Leads"},
    )
    assert sheet == "Leads"


def test_persist_mapping_execution_result_upserts_context():
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="map",
        active_job_id="job-1",
        destination="salesforce",
        destination_name="SF 2",
    )
    captured: dict = {}

    async def fake_persist(sess, **kwargs):
        captured.update(kwargs)
        from services.migration.context import MigrationContext

        return MigrationContext(
            user_id=sess.user_id,
            extraction_job_id=sess.active_job_id,
            mapping=kwargs["mapping"],
            document_type=kwargs["document_type"],
            destination="salesforce",
            conversation_id=sess.conversation_id,
            mapping_version=3,
        )

    async def _run():
        with patch(
            "services.migration.session_migration.persist_session_migration_context",
            new=AsyncMock(side_effect=fake_persist),
        ):
            with patch(
                "services.conversation.session_sync.sync_session_after_mapping",
                new=AsyncMock(),
            ):
                return await persist_mapping_execution_result(
                    session,
                    mapping_id="mapping-1",
                    destination="salesforce",
                    destination_name="SF 2",
                    mapping_cfg={"Name": {"object": "Lead", "field": "LastName"}},
                    result={
                        "destination_object_api_name": "Lead",
                        "transformation_rules": {"fields": []},
                    },
                    row={"source_type": "salesforce"},
                    document_type="Lead",
                )

    saved = asyncio.run(_run())
    assert saved is not None
    assert saved.mapping_version == 3
    assert captured["destination_object_api_name"] == "Lead"
    assert captured["mapping"]["Name"]["object"] == "Lead"
