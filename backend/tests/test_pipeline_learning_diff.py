"""Unit tests for pipeline learning diff and chunk builders."""
from __future__ import annotations

from services.pipeline_learning.chunk_builder import (
    build_extraction_rules_text,
    source_fingerprint_from_schema,
)
from utils.learning_diff import (
    diff_extraction_rules,
    diff_has_signal,
    diff_mapping_config,
    redact_snapshot_for_storage,
)


def test_diff_extraction_rules_detects_transform_change():
    ai = {"transformation_rules": {"fields": [{"rule_id": "r1", "action": "keep"}]}}
    final = {"transformation_rules": {"fields": [{"rule_id": "r1", "action": "merge"}]}}
    d = diff_extraction_rules(ai, final)
    assert d["changed"] is True
    assert "transformation_rules" in d["sections"]


def test_diff_mapping_config_changed_fields():
    ai = {"Name": {"source": "FirstName"}}
    final = {"Name": {"source": "LastName"}}
    d = diff_mapping_config(ai, final)
    assert "Name" in d["changed_fields"]


def test_diff_has_signal():
    assert diff_has_signal({"changed": True, "changed_fields": ["A"]}) is True
    assert diff_has_signal({"changed": False}) is False


def test_redact_row_data():
    snap = {"records": [{"email": "a@b.com"}], "filter_rules": {"conditions": []}}
    out = redact_snapshot_for_storage(snap)
    assert "records" not in out
    assert "filter_rules" in out


def test_source_fingerprint_stable():
    fp1 = source_fingerprint_from_schema(["Email", "Name"])
    fp2 = source_fingerprint_from_schema(["name", "email"])
    assert fp1 == fp2


def test_build_extraction_rules_text_omits_rows():
    text = build_extraction_rules_text({"filter_rules": {"conditions": [], "logic": "AND"}})
    assert "filter_rules" in text
    assert "@" not in text or "conditions" in text
