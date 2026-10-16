"""Planning stage pipeline learning chunks."""

from services.pipeline_learning.chunk_builder import (
    build_plan_snapshot_text,
    build_pipeline_recipe_text,
)


def test_build_plan_snapshot_text():
    text = build_plan_snapshot_text(
        prompt="Extract from Service Appointments and migrate to SF 1",
        pipeline_label="extract → map → migrate",
        pipeline_steps=["extract", "map", "migrate"],
        step_labels=["Extract", "Transform", "Map", "Migrate"],
        source_type="google_sheets",
        destination_type="salesforce",
        source_name="Service Appointments",
        destination_name="SF 1",
        approved=True,
    )
    assert "pipeline_label:extract → map → migrate" in text
    assert "pipeline_steps:extract → map → migrate" in text
    assert "approved:true" in text
    assert "SF 1" in text


def test_build_plan_snapshot_rejected():
    text = build_plan_snapshot_text(
        prompt="bad plan prompt",
        pipeline_label="extract → export → create",
        rejected=True,
    )
    assert "rejected:true" in text


def test_build_pipeline_recipe_text():
    text = build_pipeline_recipe_text(
        prompt="migrate contacts",
        pipeline_label="extract → map → migrate",
        success_rate=0.95,
        destination_type="salesforce",
        destination_name="SF 1",
    )
    assert "outcome_success_rate:0.950" in text
    assert "approved:true" in text
