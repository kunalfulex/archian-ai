from services.planning.flow_steps import (
    apply_mapping_transformation_to_config,
    flow_field_mappings_to_config,
)


def test_flow_field_mappings_salesforce():
    rows = [
        {
            "extracted_field_name": "Email",
            "salesforce_object": "Contact",
            "salesforce_field": "Email",
        },
    ]
    cfg = flow_field_mappings_to_config(rows, destination="salesforce")
    assert cfg["Email"]["object"] == "Contact"
    assert cfg["Email"]["field"] == "Email"


def test_flow_field_mappings_google_sheets():
    rows = [
        {
            "extracted_field_name": "Email",
            "salesforce_object": "Sheet1",
            "salesforce_field": "Email",
        },
    ]
    cfg = flow_field_mappings_to_config(rows, destination="google_sheets")
    assert cfg["Email"]["sheet"] == "Sheet1"
    assert cfg["Email"]["column"] == "Email"


def test_flow_field_mappings_combine_restores_flags():
    rows = [
        {
            "extracted_field_name": "FirstName",
            "salesforce_object": "Sheet1",
            "salesforce_field": "Name",
        },
        {
            "extracted_field_name": "LastName",
            "salesforce_object": "Sheet1",
            "salesforce_field": "Name",
        },
        {
            "extracted_field_name": "Name",
            "salesforce_object": "Sheet1",
            "salesforce_field": "Name",
        },
    ]
    mt = {
        "fields": [
            {
                "action": "combine",
                "source_fields": ["FirstName", "LastName"],
                "output_field": "Name",
                "output_sheet": "Sheet1",
                "separator": " ",
            }
        ]
    }
    cfg = flow_field_mappings_to_config(rows, destination="google_sheets", mapping_transformation=mt)
    assert cfg["FirstName"]["is_combined_field"] is True
    assert cfg["LastName"]["is_combined_field"] is True
    assert cfg["FirstName"]["combine_order"] == 0
    assert cfg["LastName"]["combine_order"] == 1
    assert "Name" not in cfg


def test_apply_mapping_transformation_to_config_orphan_removal():
    mapping = {
        "first_name": {"object": "Contact", "field": "Name"},
        "last_name": {"object": "Contact", "field": "Name"},
        "Name": {"object": "Contact", "field": "Name"},
    }
    mt = {
        "fields": [
            {
                "action": "combine",
                "source_fields": ["first_name", "last_name"],
                "output_field": "Name",
                "output_object": "Contact",
            }
        ]
    }
    out = apply_mapping_transformation_to_config(mapping, mt, destination="salesforce")
    assert out["first_name"]["is_combined_field"] is True
    assert "Name" not in out
