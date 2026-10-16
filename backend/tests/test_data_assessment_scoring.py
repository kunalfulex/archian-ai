"""Unit tests for assessment scoring and profiling."""
from services.data_assessment.scoring import rollup_connector_scores, score_entity_profile
from services.data_assessment.streaming_profiler import profile_rows_single
from utils.data_quality_analyzer import analyze_data_quality, merge_quality_profiles


def test_score_entity_profile_empty():
    dims = score_entity_profile({}, 0)
    assert "quality" in dims
    assert dims["quality"] == 100.0


def test_rollup_connector_scores_structure_penalty_for_gaps():
    entities = [
        {
            "entity_id": "Contact",
            "dimension_scores": {
                "completeness": 80,
                "quality": 90,
                "consistency": 85,
                "structure": 85,
                "warehouse_readiness": 88,
            },
        }
    ]
    gaps = [
        {"entity_id": "Contact", "category": "metadata", "message": "No schema"},
    ]
    scores = rollup_connector_scores(entities, gap_findings=gaps)
    assert scores["dimensions"]["structure"] <= 45.0


def test_rollup_connector_scores():
    entities = [
        {
            "dimension_scores": {
                "completeness": 80,
                "quality": 90,
                "consistency": 85,
                "structure": 85,
                "warehouse_readiness": 88,
            }
        }
    ]
    scores = rollup_connector_scores(entities)
    assert scores["entities_assessed"] == 1
    assert scores["overall"] > 0


def test_merge_quality_profiles():
    a = analyze_data_quality(
        [{"email": "a@test.com", "name": "Alice"}, {"email": "", "name": "Bob"}],
        omit_record_indices=True,
    )
    b = analyze_data_quality(
        [{"email": "bad-email", "name": "carol"}],
        omit_record_indices=True,
    )
    merged = merge_quality_profiles(a, b)
    assert merged["total_records"] == 3
    assert "summary" in merged
    assert merged["summary"]["data_quality_score"] is not None


def test_analyze_data_quality_with_nested_values():
    """SQL JSON / array columns must not break duplicate detection."""
    report = analyze_data_quality(
        [
            {"id": 1, "payload": {"a": 1}, "email": "a@test.com"},
            {"id": 2, "payload": {"a": 1}, "email": "b@test.com"},
            {"id": 3, "payload": {"b": 2}, "tags": ["x", "y"]},
        ],
        omit_record_indices=True,
    )
    assert report["summary"].get("error") is None
    assert report["summary"]["data_quality_score"] is not None


def test_profile_rows_single():
    profile = profile_rows_single(
        "Contact",
        "Contact",
        [{"Email": "x@y.com", "FirstName": "A"}, {"Email": "", "FirstName": "B"}],
    )
    assert profile.entity_id == "Contact"
    assert profile.records_sampled == 2
    assert profile.dimension_scores.get("quality") is not None
    d = profile.to_dict()
    assert "record_indices" not in str(d.get("quality_report", {}))
