"""Tests for MissingPlanScopeError and overlay guards."""
import pytest

from services.intent.migration_intent_view import overlay_intent_state_on_classification
from services.planning.goal_parser import MissingPlanScopeError
from services.planning.plan_builder import build_plan_steps


def test_build_plan_steps_requires_plan_scope():
    with pytest.raises(MissingPlanScopeError):
        build_plan_steps(
            {
                "source": "salesforce",
                "source_name": "SF 1",
                "intent": "extract",
                "mode": "extract",
            },
            prompt="extract contacts",
        )


def test_overlay_skips_mode_when_plan_scope_present():
    cls = {
        "_plan_scope": {"operations": ["extract"], "data_operations": ["filter"]},
        "mode": "extract",
        "intent_state": {
            "mode": "data_ops",
            "operations": ["filter"],
            "turn_type": "data_ops",
        },
    }
    out = overlay_intent_state_on_classification(cls)
    assert out["mode"] == "extract"
    assert out.get("_plan_scope")["operations"] == ["extract"]
