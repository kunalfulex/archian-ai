from services.planning.clarification_pipeline import enforce_single_blocking_clarification
from services.planning.schemas import Clarification, ExecutionPlan


def test_enforce_single_blocking_keeps_first_open_clarification():
    plan = ExecutionPlan(
        plan_id="p1",
        clarifications=[
            Clarification(
                id="a",
                clarification_type="pick_destination_entity",
                prompt="Dest?",
                required=True,
            ),
            Clarification(
                id="b",
                clarification_type="pick_extract_fields",
                prompt="Fields?",
                required=True,
            ),
        ],
    )
    out = enforce_single_blocking_clarification(plan)
    blocking = out.blocking_clarifications()
    assert len(blocking) == 1
    assert blocking[0].clarification_type == "pick_destination_entity"
