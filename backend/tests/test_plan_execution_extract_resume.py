from services.planning.plan_execution import apply_extract_step_resume, build_extract_step_pending


def test_build_and_resume_extract_step_pending():
    classification = {
        "source": "sql",
        "destination": "sql",
        "source_name": "Test DB",
        "destination_name": "Test DB",
        "plan_status": "approved",
        "plan_id": "plan-1",
    }
    pending = build_extract_step_pending(
        classification=classification,
        original_prompt="extract from Test DB and migrate to same database",
        resume_step_index=0,
        clarification_message="Which columns?",
        conversation_id="conv-1",
    )
    assert pending["extract_step_clarification"] is True
    assert pending["plan_execution_pending"] is True

    resumed = apply_extract_step_resume(pending, "all columns")
    assert resumed["plan_status"] == "approved"
    assert resumed["plan_resume_step_index"] == 0
    assert "all columns" in resumed["_merged_execution_prompt"]
    assert "Test DB" in resumed["_merged_execution_prompt"]
