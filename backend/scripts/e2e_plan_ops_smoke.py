#!/usr/bin/env python3
"""Smoke checks for plan-scoped operation assembly (CI-friendly, no live connectors)."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.planning.plan_builder import build_plan_steps


def _tool_ids(classification: dict, prompt: str) -> list[str]:
    plan = build_plan_steps(classification, prompt=prompt)
    return [s.parameters.get("tool_id") for s in plan.steps]


def _assert_ops(label: str, classification: dict, prompt: str, expected_tools: list[str]) -> None:
    tools = _tool_ids(classification, prompt)
    assert tools == expected_tools, f"{label}: expected {expected_tools}, got {tools}"


def main() -> int:
    _assert_ops(
        "standalone_create",
        {
            "destination": "google_sheets",
            "intent": "create",
            "_plan_scope": {
                "operations": ["create"],
                "create_destination": True,
                "spreadsheet_title": "Leads",
                "spreadsheet_headers": ["Name", "Email"],
            },
            "planner_create_spreadsheet": True,
            "planner_new_spreadsheet_name": "Leads",
            "planner_spreadsheet_headers": ["Name", "Email"],
        },
        "Create sheet Leads with Name and Email",
        ["google_sheets_create_from_prompt"],
    )

    _assert_ops(
        "sf_extract_to_new_sheet",
        {
            "source": "salesforce",
            "source_name": "Prod",
            "destination": "google_sheets",
            "intent": "extract",
            "_plan_scope": {
                "operations": ["extract", "create", "map", "migrate"],
                "create_destination": True,
                "spreadsheet_title": "Export",
            },
            "planner_create_spreadsheet": True,
            "planner_new_spreadsheet_name": "Export",
        },
        "Extract contacts from Prod and put in new sheet Export",
        [
            "extract_salesforce",
            "google_sheets_create_from_prompt",
            "map_fields",
            "start_migration",
        ],
    )

    _assert_ops(
        "multi_source_join",
        {
            "source": "google_sheets",
            "source_name": "Book A",
            "source_sheet_name": "Tab1",
            "planner_additional_sources": [
                {"connector_type": "google_sheets", "display_name": "Book B", "sheet_name": "Tab2"}
            ],
            "multi_source_pipeline": True,
            "_plan_scope": {"operations": ["extract", "join"]},
        },
        "Join Book A Tab1 and Book B Tab2 on Email",
        ["extract_google_sheets", "gsheets_execute_operation"],
    )

    _assert_ops(
        "metadata_deploy",
        {
            "source": "salesforce",
            "source_name": "Production",
            "destination": "salesforce",
            "destination_name": "Sandbox",
            "intent": "metadata_deploy",
            "_plan_scope": {"operations": ["deploy_schema"]},
        },
        "Deploy record types from Production to Sandbox",
        ["metadata_deploy_salesforce"],
    )

    _assert_ops(
        "standalone_upsert",
        {
            "source": "google_sheets",
            "destination": "google_sheets",
            "destination_name": "Leads",
            "intent": "upsert",
            "_plan_scope": {"operations": ["upsert"]},
        },
        "Upsert row into Leads",
        ["gsheets_execute_operation"],
    )

    _assert_ops(
        "continuation_map_migrate",
        {
            "source": "salesforce",
            "destination": "google_sheets",
            "destination_name": "Export",
            "active_job_id": "job-1",
            "_session_progress": {"mapping_ready": False},
            "plan_route": "continuation_path",
            "_continuation_steps": ["map", "migrate"],
            "_plan_scope": {"operations": ["map", "migrate"]},
            "_continuation_steps": ["map", "migrate"],
        },
        "map and migrate",
        ["map_fields", "start_migration"],
    )

    _assert_ops(
        "standalone_search",
        {
            "source": "google_sheets",
            "destination_name": "Leads",
            "intent": "search",
            "_plan_scope": {"operations": ["search"]},
            "search_criteria": "email contains @acme.com",
        },
        "Search Leads for email contains @acme.com",
        ["gsheets_execute_operation"],
    )

    print("e2e_plan_ops_smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
