"""Regression: do not use GSheets spreadsheet id as Salesforce token_id."""

from services.mapping_plan.service import build_mapping_plan_request
from utils.destination_connector_resolve import _looks_like_uuid


def test_gsheets_source_to_salesforce_does_not_use_spreadsheet_id_as_connector():
    sheet_id = "1xuonN1nyq62HP1tFni5foNuXnUt8ew6Lf-Ho7Ys5Ws0"
    req = build_mapping_plan_request(
        user_id="user-1",
        destination="salesforce",
        destination_name="SF 1",
        flat_data={"Name": "Test"},
        extraction_row={
            "source_type": "gsheets",
            "source_identifier": sheet_id,
            "source_name": "Service Appointments",
        },
    )
    assert req.destination_connector_id is None


def test_gsheets_source_to_gsheets_uses_spreadsheet_id():
    sheet_id = "1xuonN1nyq62HP1tFni5foNuXnUt8ew6Lf-Ho7Ys5Ws0"
    req = build_mapping_plan_request(
        user_id="user-1",
        destination="google_sheets",
        destination_name="Other workbook",
        flat_data={"Name": "Test"},
        extraction_row={
            "source_type": "google_sheets",
            "source_identifier": sheet_id,
        },
    )
    assert req.destination_spreadsheet_id == sheet_id


def test_spreadsheet_id_is_not_uuid():
    assert not _looks_like_uuid("1xuonN1nyq62HP1tFni5foNuXnUt8ew6Lf-Ho7Ys5Ws0")
    assert _looks_like_uuid("8eec776f-8caa-423e-b423-0bba9c0a3b76")
