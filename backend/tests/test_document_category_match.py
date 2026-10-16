"""Tests for document category normalization and reconcile result shape."""

from utils.document_category_match import (
    CategoryReconcileResult,
    _coerce_json_dict,
    apply_reconcile_to_tuple,
    normalize_doc_label,
)


def test_normalize_doc_label_collapse_and_strip_period():
    assert normalize_doc_label("  Contact  ") == "contact"
    assert normalize_doc_label("Used for contacts.") == "used for contacts"
    assert normalize_doc_label(None) == ""


def test_apply_reconcile_to_tuple_prefers_canonical():
    rec = CategoryReconcileResult(
        canonical_type="Contact",
        canonical_description="Contact records",
        matched_target="Contact",
        match_tier="exact",
    )
    ct, cd = apply_reconcile_to_tuple("contact", "wrong desc", rec)
    assert ct == "Contact"
    assert cd == "Contact records"


def test_reconcile_result_to_dict():
    rec = CategoryReconcileResult("A", "B", "Contact", "exact")
    d = rec.to_dict()
    assert d["canonical_document_type"] == "A"
    assert d["category_match_tier"] == "exact"


def test_coerce_json_dict_handles_null_and_objects():
    assert _coerce_json_dict(None) == {}
    assert _coerce_json_dict("null") == {}
    assert _coerce_json_dict("") == {}
    assert _coerce_json_dict({"document_type": "Contact"}) == {"document_type": "Contact"}
    assert _coerce_json_dict('{"document_type": "Contact"}') == {"document_type": "Contact"}
    assert _coerce_json_dict("[]") == {}
    assert _coerce_json_dict(["x"]) == {}
