"""Tests for Enterprise Graph validation helpers."""
import pytest

from services.enterprise_graph.validation import (
    check_duplicate_members,
    connector_id_from_ref,
    connector_ref_for_type,
    member_ref_key,
)


def test_connector_ref_salesforce():
    assert connector_ref_for_type("salesforce", "token-uuid-1") == {
        "connector_id": "token-uuid-1",
    }


def test_connector_id_from_ref_salesforce_legacy_org():
    assert connector_id_from_ref("salesforce", {"org_id": "00Dabc"}) == "00Dabc"


def test_connector_id_from_ref_salesforce_token():
    assert connector_id_from_ref(
        "salesforce", {"connector_id": "uuid-1", "org_id": "00Dabc"}
    ) == "uuid-1"


def test_connector_ref_sql():
    assert connector_ref_for_type("sql", "uuid-1") == {"connector_id": "uuid-1"}


def test_connector_ref_google_sheets():
    ref = connector_ref_for_type("google_sheets", "conn-1")
    assert ref.get("google_sheets_connection_id") == "conn-1"
    assert ref.get("connection_id") == "conn-1"


def test_connector_id_roundtrip():
    ref = connector_ref_for_type("sql", "abc")
    assert connector_id_from_ref("sql", ref) == "abc"


def test_connector_ref_requires_id():
    with pytest.raises(ValueError):
        connector_ref_for_type("sql", "")


def test_duplicate_members_detected():
    members = [
        {
            "connector_type": "sql",
            "connector_ref": {"connector_id": "a"},
        },
        {
            "connector_type": "sql",
            "connector_ref": {"connector_id": "a"},
        },
    ]
    with pytest.raises(Exception) as exc:
        check_duplicate_members(members)
    assert exc.value.status_code == 400


def test_member_ref_key_stable():
    a = member_ref_key("sql", {"connector_id": "x"})
    b = member_ref_key("sql", {"connector_id": "x"})
    assert a == b
