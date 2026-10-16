"""Planning checkpoint persist/hydrate across metadata clarification resumes."""
from __future__ import annotations

from services.planning.clarification_apply import (
    apply_clarification_selections,
    build_planner_pending_payload,
)
from services.planning.planning_checkpoint import (
    hydrate_classification_from_checkpoint,
    persist_planning_checkpoint,
    plan_scope_is_resolved,
    resolved_goal_for_session,
)
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
)
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef


def _migrate_goal():
    return {
        "operations": ["extract", "map", "migrate"],
        "needs_source": True,
        "needs_destination": True,
        "source_phrase": "employee list",
        "destination_phrase": "contact list",
    }


def test_plan_scope_is_resolved():
    assert plan_scope_is_resolved({}) is False
    assert plan_scope_is_resolved({"needs_source": True}) is True
    assert plan_scope_is_resolved({"operations": ["extract"]}) is True


def test_persist_and_hydrate_restores_thin_pending_classification():
    catalog = ResourceCatalog(
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="gs-1", name="xTag"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_id="sid-src",
                spreadsheet_name="Employee list",
                sheet_name="Sheet1",
                google_sheets_connection_id="gs-1",
            ),
            GoogleSheetRef(
                spreadsheet_id="sid-dest",
                spreadsheet_name="contact list",
                sheet_name="Sheet1",
                google_sheets_connection_id="gs-1",
            ),
        ],
    )
    goal = _migrate_goal()
    classification = {
        "source": "google_sheets",
        "source_name": "Employee list",
        "source_connector_id": "gs-1",
        "source_spreadsheet_id": "sid-src",
        "source_object_api_name": "sid-src::Sheet1",
        "destination": "google_sheets",
        "destination_name": "contact list",
        "destination_connector_id": "gs-1",
        "destination_spreadsheet_id": "sid-dest",
        "destination_object_api_name": "sid-dest::Sheet1",
        "intent": "migrate_data",
        "_source_endpoint_clarified": True,
        "_destination_endpoint_clarified": True,
        "_plan_scope": goal,
        "operations": goal["operations"],
        "_planning_catalog": catalog,
    }
    plan = ExecutionPlan(plan_id="plan-1", status="awaiting_clarification")
    classification, plan = persist_planning_checkpoint(classification, plan, parsed_goal=goal)

    thin = {
        "source_name": "Employee list",
        "destination_name": "contact list",
        "intent": "migrate_data",
    }
    hydrated = hydrate_classification_from_checkpoint(thin, plan)
    assert hydrated.get("source_connector_id") == "gs-1"
    assert hydrated.get("destination_connector_id") == "gs-1"
    assert hydrated.get("operations") == goal["operations"]
    assert plan_scope_is_resolved(resolved_goal_for_session(hydrated, plan=plan))


def test_persist_and_hydrate_restores_pending_file_ref():
    """Regression: Run plan after SF org pick must still find staged chat file."""
    file_ref = {
        "file_key": "pending-upload/u/c/uuid_test_contacts_5000.csv",
        "filename": "test_contacts_5000.csv",
        "size_bytes": 12345,
    }
    classification = {
        "source": "file",
        "source_name": "test_contacts_5000.csv",
        "_file_upload_pinned": True,
        "_pending_file_ref": file_ref,
        "destination": "salesforce",
        "destination_name": "Sales CRM",
        "destination_connector_id": "tok-sf",
        "_destination_endpoint_clarified": True,
        "operations": ["extract", "map", "migrate"],
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "needs_destination": True,
        },
    }
    plan = ExecutionPlan(plan_id="plan-file-1", status="awaiting_clarification")
    classification, plan = persist_planning_checkpoint(classification, plan)
    ck = (plan.metadata or {}).get("classification_checkpoint") or {}
    assert ck.get("_pending_file_ref", {}).get("file_key") == file_ref["file_key"]
    assert ck.get("_file_upload_pinned") is True

    thin = {
        "source": "file",
        "source_name": "test_contacts_5000.csv",
        "destination": "salesforce",
        "destination_name": "Sales CRM",
    }
    hydrated = hydrate_classification_from_checkpoint(thin, plan)
    assert hydrated.get("_pending_file_ref", {}).get("file_key") == file_ref["file_key"]
    assert hydrated.get("_file_upload_pinned") is True


def test_build_planner_pending_includes_goal_and_checkpoint():
    goal = _migrate_goal()
    classification = {
        "source": "google_sheets",
        "source_name": "Employee list",
        "source_connector_id": "gs-1",
        "destination": "google_sheets",
        "destination_name": "contact list",
        "destination_connector_id": "gs-1",
        "intent": "migrate_data",
        "_plan_scope": goal,
        "operations": goal["operations"],
        "needs_clarification": True,
        "execution_plan": ExecutionPlan(
            plan_id="plan-2",
            status="awaiting_clarification",
            clarifications=[
                Clarification(
                    id="wb",
                    clarification_type="pick_workbook",
                    prompt="Which workbook?",
                    options=[
                        ClarificationOption(
                            id="wb_contact",
                            label="contact list",
                            resource_ref=ResourceRef(
                                connector_type="google_sheets",
                                display_name="contact list",
                                spreadsheet_id="sid-dest",
                                sheet_name="Sheet1",
                                connector_id="gs-1",
                            ),
                        )
                    ],
                )
            ],
        ).model_dump(mode="json"),
    }
    pending = build_planner_pending_payload(
        classification,
        prompt="extract employee list and migrate to contact list",
        conversation_id="conv-1",
    )
    plan_meta = pending["execution_plan"].get("metadata") or {}
    assert plan_meta.get("goal", {}).get("operations") == goal["operations"]
    assert pending["classification"].get("source_connector_id") == "gs-1"
    ck = plan_meta.get("classification_checkpoint") or {}
    assert ck.get("destination_connector_id") == "gs-1"


def test_pick_workbook_apply_uses_destination_entity_key_format():
    goal = _migrate_goal()
    plan = ExecutionPlan(
        plan_id="plan-3",
        status="awaiting_clarification",
        metadata={"goal": goal},
        clarifications=[
            Clarification(
                id="wb",
                clarification_type="pick_workbook",
                prompt="Which workbook?",
                options=[
                    ClarificationOption(
                        id="wb_contact",
                        label="contact list",
                        resource_ref=ResourceRef(
                            connector_type="google_sheets",
                            display_name="contact list",
                            spreadsheet_id="sid-dest",
                            sheet_name="Sheet1",
                            connector_id="gs-1",
                        ),
                    )
                ],
            )
        ],
    )
    base = hydrate_classification_from_checkpoint(
        {
            "source_name": "Employee list",
            "destination_name": "contact list",
            "intent": "migrate_data",
        },
        plan,
    )
    out, updated = apply_clarification_selections(
        base,
        plan,
        [{"clarification_id": "wb", "option_id": "wb_contact"}],
    )
    assert out.get("destination_object_api_name") == "sid-dest::Sheet1"
    assert out.get("destination_connector_id") == "gs-1"
    assert out.get("planner_workbook_picker_done") is True
    meta_goal = (updated.metadata or {}).get("goal") or {}
    assert meta_goal.get("operations") == goal["operations"]


def test_enrich_google_sheets_bind_pins_marks_workbook_picker_done():
    from services.planning.destination_catalog_validate import needs_google_sheets_destination_clarification
    from services.planning.planning_checkpoint import enrich_google_sheets_bind_pins

    catalog = ResourceCatalog(
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="gs-1", name="xTag"),
            GoogleSheetsConnectionRef(connection_id="gs-2", name="Archian AI"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_id="sid-dest",
                spreadsheet_name="contact list",
                sheet_name="Sheet1",
                google_sheets_connection_id="gs-1",
            ),
        ],
    )
    goal = _migrate_goal()
    classification = {
        "source": "google_sheets",
        "destination": "google_sheets",
        "destination_name": "contact list",
        "destination_connector_id": "gs-1",
        "destination_object_api_name": "sid-dest::Sheet1",
        "_destination_endpoint_clarified": True,
        "_destination_connector_validated": True,
        "_plan_scope": goal,
    }
    out = enrich_google_sheets_bind_pins(
        classification,
        catalog=catalog,
        parsed_goal=goal,
    )
    assert out.get("google_sheets_connection_id") == "gs-1"
    assert out.get("destination_spreadsheet_id") == "sid-dest"
    assert out.get("planner_workbook_picker_done") is True
    assert not needs_google_sheets_destination_clarification(out, catalog, parsed_goal=goal)
