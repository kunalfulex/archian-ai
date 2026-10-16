from agent.stream_utils import is_raw_handler_clarification_sse_line


def test_is_raw_handler_clarification_sse_line():
    raw = 'data: {"status": "clarification", "message": "Which columns?", "job_id": "j1"}\n\n'
    assert is_raw_handler_clarification_sse_line(raw) is True

    planner = (
        'data: {"event_type": "planner_plan_execution_clarification", '
        '"status": "clarification", "message": "Which columns?"}\n\n'
    )
    assert is_raw_handler_clarification_sse_line(planner) is False
