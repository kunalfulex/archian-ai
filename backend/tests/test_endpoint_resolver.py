"""Tests for endpoint signals and merge helpers."""

from services.planning.endpoint_resolver import (
    apply_resolution_to_classification,
    merge_endpoint_signals,
    resolve_endpoints,
)
from services.planning.schemas import EndpointResolution, ResolvedEndpoint
from services.planning.resource_catalog import ResourceCatalog


def test_resolve_endpoints_honors_classification_signals_only():
    cat = ResourceCatalog()
    res = resolve_endpoints(
        "extract from contact list and migrate to same sheet",
        cat,
        classification={"destination_same_as_source": True},
    )
    assert res.source is None
    assert res.destination is None
    assert res.destination_same_as_source is True



def test_merge_endpoint_signals_uses_classification_only():
    signals = merge_endpoint_signals(
        "migrate to the same database",
        {"destination_same_as_source": True},
    )
    assert signals["destination_same_as_source"] is True

    signals = merge_endpoint_signals("migrate to the same database", {})
    assert signals["destination_same_as_source"] is False


def test_apply_resolution_to_classification_includes_connector_ids():
    resolution = EndpointResolution(
        source=ResolvedEndpoint(
            connector_type="sql",
            display_name="Test DB",
            connector_id="sql-1",
        ),
        destination=ResolvedEndpoint(
            connector_type="salesforce",
            display_name="Production 2 Org",
            connector_id="sf-token-1",
        ),
    )
    out = apply_resolution_to_classification({}, resolution)
    assert out["source_connector_id"] == "sql-1"
    assert out["destination_connector_id"] == "sf-token-1"


def test_resolve_endpoints_pinned_file_upload():
    cat = ResourceCatalog()
    res = resolve_endpoints(
        "",
        cat,
        classification={
            "source": "file",
            "source_name": "upload.csv",
            "_source_endpoint_clarified": True,
        },
    )
    assert res.source is not None
    assert res.source.connector_type == "file"
