from agent.plan import ExecutionPlan, PlanStep
from agent.runtime import AgentRuntime
from agent.session import AgentSession


def _session(continuation_steps: list[str], active_job_id: str | None) -> AgentSession:
    return AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="map",
        active_job_id=active_job_id,
        classification_snapshot={
            "active_job_id": active_job_id,
            "plan_route": "continuation_path",
            "_continuation_steps": continuation_steps,
        },
    )


def test_sanitize_plan_removes_extract_for_continuation():
    runtime = AgentRuntime()
    session = _session(["map", "migrate"], "job-1")
    plan = ExecutionPlan(
        mode="map",
        steps=[
            PlanStep(tool_id="extract_google_sheets", arguments={}),
            PlanStep(tool_id="map_fields", arguments={}),
            PlanStep(tool_id="start_migration", arguments={}),
        ],
        reasoning="plan_scoped:map → migrate",
    )
    out = runtime._sanitize_plan_for_continuation_tokens(plan, session)
    assert [s.tool_id for s in out.steps] == ["map_fields", "start_migration"]


def test_continuation_requires_job_for_map_migrate_steps():
    runtime = AgentRuntime()
    session = _session(["map", "migrate"], None)
    plan = ExecutionPlan(
        mode="map",
        steps=[PlanStep(tool_id="map_fields", arguments={})],
        reasoning="plan_scoped:map → migrate",
    )
    assert runtime._continuation_requires_job(plan, session) is True


def test_general_action_does_not_require_job():
    runtime = AgentRuntime()
    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="general",
        active_job_id=None,
        classification_snapshot={"plan_route": "fresh_path"},
    )
    plan = ExecutionPlan(
        mode="general",
        steps=[PlanStep(tool_id="general_ai_response", arguments={})],
        reasoning="general",
    )
    assert runtime._continuation_requires_job(plan, session) is False
