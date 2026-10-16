from services.planning.flow_steps import (
    detect_flow_clarifications,
    prompt_requests_load_flow,
    prompt_requests_save_flow,
)


def test_prompt_detects_run_flow():
    assert prompt_requests_load_flow("Please run my customer export flow")
    assert not prompt_requests_save_flow("run the flow now")


def test_start_the_migration_is_not_load_flow():
    migrate_cls = {"intent": "migrate_data", "mode": "migrate"}
    assert not prompt_requests_load_flow("start the migration", migrate_cls)
    assert prompt_requests_load_flow("run customer export flow")


def test_pick_flow_clarification():
    flows = [{"id": "f1", "migration_name": "Customer Export"}]
    gaps = detect_flow_clarifications({}, "run customer export flow", flows)
    assert len(gaps) == 1
    assert gaps[0].clarification_type == "pick_flow"
    assert gaps[0].options[0].id == "flow_f1"
