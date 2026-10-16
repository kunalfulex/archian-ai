"""Spreadsheet header policy — PLAN_SCOPE source + slot satisfaction."""
from __future__ import annotations

from services.planning.goal_parser import _apply_plan_scope_llm_data
from services.planning.op_requirements import CREATE_HEADERS, assess_plan_requirements, derive_plan_requirements
from services.planning.source_column_headers import (
    HEADER_SOURCE_FROM_EXTRACTION,
    HEADER_SOURCE_FROM_USER,
    HEADER_SOURCE_USER_SPECIFIED_INLINE,
    build_create_header_contract,
    create_headers_slot_satisfied,
    create_headers_use_extraction,
    read_spreadsheet_header_source,
)
from services.planning.plan_scope import sync_scope_into_classification
from tests.plan_scope_helpers import with_plan_scope


def test_plan_scope_merge_sets_from_extraction_on_extract_create():
    out = _apply_plan_scope_llm_data(
        {},
        {
            "pipeline_ops": ["extract", "create", "map", "migrate"],
            "create_destination": True,
            "spreadsheet_headers": ["Name", "Email"],
        },
    )
    assert out["spreadsheet_header_source"] == HEADER_SOURCE_FROM_EXTRACTION
    assert "spreadsheet_headers" not in out


def test_plan_scope_merge_inline_headers():
    out = _apply_plan_scope_llm_data(
        {},
        {
            "pipeline_ops": ["create"],
            "create_destination": True,
            "spreadsheet_header_source": "user_specified_inline",
            "spreadsheet_headers": ["Name", "Email"],
        },
    )
    assert out["spreadsheet_header_source"] == HEADER_SOURCE_USER_SPECIFIED_INLINE
    assert out["spreadsheet_headers"] == ["Name", "Email"]


def test_create_only_from_user_policy():
    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_header_source": HEADER_SOURCE_FROM_USER,
    }
    cls = {"destination": "google_sheets", "planner_create_spreadsheet": True}
    assert read_spreadsheet_header_source(cls, goal) == HEADER_SOURCE_FROM_USER
    assert not create_headers_use_extraction(cls, goal)
    assert not create_headers_slot_satisfied(cls, goal)


def test_continuation_export_infers_from_extraction():
    goal = {
        "operations": ["create", "map", "migrate"],
        "create_destination": True,
        "spreadsheet_header_source": HEADER_SOURCE_FROM_EXTRACTION,
    }
    cls = {
        "active_job_id": "job-1",
        "destination": "google_sheets",
        "planner_create_spreadsheet": True,
    }
    assert create_headers_use_extraction(cls, goal)
    assert create_headers_slot_satisfied(cls, goal)


def test_export_fresh_plan_skips_create_headers_slot():
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_object_api_name": "Lead",
            "destination": "google_sheets",
            "planner_create_spreadsheet": True,
            "planner_spreadsheet_title_confirmed": True,
            "planner_new_spreadsheet_name": "Lead Export",
        },
        operations=["extract", "create", "map", "migrate"],
        create_destination=True,
        spreadsheet_header_source=HEADER_SOURCE_FROM_EXTRACTION,
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["extract", "create", "map", "migrate"],
    )
    reqs = derive_plan_requirements(
        ["extract", "create", "map", "migrate"],
        classification=classification,
    )
    gaps, _clars = assess_plan_requirements(classification, reqs)
    assert CREATE_HEADERS not in [g["slot"] for g in gaps]


def test_create_header_contract_uses_extraction_for_export():
    goal = {
        "operations": ["extract", "create", "map", "migrate"],
        "create_destination": True,
        "spreadsheet_header_source": HEADER_SOURCE_FROM_EXTRACTION,
    }
    cls = {
        "planner_create_spreadsheet": True,
        "planner_new_spreadsheet_name": "Export",
        "_plan_scope": goal,
    }
    contract = build_create_header_contract(cls, goal, pipeline_tokens=["extract", "create", "map", "migrate"])
    assert contract.use_extraction_headers is True
    assert contract.headers == []
