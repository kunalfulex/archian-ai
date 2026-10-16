"""Phase 7.3–7.5: fresh path cleanup and approve handoff."""
from __future__ import annotations


def test_fresh_path_never_skips_source_binding():
    from services.planning.continuation_policy import should_skip_source_binding

    cls = {
        "plan_route": "fresh_path",
        "active_job_id": "job-1",
        "operations": ["migrate"],
    }
    assert not should_skip_source_binding(cls, {"operations": ["migrate"]}, prompt="start migration")


def test_continuation_path_may_skip_source_binding():
    from services.planning.continuation_policy import should_skip_source_binding

    cls = {
        "active_job_id": "job-1",
        "operations": ["migrate"],
    }
    assert should_skip_source_binding(cls, {"operations": ["migrate"]}, prompt="start migration")


def test_sanitize_skips_continuation_route():
    from services.planning.connector_endpoint_confirm import sanitize_unconfirmed_connector_endpoints
    from services.planning.resource_catalog import ResourceCatalog

    cls = {
        "plan_route": "continuation_path",
        "destination": "salesforce",
        "destination_name": "SF Org",
    }
    out = sanitize_unconfirmed_connector_endpoints(cls, "", ResourceCatalog())
    assert out.get("destination_name") == "SF Org"


async def _sf_object_hint_without_prompt():
    from unittest.mock import patch

    from handlers.salesforce_handler import determine_salesforce_object_and_fields

    with patch("db.connection.database.fetch_all", return_value=[]):
        result = await determine_salesforce_object_and_fields(
            "",
            "user-1",
            object_hint="Contact",
        )
    assert result.get("clarification_message") != (
        "Please provide a request. What Salesforce data would you like to extract?"
    )


def test_sf_extract_allows_object_hint_without_prompt():
    import asyncio

    asyncio.run(_sf_object_hint_without_prompt())
