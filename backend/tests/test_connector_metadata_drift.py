"""Tests for connector metadata drift fingerprints."""
from services.connector_metadata.drift import (
    compute_drift_from_maps,
    fingerprint_salesforce_object,
    fingerprint_sql_schema,
)


def test_fingerprint_sql_stable():
    schema = {
        "columns": [{"name": "id", "type": "int"}, {"name": "name", "type": "varchar"}],
        "foreign_keys": [{"column": "org_id", "referenced_table": "orgs"}],
    }
    assert fingerprint_sql_schema(schema) == fingerprint_sql_schema(schema)


def test_drift_detects_new_and_removed():
    prev = {"a": "hash1", "b": "hash2"}
    cur = {"a": "hash1", "c": "hash3"}
    report = compute_drift_from_maps(previous_hashes=prev, current_hashes=cur)
    assert "c" in report.new_entities
    assert "b" in report.removed_entities
    assert report.unchanged_count == 1


def test_drift_detects_schema_change():
    report = compute_drift_from_maps(
        previous_hashes={"t": "old"},
        current_hashes={"t": "new"},
    )
    assert report.changed_entities == ["t"]
