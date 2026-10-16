"""Structural mapping audit and post-map transform resolution."""
from utils.mapping_audit import (
    apply_enhanced_mapping_safely,
    audit_mapping_structure,
    audit_result_to_dict,
    resolve_post_map_transforms,
    sanitize_enhanced_mapping,
)
from utils.mapping_transform_context import try_programmatic_name_combine_for_mapping


def test_sf_contact_split_skips_programmatic_combine():
    """Regression: FN/LN on Contact must not combine to Account.Name."""
    flat = {
        "first_name": "Rose",
        "last_name": "Gonzalez",
        "email": "rose@edge.com",
        "company": "Edge Communications",
    }
    mapping = {
        "email": {"object": "Contact", "field": "Email"},
        "first_name": {"object": "Contact", "field": "FirstName"},
        "last_name": {"object": "Contact", "field": "LastName"},
        "company": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "Contact.AccountId",
        },
    }
    dest_shape = {
        "Contact": {
            "generic_name_columns": [],
            "has_separate_first_last": True,
            "first_name_column": "FirstName",
            "last_name_column": "LastName",
        },
        "Account": {
            "generic_name_columns": ["Name"],
            "has_separate_first_last": False,
        },
    }
    audit = audit_mapping_structure(
        mapping,
        flat,
        destination_type="salesforce",
        base_objects=["Contact"],
    )
    assert audit.has_code("BASE_NAME_SPLIT_OK")
    assert audit.allow_programmatic_transform is False
    assert audit.status == "ok"

    out = try_programmatic_name_combine_for_mapping(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=dest_shape,
        destination_type="salesforce",
        base_container="Contact",
        audit=audit,
    )
    assert out is None

    resolved = resolve_post_map_transforms(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=dest_shape,
        destination_type="salesforce",
        base_objects=["Contact"],
    )
    assert resolved["skip_transform_llm"] is True
    assert not resolved.get("enhanced_mapping")


def test_gs_unmapped_first_last_suggests_combine_on_base_sheet():
    sheets_shape = {
        "Customer Contact Details": {
            "generic_name_columns": ["Name"],
            "has_separate_first_last": False,
        }
    }
    flat = {"first_name": "Kunal", "last_name": "Fule", "email": "k@e.com"}
    mapping = {
        "email": {"column": "Email", "sheet": "Customer Contact Details"},
    }
    audit = audit_mapping_structure(
        mapping,
        flat,
        destination_type="google_sheets",
        base_container="Customer Contact Details",
        destination_name_shape=sheets_shape,
    )
    assert any(h.action == "combine" for h in audit.hints)

    resolved = resolve_post_map_transforms(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=sheets_shape,
        destination_type="google_sheets",
        base_container="Customer Contact Details",
    )
    assert resolved["enhanced_mapping"]
    assert resolved["transformation_rules"]["fields"][0]["action"] == "combine"


def test_gs_already_mapped_same_name_column_gets_combine_flags():
    sheets_shape = {
        "Customer Contact Details": {
            "generic_name_columns": ["Name"],
            "has_separate_first_last": False,
        }
    }
    flat = {"first_name": "Kunal", "last_name": "Fule", "email": "k@e.com"}
    mapping = {
        "first_name": {"column": "Name", "sheet": "Customer Contact Details"},
        "last_name": {"column": "Name", "sheet": "Customer Contact Details"},
        "email": {"column": "Email", "sheet": "Customer Contact Details"},
    }
    resolved = resolve_post_map_transforms(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=sheets_shape,
        destination_type="google_sheets",
        base_container="Customer Contact Details",
    )
    assert resolved["enhanced_mapping"]["first_name"].get("is_combined_field") is True


def test_sf_extract_api_keys_skip_transform_llm_with_snake_mapping():
    """FirstName/LastName in extract + first_name/last_name in mapping → skip transform LLM."""
    flat = {
        "FirstName": "Rose",
        "LastName": "Gonzalez",
        "Email": "rose@edge.com",
        "Phone": "+15127576000",
        "AccountName": "Edge Communications",
        "AccountPhone": "5127576000",
    }
    mapping = {
        "email": {"object": "Contact", "field": "Email"},
        "first_name": {"object": "Contact", "field": "FirstName"},
        "last_name": {"object": "Contact", "field": "LastName"},
        "phone": {"object": "Contact", "field": "Phone"},
        "company": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "Contact.AccountId",
        },
        "account_phone": {
            "object": "Account",
            "field": "Phone",
            "lookup_of": "Contact.AccountId",
        },
    }
    dest_shape = {
        "Contact": {
            "generic_name_columns": [],
            "has_separate_first_last": True,
            "first_name_column": "FirstName",
            "last_name_column": "LastName",
        },
        "Account": {
            "generic_name_columns": ["Name"],
            "has_separate_first_last": False,
        },
    }
    audit = audit_mapping_structure(
        mapping,
        flat,
        destination_type="salesforce",
        base_objects=["Contact"],
    )
    assert audit.has_code("BASE_NAME_SPLIT_OK")

    resolved = resolve_post_map_transforms(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=dest_shape,
        destination_type="salesforce",
        base_objects=["Contact"],
    )
    assert resolved["skip_transform_llm"] is True
    assert not resolved.get("enhanced_mapping")


def test_sanitize_rejects_account_name_split_to_accountid():
    flat = {
        "AccountName": "Edge Communications",
        "FirstName": "Rose",
    }
    mapping = {
        "AccountName": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "Contact.AccountId",
        },
    }
    bad_enhanced = {
        "AccountName": [
            {"object": "Contact", "field": "AccountId", "lookup_of": "Contact.AccountId"},
        ],
    }
    cleaned = sanitize_enhanced_mapping(mapping, bad_enhanced, flat)
    assert cleaned == {}

    updated, skipped = apply_enhanced_mapping_safely(mapping, bad_enhanced, flat)
    assert isinstance(updated["AccountName"], dict)
    assert updated["AccountName"]["field"] == "Name"
    assert not isinstance(updated["AccountName"], list)


def test_compound_name_on_lastname_triggers_programmatic_split():
    """Regression: data-ops merged name must split for Contact FirstName + LastName."""
    flat = {"name": "Sai Reddy", "email": "sai.archian@gmail.com"}
    mapping = {
        "name": {
            "object": "Contact",
            "field": "LastName",
            "is_required": True,
            "is_duplicate_check": True,
        },
        "email": {"object": "Contact", "field": "Email"},
    }
    dest_shape = {
        "Contact": {
            "generic_name_columns": [],
            "has_separate_first_last": True,
            "first_name_column": "FirstName",
            "last_name_column": "LastName",
        },
    }
    audit = audit_mapping_structure(
        mapping,
        flat,
        destination_type="salesforce",
        base_objects=["Contact"],
        destination_name_shape=dest_shape,
    )
    assert audit.has_code("BASE_NAME_SPLIT_NEEDED")
    assert any(h.action == "split" for h in audit.hints)

    resolved = resolve_post_map_transforms(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=dest_shape,
        destination_type="salesforce",
        base_objects=["Contact"],
    )
    assert resolved.get("enhanced_mapping")
    assert resolved["transformation_rules"]["fields"][0]["action"] == "split_2"
    name_entries = resolved["enhanced_mapping"]["name"]
    assert isinstance(name_entries, list)
    assert len(name_entries) == 2
    assert {e["field"] for e in name_entries} == {"FirstName", "LastName"}
    assert resolved["skip_transform_llm"] is True


def test_dest_shaped_name_email_still_runs_split_not_blank_skip():
    """Generic name+email source shape must not skip transform when split is required."""
    flat = {"name": "Jane Doe", "email": "jane@example.com"}
    mapping = {
        "name": {"object": "Contact", "field": "LastName"},
        "email": {"object": "Contact", "field": "Email"},
    }
    dest_shape = {
        "Contact": {
            "has_separate_first_last": True,
            "first_name_column": "FirstName",
            "last_name_column": "LastName",
            "generic_name_columns": [],
        },
    }
    resolved = resolve_post_map_transforms(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=dest_shape,
        destination_type="salesforce",
        base_objects=["Contact"],
    )
    assert resolved.get("enhanced_mapping")
    assert resolved["transformation_rules"]["fields"][0]["action"] == "split_2"


def test_audit_result_includes_mapping_score():
    flat = {"first_name": "A", "last_name": "B"}
    mapping = {
        "first_name": {"object": "Contact", "field": "FirstName"},
        "last_name": {"object": "Contact", "field": "LastName"},
    }
    audit = audit_mapping_structure(
        mapping, flat, destination_type="salesforce", base_objects=["Contact"]
    )
    payload = audit_result_to_dict(audit)
    assert "mapping_score" in payload
    assert payload["mapping_score"] >= 0.9
