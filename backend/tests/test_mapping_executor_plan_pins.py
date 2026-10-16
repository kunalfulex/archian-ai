"""Tests that mapping target resolution respects plan pins (no catalog override)."""
from __future__ import annotations

import asyncio

from services.mapping_plan.target_resolution import (
    resolve_mapping_target,
    resolve_mapping_target_async,
)


def test_plan_contract_hint_not_overridden_by_sync_resolver():
    resolved = resolve_mapping_target(
        destination="salesforce",
        mapping_instruction="map customer records to SF 2",
        classification_snapshot={
            "plan_contract": {
                "destination": {
                    "connector_type": "salesforce",
                    "entity_key": "Lead",
                }
            }
        },
    )
    assert resolved.target_object_api_name == "Lead"
    assert resolved.resolution_source == "plan_pinned"


def test_async_resolver_skips_catalog_when_hint_present(monkeypatch):
    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("catalog LLM must not run when plan pins entity")

    monkeypatch.setattr(
        "services.mapping_plan.catalog_target_resolver.resolve_salesforce_destination_from_catalog",
        _should_not_run,
    )

    async def _run():
        return await resolve_mapping_target_async(
            destination="salesforce",
            mapping_instruction="build sales lead pipeline between SF 1 and SF 2",
            classification_snapshot={
                "destination_object_api_name": "Lead",
                "plan_contract": {
                    "destination": {"entity_key": "Lead", "connector_type": "salesforce"},
                },
            },
            user_id="user-1",
            destination_name="SF 2",
        )

    resolved = asyncio.run(_run())
    assert resolved.target_object_api_name == "Lead"
    assert resolved.resolution_source == "plan_pinned"
