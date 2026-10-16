"""Tests for mapping destination target catalog API helpers."""
import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from services.mapping_plan.destination_targets_api import list_destination_targets


def test_list_destination_targets_salesforce_empty_name():
    payload = asyncio.run(
        list_destination_targets(
            user_id=uuid4(),
            destination="salesforce",
            destination_name="",
        )
    )
    assert payload["targets"] == []
    assert payload["target_kind"] is None


def test_list_destination_targets_salesforce_objects():
    user_id = uuid4()
    with (
        patch(
            "services.mapping_plan.destination_targets_api.resolve_salesforce_token_record",
            new_callable=AsyncMock,
            return_value={"org_id": "org-1"},
        ),
        patch(
            "services.mapping_plan.catalog_target_resolver._load_salesforce_org_objects",
            new_callable=AsyncMock,
            return_value=[
                {"object_api_name": "Contact", "label": "Contact"},
                {"object_api_name": "ServiceAppointment", "label": "Service Appointment"},
            ],
        ),
    ):
        payload = asyncio.run(
            list_destination_targets(
                user_id=user_id,
                destination="salesforce",
                destination_name="SF 1",
            )
        )

    assert payload["target_kind"] == "object"
    assert len(payload["targets"]) == 2
    values = {t["value"] for t in payload["targets"]}
    assert values == {"Contact", "ServiceAppointment"}
