from services.planning.plan_context import (
    apply_active_plan_thread_context,
    conversation_has_completed_pipeline,
    find_extract_step_pending,
    latest_clarification_pending_from_history,
    latest_plan_execution_state_from_history,
    merge_execution_prompt,
)


def test_merge_execution_prompt():
    assert "Test DB" in merge_execution_prompt(
        "extract from Test DB and migrate",
        "all columns",
    )


def test_latest_plan_execution_state_from_history_pending():
    history = [
        {
            "metadata": {
                "event_type": "planner_schema_deploy_required",
                "planner_pending": {
                    "classification": {
                        "_plan_completed_step_ids": ["extract", "create_sheet"],
                        "plan_resume_step_index": 2,
                        "active_job_id": "job-9",
                    }
                },
            }
        }
    ]
    state = latest_plan_execution_state_from_history(history)
    assert state["_plan_completed_step_ids"] == ["extract", "create_sheet"]
    assert state["plan_resume_step_index"] == 2
    assert state["active_job_id"] == "job-9"


def test_latest_plan_execution_state_from_checkpoint():
    history = [
        {
            "metadata": {
                "event_type": "plan_step_checkpoint",
                "detail": {
                    "_plan_completed_step_ids": ["extract"],
                    "active_job_id": "job-3",
                },
            }
        }
    ]
    state = latest_plan_execution_state_from_history(history)
    assert state["_plan_completed_step_ids"] == ["extract"]
    assert state["active_job_id"] == "job-3"


def test_apply_active_plan_thread_context_carries_endpoints():
    history = [
        {
            "metadata": {
                "event_type": "planner_plan_execution_clarification",
                "planner_pending": {
                    "extract_step_clarification": True,
                    "original_prompt": "extract from Test DB and migrate to same database",
                    "classification": {
                        "source": "sql",
                        "destination": "sql",
                        "source_name": "Test DB",
                        "destination_name": "Test DB",
                        "plan_status": "approved",
                    },
                },
            }
        }
    ]
    out = apply_active_plan_thread_context(
        {
            "source": "sql",
            "destination": "sql",
            "intent": "extract",
            "session_action": "continue_active_job",
        },
        "all columns",
        history,
    )
    assert out["source_name"] == "Test DB"
    assert out["destination_name"] == "Test DB"
    assert out.get("plan_thread_active") is True


def test_apply_active_plan_thread_context_skips_fresh_path():
    history = [
        {
            "metadata": {
                "event_type": "planner_plan_execution_clarification",
                "planner_pending": {
                    "extract_step_clarification": True,
                    "original_prompt": "extract from Test DB and migrate to same database",
                    "classification": {
                        "source": "sql",
                        "destination": "sql",
                        "source_name": "Test DB",
                        "destination_name": "Test DB",
                        "plan_status": "approved",
                    },
                },
            }
        }
    ]
    out = apply_active_plan_thread_context(
        {"source": "google_sheets", "destination": "google_sheets", "intent": "extract"},
        "extract data from contact list sheet and migrate them to same sheet",
        history,
    )
    assert out.get("plan_thread_active") is not True


def test_find_extract_step_pending():
    history = [
        {
            "metadata": {
                "event_type": "planner_plan_execution_clarification",
                "planner_pending": {
                    "extract_step_clarification": True,
                    "original_prompt": "extract from Test DB",
                },
            }
        }
    ]
    pending = find_extract_step_pending(history)
    assert pending is not None
    assert pending["original_prompt"] == "extract from Test DB"


def test_conversation_has_completed_pipeline():
    history = [
        {"metadata": {"event_type": "migration_completed", "status": "completed"}},
    ]
    assert conversation_has_completed_pipeline(history) is True


def test_latest_clarification_pending_from_history():
    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "clarifications": [{"clarification_type": "source"}],
                    "classification": {"plan_status": "awaiting_clarification"},
                },
            }
        }
    ]
    pending = latest_clarification_pending_from_history(history)
    assert pending is not None


def test_clarification_pending_found_after_prior_migration_completed():
    """New create-sheet clar after an earlier migrate in the same chat must stay resumable."""
    history = [
        {"metadata": {"event_type": "migration_completed", "status": "completed"}},
        {"type": "user", "text": "create new workbook for me"},
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "plan_status": "awaiting_clarification",
                "planner_pending": {
                    "original_prompt": "create new workbook for me",
                    "clarifications": [
                        {
                            "id": "c-headers",
                            "clarification_type": "pick_create_headers",
                            "required": True,
                            "prompt": "Which column headers?",
                        }
                    ],
                    "classification": {"plan_status": "awaiting_clarification"},
                },
            }
        },
    ]
    pending = latest_clarification_pending_from_history(history)
    assert pending is not None
    assert pending["original_prompt"] == "create new workbook for me"
    assert pending["clarifications"][0]["clarification_type"] == "pick_create_headers"


def test_clarification_pending_superseded_when_migration_after_clar():
    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "extract and migrate contacts",
                    "clarifications": [
                        {
                            "id": "c1",
                            "clarification_type": "pick_destination_entity",
                            "required": True,
                        }
                    ],
                    "classification": {"plan_status": "awaiting_clarification"},
                },
            }
        },
        {"metadata": {"event_type": "migration_completed", "status": "completed"}},
    ]
    assert latest_clarification_pending_from_history(history) is None


def test_build_open_clarification_routing_block_includes_gap_and_original():
    from services.planning.plan_context import build_open_clarification_routing_block

    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "create new workbook for me",
                    "clarifications": [
                        {
                            "clarification_type": "pick_create_headers",
                            "prompt": "Which column headers?",
                        }
                    ],
                    "classification": {"plan_status": "awaiting_clarification"},
                },
            }
        }
    ]
    block = build_open_clarification_routing_block(history)
    assert "pick_create_headers" in block
    assert "create new workbook for me" in block


def test_apply_active_plan_thread_context_ignores_completed_pipeline():
    history = [
        {
            "metadata": {
                "event_type": "migration_completed",
                "status": "completed",
            }
        },
        {
            "metadata": {
                "event_type": "planner_plan_execution_clarification",
                "planner_pending": {
                    "extract_step_clarification": True,
                    "original_prompt": "extract from Test DB",
                    "classification": {"source_name": "Test DB"},
                },
            }
        },
    ]
    out = apply_active_plan_thread_context(
        {"session_action": "continue_active_job"},
        "all columns",
        history,
    )
    assert out.get("plan_thread_active") is not True
