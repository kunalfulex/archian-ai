from services.mapping_plan.service import build_mapping_plan_request
from services.mapping_plan.target_resolution import MappingTargetResolution


def test_build_request_carries_target_resolution_fields():
    request = build_mapping_plan_request(
        user_id="u1",
        destination="salesforce",
        destination_name="SF 1",
        flat_data={"Name": "A"},
        extraction_row={"source_type": "google_sheets", "source_identifier": "sheet-id"},
        mapping_instruction="map to Service Appointment object",
        target_resolution=MappingTargetResolution(
            connector_type="salesforce",
            resolved_target="ServiceAppointment",
            target_object_api_name="ServiceAppointment",
            resolution_source="prompt_explicit",
            confidence=0.95,
        ),
    )
    assert request.target_object_api_name == "ServiceAppointment"
    assert request.target_resolution_source == "prompt_explicit"
