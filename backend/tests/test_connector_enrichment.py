"""Tests for pre-planner connector enrichment hooks (P4.8)."""
from __future__ import annotations

from services.intent.connector_enrichment import apply_pre_planner_connector_hooks


def test_same_workbook_hook_preserves_llm_classification_without_mechanical_override():
    """Pre-planner hook must not rewrite destination from session history."""
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "gsheets",
                "destination": "unknown",
                "source_name": "contact list",
                "job_id": "job-1",
            }
        }
    ]
    cls = {
        "source": "gsheets",
        "destination": "salesforce",
        "destination_name": "Contact",
        "intent": "map_data",
        "mode": "map",
        "destination_same_as_source": True,
    }
    out = apply_pre_planner_connector_hooks(
        cls,
        prompt="start the mapping to same sheet",
        conversation_history=history,
    )
    assert out["destination"] == "salesforce"
    assert out["destination_name"] == "Contact"
    assert out["destination_same_as_source"] is True
