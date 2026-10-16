"""Tests for connector business profile rollup."""
from __future__ import annotations

from services.labeling.models import ConnectorAiProfile
from services.labeling.profile_prompts import build_connector_profile_prompt


def test_connector_ai_profile_from_llm_json():
    data = {
        "synopsis": "Field service CRM for scheduling and customer visits.",
        "business_domains": ["service", "operations"],
        "system_roles": ["crm", "fsm"],
        "synonyms": ["service org", "operations system"],
        "capabilities": {
            "record_families": [{"label": "appointments", "description": "Scheduling"}],
            "not_included": ["payroll"],
        },
        "confidence": 0.9,
    }
    profile = ConnectorAiProfile.from_llm_dict(data)
    assert "Field service" in profile.synopsis
    assert "service" in profile.business_domains
    assert profile.capabilities.get("not_included") == ["payroll"]


def test_profile_prompt_includes_user_description():
    prompt = build_connector_profile_prompt(
        connector_type="salesforce",
        display_name="SF Production",
        user_description="UK field service production org",
        entity_summaries=[
            {
                "entity_key": "ServiceAppointment",
                "entity_display_name": "Service Appointment",
                "semantics": {
                    "primary": {"category": "Service Appointment", "description": "Visits"},
                    "domains": ["service"],
                },
            }
        ],
        stats={"total": 10, "ready": 8, "failed": 0},
    )
    assert "UK field service production org" in prompt
    assert "ServiceAppointment" in prompt


def test_should_rollup_ratio():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from services.labeling.connector_profile_service import ConnectorProfileService

    service = ConnectorProfileService()

    async def _run():
        with patch(
            "services.labeling.connector_profile_service.LabelingStore.get_entity_labeling_stats",
            new=AsyncMock(return_value={"total": 10, "ready": 8, "failed": 0}),
        ):
            assert await service.should_rollup(
                user_id="u1", connector_type="sql", connector_instance_id="c1"
            )
        with patch(
            "services.labeling.connector_profile_service.LabelingStore.get_entity_labeling_stats",
            new=AsyncMock(return_value={"total": 10, "ready": 2, "failed": 0}),
        ):
            assert not await service.should_rollup(
                user_id="u1", connector_type="sql", connector_instance_id="c1"
            )

    asyncio.run(_run())
