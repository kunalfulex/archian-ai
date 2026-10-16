"""Map continuation must reuse prior SQL extraction, not start GS extract."""

from services.planning.continuation_policy import apply_extraction_session_continuation
from services.planning.plan_builder import build_plan_steps
from services.planning.resource_catalog import GoogleSheetRef, ResourceCatalog, SqlConnectionRef
from services.planning.schemas import ExecutionPlan


def _sql_gs_catalog() -> ResourceCatalog:
    cat = ResourceCatalog(has_google_sheets=True)
    cat.sql_connections.append(SqlConnectionRef(name="Test DB", connector_id="sql-1"))
    cat.google_sheet_rows.append(
        GoogleSheetRef(
            spreadsheet_name="Contact List Sheet",
            sheet_name="Sheet1",
            spreadsheet_id="gs-1",
        )
    )
    return cat


SQL_HISTORY = [
    {
        "metadata": {
            "event_type": "extraction_completed",
            "job_id": "cd685e21-sql-job",
            "source_type": "sql",
            "source_name": "Test DB",
            "destination": "unknown",
            "record_count": 4,
        }
    }
]

MAP_PROMPT = "No map the extarct data to contact list google sheet"


def test_map_continuation_does_not_drop_sql_job_on_typo_map_prompt():
    cat = _sql_gs_catalog()
    out = apply_extraction_session_continuation(
        {
            "intent": "map_data",
            "mode": "map",
            "source": "google_sheets",
            "source_name": "contact list",
            "destination": "google_sheets",
            "destination_name": "contact list",
        },
        extraction_meta=SQL_HISTORY[0]["metadata"],
        prompt=MAP_PROMPT,
        catalog=cat,
        conversation_history=SQL_HISTORY,
    )
    assert out["active_job_id"] == "cd685e21-sql-job"
    assert out["source"] == "sql"
    assert out["source_name"] == "Test DB"
    assert out["destination"] == "google_sheets"
    assert "contact list" in (out.get("destination_name") or "").lower()


def test_map_migrate_plan_skips_extract_step_with_active_job():
    cat = _sql_gs_catalog()
    classification = {
        "intent": "map_data",
        "mode": "map",
        "source": "sql",
        "source_name": "Test DB",
        "destination": "google_sheets",
        "destination_name": "Contact List Sheet",
        "active_job_id": "cd685e21-sql-job",
        "operations": ["map"],
        "_plan_scope": {
            "operations": ["map"],
            "needs_destination": True,
        },
    }
    plan = build_plan_steps(
        classification,
        prompt=MAP_PROMPT,
        plan=ExecutionPlan(status="draft"),
    )
    tool_ids = [s.parameters.get("tool_id") for s in plan.steps]
    assert "extract_google_sheets" not in tool_ids
    assert "map_fields" in tool_ids
