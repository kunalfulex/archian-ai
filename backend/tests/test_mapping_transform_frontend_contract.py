"""
Frontend → backend contract tests for mapping combine/split/undo.

Payload shapes mirror edisonai-frontend/src/utils/mappingTransformEdit.js output
sent to POST /transform-data and migration runners.
"""
import asyncio

import pytest

from services.data_transformation_service import DataTransformationService


def _run(coro):
    return asyncio.run(coro)


SAMPLE_RECORDS = [
    {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
    },
    {
        "first_name": "Alan",
        "last_name": "Turing",
        "full_name": "Alan Turing",
        "email": "alan@example.com",
    },
]


def test_combine_payload_produces_joined_destination_column():
    """After frontend combineSourceFields → transform-data."""
    mapping = {
        "first_name": {
            "object": "Contact",
            "field": "Name",
            "is_combined_field": True,
            "combine_group": "contact.name",
            "combine_order": 0,
        },
        "last_name": {
            "object": "Contact",
            "field": "Name",
            "is_combined_field": True,
            "combine_group": "contact.name",
            "combine_order": 1,
        },
        "email": {"object": "Contact", "field": "Email"},
    }
    transformation_rules = {
        "fields": [
            {
                "source_fields": ["first_name", "last_name"],
                "action": "combine",
                "separator": " ",
                "output_field": "Name",
                "output_object": "Contact",
            }
        ]
    }

    transformed, meta, _ = _run(
        DataTransformationService.transform_data_for_migration(
            data=SAMPLE_RECORDS,
            mapping=mapping,
            transformation_rules=transformation_rules,
            user_id=None,
            destination="salesforce",
        )
    )

    assert transformed[0]["Name"] == "Ada Lovelace"
    assert transformed[1]["Name"] == "Alan Turing"
    assert "first_name" not in transformed[0]
    assert "last_name" not in transformed[0]
    combine_applied = [
        t for t in meta.get("transformations_applied", []) if t.get("transformation") == "combine"
    ]
    assert len(combine_applied) >= 1


def test_split_payload_produces_two_destination_columns():
    """After frontend splitSourceField → transform-data."""
    mapping = {
        "full_name": [
            {
                "object": "Contact",
                "field": "FirstName",
                "is_split_field": True,
            },
            {
                "object": "Contact",
                "field": "LastName",
                "is_split_field": True,
            },
        ],
        "email": {"object": "Contact", "field": "Email"},
    }
    transformation_rules = {
        "fields": [
            {
                "source_field": "full_name",
                "action": "split_2",
                "split_char": " ",
                "output_fields": ["FirstName", "LastName"],
            }
        ]
    }

    transformed, meta, _ = _run(
        DataTransformationService.transform_data_for_migration(
            data=SAMPLE_RECORDS,
            mapping=mapping,
            transformation_rules=transformation_rules,
            user_id=None,
            destination="salesforce",
        )
    )

    assert transformed[0]["FirstName"] == "Ada"
    assert transformed[0]["LastName"] == "Lovelace"
    split_applied = [
        t for t in meta.get("transformations_applied", []) if "split" in str(t.get("transformation", ""))
    ]
    assert len(split_applied) >= 1


def test_split_monotoken_goes_to_last_name():
    """Single-token full names must land in LastName (Salesforce required field)."""
    mapping = {
        "full_name": [
            {"object": "Contact", "field": "FirstName", "is_split_field": True},
            {"object": "Contact", "field": "LastName", "is_split_field": True, "is_required": True},
        ],
        "email": {"object": "Contact", "field": "Email"},
    }
    transformation_rules = {
        "fields": [
            {
                "source_field": "full_name",
                "action": "split_2",
                "split_char": " ",
                "output_fields": ["FirstName", "LastName"],
            }
        ]
    }
    rows = [{"full_name": "EMP20A00013", "email": "x@test.com"}]

    transformed, _, _ = _run(
        DataTransformationService.transform_data_for_migration(
            data=rows,
            mapping=mapping,
            transformation_rules=transformation_rules,
            user_id=None,
            destination="salesforce",
        )
    )

    assert transformed[0]["LastName"] == "EMP20A00013"
    assert transformed[0].get("FirstName", "") in ("", None) or transformed[0]["FirstName"] == ""


def test_undo_combine_payload_migrates_fields_independently():
    """After frontend uncombineGroup — no combine rule, plain mappings."""
    mapping = {
        "first_name": {"object": "Contact", "field": "FirstName"},
        "last_name": {"object": "Contact", "field": "LastName"},
        "email": {"object": "Contact", "field": "Email"},
    }
    transformation_rules = {"fields": []}

    transformed, _, _ = _run(
        DataTransformationService.transform_data_for_migration(
            data=SAMPLE_RECORDS,
            mapping=mapping,
            transformation_rules=transformation_rules,
            user_id=None,
            destination="salesforce",
        )
    )

    # transform-data keeps source keys; migration renames to destination fields
    assert transformed[0]["first_name"] == "Ada"
    assert transformed[0]["last_name"] == "Lovelace"
    assert transformed[0]["email"] == "ada@example.com"
    assert "Name" not in transformed[0]


def test_sql_combine_payload_gsheets_style_columns():
    """SQL/Google Sheets column mapping from frontend combine modal."""
    mapping = {
        "first_name": {
            "sheet": "Sheet1",
            "column": "Full Name",
            "is_combined_field": True,
            "combine_group": "sheet1.full_name",
            "combine_order": 0,
        },
        "last_name": {
            "sheet": "Sheet1",
            "column": "Full Name",
            "is_combined_field": True,
            "combine_group": "sheet1.full_name",
            "combine_order": 1,
        },
    }
    transformation_rules = {
        "fields": [
            {
                "source_fields": ["first_name", "last_name"],
                "action": "combine",
                "separator": " ",
                "output_field": "Full Name",
                "output_sheet": "Sheet1",
            }
        ]
    }

    transformed, _, _ = _run(
        DataTransformationService.transform_data_for_migration(
            data=SAMPLE_RECORDS,
            mapping=mapping,
            transformation_rules=transformation_rules,
            user_id=None,
            destination="google_sheets",
        )
    )

    assert transformed[0]["Full Name"] == "Ada Lovelace"
    assert "first_name" not in transformed[0]


def test_orphan_combine_rule_without_flags_still_combines_when_rule_present():
    """Rule-driven combine even if user stripped is_combined_field flags."""
    mapping = {
        "first_name": {"object": "Contact", "field": "Name"},
        "last_name": {"object": "Contact", "field": "Name"},
    }
    transformation_rules = {
        "fields": [
            {
                "source_fields": ["first_name", "last_name"],
                "action": "combine",
                "separator": " ",
                "output_field": "Name",
            }
        ]
    }

    transformed, _, _ = _run(
        DataTransformationService.transform_data_for_migration(
            data=SAMPLE_RECORDS,
            mapping=mapping,
            transformation_rules=transformation_rules,
            user_id=None,
            destination="salesforce",
        )
    )

    assert transformed[0]["Name"] == "Ada Lovelace"
