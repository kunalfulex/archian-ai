from services.planning.plan_approve import approve_execution_plan
from services.planning.schemas import ExecutionPlan, PlanStep, StepType


def test_approve_restores_pending_file_ref_from_checkpoint():
    """Thin approve pending still recovers staged file key for extract_file."""
    file_ref = {
        "file_key": "pending-upload/u/c/uuid_contacts.csv",
        "filename": "test_contacts_5000.csv",
        "size_bytes": 99,
    }
    plan = ExecutionPlan(
        plan_id="plan-file-approve",
        status="awaiting_approval",
        metadata={
            "goal": {"operations": ["extract", "map", "migrate"]},
            "classification_checkpoint": {
                "source": "file",
                "source_name": "test_contacts_5000.csv",
                "_file_upload_pinned": True,
                "_pending_file_ref": file_ref,
                "destination": "salesforce",
                "destination_name": "Sales CRM",
                "destination_connector_id": "tok-sf",
            },
        },
        steps=[
            PlanStep(
                step_type=StepType.EXTRACT,
                label="Extract file",
                parameters={"tool_id": "extract_file"},
            )
        ],
    )
    pending = {
        "original_prompt": "please clean the data and migrate them to salesforce",
        "classification": {
            "source": "file",
            "source_name": "test_contacts_5000.csv",
            "destination": "salesforce",
            "destination_name": "Sales CRM",
            "operations": ["extract", "map", "migrate"],
        },
        "execution_plan": plan.model_dump(mode="json"),
    }
    out = approve_execution_plan(pending)
    assert out["plan_status"] == "approved"
    assert out.get("_pending_file_ref", {}).get("file_key") == file_ref["file_key"]
    assert out.get("_file_upload_pinned") is True
    assert out.get("destination_connector_id") == "tok-sf"


def test_approve_execution_plan_preserves_plan_scope():
    pending = {
        "original_prompt": (
            "Extract 100 records from Service Appointments and migrate to SF 1 Service Appointment"
        ),
        "classification": {
            "source": "google_sheets",
            "destination": "salesforce",
            "source_name": "Service Appointments",
            "destination_name": "SF 1",
            "operations": ["extract", "map", "migrate"],
            "intent": "extract",
            "mode": "extract",
        },
        "execution_plan": ExecutionPlan(
            plan_id="plan-1",
            status="awaiting_approval",
            metadata={
                "pipeline_label": "extract → map → migrate",
                "pipeline_steps": ["extract", "map", "migrate"],
                "goal": {
                    "operations": ["extract", "map", "migrate"],
                    "data_operations": ["transform"],
                },
            },
            steps=[
                PlanStep(
                    step_type=StepType.EXTRACT,
                    label="Extract",
                    parameters={"tool_id": "extract_google_sheets"},
                )
            ],
        ).model_dump(mode="json"),
    }
    out = approve_execution_plan(pending)
    assert out["plan_status"] == "approved"
    assert out.get("operations") == ["extract", "map", "migrate"]


def test_approved_plan_must_not_reenter_approval_gate():
    """Regression: Run plan sets approved; emit must not treat it as draft."""
    approved = approve_execution_plan(
        {
            "original_prompt": "deploy user data to salesforce",
            "classification": {
                "source": "sql",
                "destination": "salesforce",
                "plan_status": "awaiting_approval",
            },
            "execution_plan": ExecutionPlan(
                plan_id="plan-run",
                status="awaiting_approval",
                steps=[
                    PlanStep(step_type=StepType.EXTRACT, label="Extract"),
                    PlanStep(step_type=StepType.MAP, label="Map"),
                    PlanStep(step_type=StepType.MIGRATE, label="Migrate"),
                ],
            ).model_dump(mode="json"),
        }
    )
    assert approved["plan_status"] == "approved"
    # emit_plan_or_route only normalizes draft → awaiting_approval
    assert (approved.get("plan_status") or "draft") != "draft"
