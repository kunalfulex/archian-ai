"""Tests for Salesforce connector id resolution."""
from __future__ import annotations

from services.labeling.connector_ref import connector_instance_id


def test_connector_instance_id_salesforce_prefers_token():
    ref = {"connector_id": "token-1", "org_id": "00Dabc"}
    assert connector_instance_id("salesforce", ref) == "token-1"


def test_connector_instance_id_salesforce_legacy_org_only():
    ref = {"org_id": "00Dabc"}
    assert connector_instance_id("salesforce", ref) == "00Dabc"
