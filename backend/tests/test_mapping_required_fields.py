"""Tests for deterministic mapping post-processing."""
from __future__ import annotations

import asyncio

from services.mapping.required_fields import inject_required_salesforce_fields


def test_inject_required_fields_keeps_primary_base_object_only():
    mapping = {
        "FirstName": {"object": "Lead", "field": "FirstName", "value": "Ann"},
        "Company": {"object": "Customer", "field": "Name", "value": "Acme"},
    }
    flat = {"FirstName": "Ann", "Company": "Acme"}

    async def _run():
        return await inject_required_salesforce_fields(
            mapping,
            flat,
            base_objects=["Lead", "Customer"],
            lookup_objects=[],
            user_id="user-1",
            primary_base_object="Lead",
        )

    refined = asyncio.run(_run())
    assert "FirstName" in refined
    assert refined["FirstName"]["object"] == "Lead"
    assert "Company" not in refined
