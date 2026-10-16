"""Pipeline steps after data ops — filter vs transform."""
from services.planning.pipeline_step_resolver import PlanContext, resolve_pipeline_steps


def test_filter_and_migrate_skips_map_when_mapping_ready():
    goal = {
        "operations": ["migrate"],
        "data_operations": ["filter"],
    }
    ctx = PlanContext(
        active_job_id="job-1",
        mapping_ready=True,
        is_continuation=True,
    )
    steps = resolve_pipeline_steps(goal, ctx)
    assert steps == ["data_ops", "migrate"]


def test_transform_and_migrate_inserts_map_when_migrate_in_scope():
    goal = {
        "operations": ["migrate"],
        "data_operations": ["transform"],
    }
    ctx = PlanContext(
        active_job_id="job-1",
        mapping_ready=False,
        is_continuation=True,
    )
    steps = resolve_pipeline_steps(goal, ctx)
    assert steps == ["data_ops", "map", "migrate"]
