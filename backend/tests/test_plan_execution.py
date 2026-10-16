from services.planning.plan_execution import (
    apply_schema_deploy_approval,
    build_plan_execution_pending,
    is_plan_execution_resume,
)


def test_build_plan_execution_pending():
    pending = build_plan_execution_pending(
        classification={"plan_id": "p1", "source": "salesforce"},
        original_prompt="map contacts",
        resume_step_index=3,
        schema_proposal={"object_api_name": "Contact", "fields_missing_on_destination": ["Foo__c"]},
    )
    assert pending["plan_execution_pending"] is True
    assert pending["resume_step_index"] == 3
    assert pending["schema_proposal"]["object_api_name"] == "Contact"


def test_apply_schema_deploy_approval_skip_clears_missing():
    pending = {
        "classification": {"schema_gate_missing_fields": ["A__c"]},
        "resume_step_index": 2,
    }
    out = apply_schema_deploy_approval(pending, skip_deploy=True)
    assert out["schema_deploy_approved"] is True
    assert out["plan_resume_step_index"] == 2
    assert out["schema_gate_missing_fields"] == []


def test_is_plan_execution_resume():
    assert is_plan_execution_resume({"approve_schema_deploy": True})
    assert is_plan_execution_resume({"planner_pending": {"plan_execution_pending": True}})
