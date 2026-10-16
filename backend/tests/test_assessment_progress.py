"""Unit tests for assessment progress helpers."""
from services.data_assessment.progress import (
    PHASE_PROFILING,
    PHASE_SYNTHESIZING,
    build_progress_payload,
    entity_work_percent,
    sub_phase_label,
)


def test_entity_work_percent_in_entity_slot():
    assert entity_work_percent(2, 10, in_entity=True) > entity_work_percent(2, 10, in_entity=False)


def test_build_progress_payload_profiling_message():
    payload = build_progress_payload(
        sub_phase=PHASE_PROFILING,
        completed_entities=1,
        total_entities=4,
        current_entity="customers",
        in_entity=True,
    )
    assert payload["percent"] > 0
    assert "customers" in payload["message"]
    assert payload["sub_phase"] == PHASE_PROFILING


def test_sub_phase_label():
    assert "Profiling" in sub_phase_label(PHASE_PROFILING)
    assert sub_phase_label(PHASE_SYNTHESIZING)
