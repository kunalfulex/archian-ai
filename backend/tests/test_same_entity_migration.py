from services.planning.connector_guards import classification_destination_endpoint_resolved
from services.planning.continuation_policy import apply_fresh_extract_goal_normalization
from services.planning.entity_resolver import apply_plan_contract_to_classification
from services.planning.schemas import PlanContract, PlanEntityRef


ARCHIAN_PROMPT = (
    "pull data from Archian Data sheet and move them to same sheet"
)


def test_fresh_extract_normalization_skipped_for_compound_same_sheet():
    cls = {
        "session_action": "fresh_extract",
        "source": "google_sheets",
        "source_name": "Archian Data",
        "destination": "google_sheets",
        "destination_name": "Archian Data",
        "destination_same_as_source": True,
        "intent": "extract",
        "mode": "extract",
        "operations": ["extract", "map", "migrate"],
    }
    goal = {
        "operations": ["extract", "map", "migrate"],
        "intent": "extract",
        "mode": "extract",
    }
    out, parsed = apply_fresh_extract_goal_normalization(cls, goal, ARCHIAN_PROMPT)
    assert out.get("destination") == "google_sheets"
    assert out.get("destination_name") == "Archian Data"
    assert "migrate" in (parsed.get("operations") or out.get("operations") or [])



def test_plan_contract_promotes_destination_for_same_entity():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="google_sheets",
            entity_key="sheet::Archian Data",
            display_name="Archian Data",
            sheet_name="Archian Data",
            spreadsheet_id="abc123",
            connector_id="abc123",
            resolution_source="catalog_label",
        ),
        destination=PlanEntityRef(
            connector_type="google_sheets",
            entity_key="sheet::Archian Data",
            display_name="Archian Data",
            sheet_name="Archian Data",
            spreadsheet_id="abc123",
            connector_id="abc123",
            resolution_source="destination_same_as_source",
        ),
    )
    out = apply_plan_contract_to_classification({}, contract)
    assert out.get("destination_same_as_source") is True
    assert out.get("destination") == "google_sheets"
    assert out.get("destination_name") == "Archian Data"
    assert out.get("destination_sheet_name") == "Archian Data"


def test_destination_resolved_when_same_entity_and_source_resolved():
    cls = {
        "intent": "migrate_data",
        "mode": "migrate",
        "operations": ["extract", "map", "migrate"],
        "source": "google_sheets",
        "source_name": "Archian Data",
        "destination": "google_sheets",
        "destination_same_as_source": True,
    }
    assert classification_destination_endpoint_resolved(cls)
