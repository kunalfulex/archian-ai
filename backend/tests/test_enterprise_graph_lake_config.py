"""Tests for lake phase gating."""
from services.enterprise_graph.config import lake_enabled, should_run_lake_phase
from services.enterprise_graph.models import GraphMember


def test_lake_enabled_flag():
    assert lake_enabled({"lake_enabled": True}) is True
    assert lake_enabled({}) is False


def test_should_run_lake_phase_requires_flag_and_member():
    members = [
        GraphMember(
            id="l1",
            graph_id="g1",
            connector_type="sql",
            connector_ref={"connector_id": "x"},
            role="lake",
            display_name="Lake",
            sort_order=0,
        )
    ]
    assert should_run_lake_phase({"lake_enabled": True}, members) is True
    assert should_run_lake_phase({}, members) is False
    assert should_run_lake_phase({"lake_enabled": True}, []) is False
