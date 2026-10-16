"""Regression: sequential post-extract turns still route through PLAN_SCOPE."""
from services.planning.goal_parser import resolve_data_operations


def test_follow_up_dedupe_from_plan_scope():
    c = {
        "intent": "dedupe",
        "mode": "data_ops",
        "active_job_id": "job-1",
        "_plan_scope": {"data_operations": ["dedupe"]},
    }
    ops = resolve_data_operations(c)
    assert ops == ["dedupe"]


def test_compound_fresh_extract_data_ops_from_plan_scope():
    c = {
        "intent": "extract",
        "mode": "extract",
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "data_operations": ["dedupe", "filter"],
        },
    }
    ops = resolve_data_operations(c)
    assert "dedupe" in ops
    assert "filter" in ops
