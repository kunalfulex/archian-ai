from services.planning.schema_gates import (
    extraction_or_mapping_has_custom_fields,
    plan_needs_schema_gate,
)


def test_extraction_or_mapping_has_custom_fields_from_schema():
    assert extraction_or_mapping_has_custom_fields(
        {"source_schema": ["Email", "Foo__c"]}
    )
    assert not extraction_or_mapping_has_custom_fields(
        {"source_schema": ["Email", "FirstName"]}
    )


def test_extraction_or_mapping_has_custom_fields_from_mapping():
    assert extraction_or_mapping_has_custom_fields(
        {
            "mapping": {
                "score": {
                    "field": "Custom_Score__c",
                    "is_custom": True,
                    "object": "Contact",
                }
            }
        }
    )


def test_plan_needs_schema_gate_false_for_standard_only(monkeypatch):
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "SF 1",
        "destination_name": "SF 2",
        "intent": "map_data",
        "source_schema": ["Email", "LastName"],
    }
    goal = {"intent": "map_data"}
    assert plan_needs_schema_gate(classification, goal) is False


def test_plan_needs_schema_gate_false_for_create_only():
    classification = {
        "source": "salesforce",
        "destination": "google_sheets",
        "source_schema": ["Custom__c"],
        "_pipeline_tokens": ["create"],
    }
    goal = {"operations": ["create"]}
    assert plan_needs_schema_gate(classification, goal) is False


def test_plan_needs_schema_gate_true_for_sf_cross_org_map():
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "Prod",
        "destination_name": "Sandbox",
        "source_schema": ["Custom__c"],
        "_pipeline_tokens": ["extract", "map", "migrate"],
    }
    goal = {"operations": ["extract", "map", "migrate"], "intent": "map_data"}
    assert plan_needs_schema_gate(classification, goal) is True
