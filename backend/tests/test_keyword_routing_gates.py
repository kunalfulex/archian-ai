"""Keyword routing removed from planning hot paths."""
from __future__ import annotations

from services.planning.continuation_policy import apply_extraction_session_continuation
from services.planning.endpoint_resolver import resolve_endpoints
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef


def _sf_catalog() -> ResourceCatalog:
    return ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )


def test_resolve_endpoints_does_not_keyword_match_connectors():
    cls = {
        "plan_route": "continuation_path",
        "active_job_id": "job-1",
        "source": "salesforce",
        "source_name": "SF 1",
    }
    resolution = resolve_endpoints("map to SF 2 org", _sf_catalog(), classification=cls)
    assert resolution.source is None
    assert resolution.destination is None


def test_extraction_session_continuation_does_not_keyword_override():
    catalog = _sf_catalog()
    classification = {
        "session_action": "continue_active_job",
        "active_job_id": "job-1",
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "unknown",
    }
    extraction_meta = {
        "source_type": "salesforce",
        "source_name": "SF 1",
        "job_id": "job-1",
    }
    out = apply_extraction_session_continuation(
        classification,
        extraction_meta=extraction_meta,
        prompt="move the data to SF 2",
        catalog=catalog,
    )
    assert out["source_name"] == "SF 1"
    assert out.get("destination_name") in (None, "unknown", "")


def test_extraction_session_continuation_keeps_job_pins_without_keywords():
    catalog = _sf_catalog()
    classification = {"source": "unknown", "destination": "unknown", "intent": "map_data"}
    extraction_meta = {
        "source_type": "salesforce",
        "source_name": "SF 1",
        "destination": "salesforce",
        "destination_name": "SF 1",
    }
    out = apply_extraction_session_continuation(
        classification,
        extraction_meta=extraction_meta,
        prompt="map to SF 2",
        catalog=catalog,
    )
    assert out["source_name"] == "SF 1"
    assert out["destination_name"] == "SF 1"
