"""Tests for connector assessment report section building."""
from services.data_assessment.report_synthesizer import (
    _build_data_gaps_section,
    _build_data_quality_section,
)


def test_build_data_quality_section_aggregates_totals():
    profiles = [
        {
            "entity_id": "Contact",
            "label": "Contact",
            "records_sampled": 100,
            "quality_report": {
                "duplicates": {
                    "exact_duplicates": {"count": 2},
                    "key_field_duplicates": {"count": 1},
                },
                "missing_values": {"total_missing": 5, "by_field": {"Email": 3}},
                "format_issues": {"invalid_emails_Email": [{"index": 0, "value": "bad"}]},
                "summary": {"data_quality_score": 75, "recommendations": ["Fix emails"]},
            },
        },
        {
            "entity_id": "Account",
            "label": "Account",
            "records_sampled": 50,
            "quality_report": {
                "duplicates": {
                    "exact_duplicates": {"count": 0},
                    "key_field_duplicates": {"count": 0},
                },
                "missing_values": {"total_missing": 0, "by_field": {}},
                "format_issues": {},
                "summary": {"data_quality_score": 95},
            },
        },
    ]
    dq = _build_data_quality_section(profiles)
    totals = dq["connector_totals"]
    assert totals["exact_duplicates"] == 2
    assert totals["key_field_duplicates"] == 1
    assert totals["total_missing"] == 5
    assert totals["format_issue_count"] == 1
    assert totals["entities_with_issues"] >= 1
    assert len(dq["by_entity"]) == 2
    assert dq["by_entity"][0]["entity_id"] == "Contact"
    assert dq["by_entity"][0]["missing_values"]["by_field"]["Email"] == 3


def test_build_data_quality_section_includes_errors():
    dq = _build_data_quality_section(
        [
            {
                "entity_id": "Bad",
                "label": "Bad Object",
                "error": "Bulk API failed",
                "records_sampled": 0,
            }
        ]
    )
    assert dq["by_entity"][0]["error"] == "Bulk API failed"
    assert dq["connector_totals"]["entities_with_issues"] == 1


def test_build_data_gaps_section_by_category():
    gaps = [
        {"entity_id": "Contact", "category": "relationship", "message": "Lookup missing"},
        {"entity_id": "*", "category": "schema_staleness", "message": "Stale"},
    ]
    section = _build_data_gaps_section(gaps)
    assert section["count"] == 2
    assert section["by_category"]["relationship"] == 1
    assert section["by_category"]["schema_staleness"] == 1
