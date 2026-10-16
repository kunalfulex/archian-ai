"""Tests for flow save context resolver."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from db.extracted_schema import SaveMappingFlowRequest
from services.flow.migration_rules_resolve import (
    build_flow_migration_rules,
    merge_richest_extraction_rules,
)
from services.flow.save_context import (
    FlowSaveContext,
    find_all_extraction_completed,
    find_best_extraction_completed,
    find_best_migration_completed,
    resolve_flow_save_context,
)
from services.migration.context import MigrationContext
from utils.mapping_utils import sanitize_mapping_for_save


def test_save_mapping_flow_request_accepts_split_field_list():
    gs_split_mapping = {
        "Name": [
            {"sheet": "Sheet1", "column": "FirstName", "is_split_field": True},
            {"sheet": "Sheet1", "column": "LastName", "is_split_field": True},
        ],
        "Email": {"sheet": "Sheet1", "column": "Email", "is_duplicate_check": True},
    }
    req = SaveMappingFlowRequest(
        flow_name="GS flow",
        document_type="Customer Contact List",
        extracted_schema={"Name": "Jane Doe", "Email": "j@example.com"},
        all_records=[{"Name": "Jane Doe", "Email": "j@example.com"}],
        mapping=gs_split_mapping,
        user_prompt="migrate to Archian Contacts",
        extracted_schema_type="gsheets",
        destination="google_sheets",
        destination_name="Archian Contacts",
        destination_id="1ygTnVj0yD1mAYOczUfksAmV55D-o_0ImOPguhmFgbyA",
        migration_rules={
            "mapping_transformation": {
                "fields": [{"action": "split_2", "source_field": "Name", "split_char": " "}]
            }
        },
    )
    assert isinstance(req.mapping["Name"], list)
    assert len(req.mapping["Name"]) == 2
    assert req.mapping["Email"]["column"] == "Email"


def test_sanitize_mapping_for_save_drops_rule_keys():
    raw = {
        "Name": [{"sheet": "Sheet1", "column": "FirstName"}],
        "Email": {"sheet": "Sheet1", "column": "Email"},
        "filter_rules": {"logic": "AND", "conditions": []},
        "transformation_rules": {"fields": []},
    }
    cleaned = sanitize_mapping_for_save(raw)
    assert "filter_rules" not in cleaned
    assert "transformation_rules" not in cleaned
    assert cleaned["Name"] == raw["Name"]
    assert cleaned["Email"] == raw["Email"]


def test_flow_save_context_to_save_request_gsheets_split():
    ctx = FlowSaveContext(
        records_job_id="job-1",
        mapping={
            "Name": [
                {"sheet": "Sheet1", "column": "FirstName"},
                {"sheet": "Sheet1", "column": "LastName"},
            ],
            "Email": {"sheet": "Sheet1", "column": "Email"},
        },
        document_type="Customer Contact List",
        user_prompt="migrate data",
        extracted_schema_type="gsheets",
        destination="google_sheets",
        destination_name="Archian Contacts",
        destination_id="sheet-id-123",
        migration_rules={
            "mapping_transformation": {
                "fields": [{"action": "split_2", "source_field": "Name"}]
            }
        },
        all_records=[{"Name": "Jane Doe", "Email": "j@example.com"}],
    )
    req = ctx.to_save_request("GS flow")
    assert req.flow_name == "GS flow"
    assert isinstance(req.mapping["Name"], list)
    assert req.destination_id == "sheet-id-123"
    assert req.migration_rules["mapping_transformation"]["fields"][0]["source_field"] == "Name"


def test_merge_richest_extraction_rules_prefers_filter_over_schema_only():
    thin = {"source_schema": ["Email"]}
    rich = {
        "source_schema": ["Email", "Status"],
        "filter_rules": {
            "logic": "AND",
            "conditions": [{"field": "Status", "operator": "equals", "value": "Active"}],
        },
    }
    merged = merge_richest_extraction_rules([thin, rich])
    assert merged.get("filter_rules")
    assert merged["filter_rules"]["conditions"][0]["field"] == "Status"


def test_build_flow_migration_rules_includes_mapping_transform_and_pref():
    rules = build_flow_migration_rules(
        ctx_transformation_rules={"fields": [{"action": "split", "source_field": "Name"}]},
        extraction_rules={"source_schema": ["Name"]},
        mapping_draft={"missing_field_preference": {"option": "skip", "placeholderValue": ""}},
        all_records=[{"Name": "Jane"}],
        source_type="salesforce",
    )
    assert rules["mapping_transformation"]["fields"][0]["source_field"] == "Name"
    assert rules["missing_field_preference"]["option"] == "skip"
    assert rules["extraction_rules"]["source_schema"] == ["Name"]


def test_find_all_extraction_completed_orders_by_version():
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-parent",
                "extraction_version": 1,
            }
        },
        {
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-child",
                "parent_job_id": "job-parent",
                "extraction_version": 2,
                "filter_rules": {"logic": "AND", "conditions": []},
            }
        },
    ]
    all_evts = find_all_extraction_completed(history, "job-child", ["job-parent", "job-child"])
    assert len(all_evts) == 2
    assert all_evts[0]["job_id"] == "job-child"


def test_find_best_migration_completed_prefers_rich_event():
    history = [
        {
            "metadata": {
                "event_type": "migration_completed",
                "source_job_id": "job-1",
                "destination": "salesforce",
            }
        },
        {
            "metadata": {
                "event_type": "migration_completed",
                "source_job_id": "job-1",
                "destination_connector_id": "token-2",
                "destination_name": "SF 2",
                "source_connector_id": "token-1",
                "sql_query": "SELECT Id FROM Contact",
            }
        },
    ]
    meta = find_best_migration_completed(history, source_job_id="job-1")
    assert meta["destination_connector_id"] == "token-2"
    assert meta["sql_query"] == "SELECT Id FROM Contact"


def test_find_best_extraction_completed_prefers_highest_version():
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-parent",
                "extraction_version": 1,
                "soql": "SELECT Id FROM Contact LIMIT 200",
            }
        },
        {
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-child",
                "parent_job_id": "job-parent",
                "extraction_version": 2,
            }
        },
    ]
    meta = find_best_extraction_completed(history, "job-child", ["job-parent", "job-child"])
    assert meta["job_id"] == "job-child"


def test_resolve_flow_save_context_sf_cross_org():
    stored = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-child",
        mapping={"Email": {"object": "Contact", "field": "Email"}},
        document_type="Contact",
        destination="salesforce",
        destination_name="SF 2",
    )

    async def run():
        history = [
            {
                "metadata": {
                    "event_type": "extraction_completed",
                    "job_id": "job-parent",
                    "extraction_version": 1,
                    "source_type": "salesforce",
                    "source_name": "SF 1",
                    "soql": "SELECT Id, Email FROM Contact LIMIT 200",
                }
            },
            {
                "metadata": {
                    "event_type": "migration_completed",
                    "source_job_id": "job-child",
                    "destination": "salesforce",
                    "destination_name": "SF 2",
                    "destination_connector_id": "dest-token",
                    "source_connector_id": "src-token",
                }
            },
        ]
        with patch(
            "services.chat_service.ChatService.get_messages_with_metadata_for_context",
            new=AsyncMock(return_value=history),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_lineage_job_ids",
            new=AsyncMock(return_value=["job-parent", "job-child"]),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                return_value={
                    "source_type": "salesforce",
                    "source_name": "SF 1",
                    "source_identifier": "00Dparent",
                    "document_type": "Contact",
                    "extraction_goals": "extract contacts",
                    "extracted_data": [{"Email": "a@b.com"}],
                    "extraction_metadata": {"object_api_name": "Contact"},
                }
            ),
        ), patch(
            "services.migration.mapping_lineage.find_lineage_mapping_context",
            new=AsyncMock(return_value=(stored, "job-child")),
        ), patch(
            "services.flow.save_context._resolve_sf_token_id",
            new=AsyncMock(side_effect=lambda uid, **kw: kw.get("token_id") or "resolved-src"),
        ):
            ctx = await resolve_flow_save_context(
                user_id="user-1",
                conversation_id="conv-1",
                records_job_id="job-child",
            )
        assert ctx.source_connector_id == "src-token"
        assert ctx.destination_id == "dest-token"
        assert ctx.destination_name == "SF 2"
        assert ctx.sql_query == "SELECT Id, Email FROM Contact LIMIT 200"
        assert ctx.table_name == "Contact"
        req = ctx.to_save_request("My Flow")
        assert req.connector_id == "src-token"
        assert req.destination_id == "dest-token"

    asyncio.run(run())


def test_resolve_flow_save_context_merges_child_filter_rules():
    stored = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-child",
        mapping={"Email": {"object": "Contact", "field": "Email"}},
        document_type="Contact",
        destination="salesforce",
        destination_name="SF 2",
        transformation_rules={"fields": [{"action": "split", "source_field": "FullName"}]},
        migration_rules={"missing_field_preference": {"option": "placeholder", "placeholderValue": "N/A"}},
    )

    async def run():
        history = [
            {
                "metadata": {
                    "event_type": "extraction_completed",
                    "job_id": "job-parent",
                    "extraction_version": 1,
                    "source_type": "sql",
                    "extraction_rules": {"source_schema": ["Email", "FullName"]},
                    "query": "SELECT Email, FullName FROM users",
                }
            },
            {
                "metadata": {
                    "event_type": "extraction_completed",
                    "job_id": "job-child",
                    "parent_job_id": "job-parent",
                    "extraction_version": 2,
                    "extraction_rules": {
                        "filter_rules": {
                            "logic": "AND",
                            "conditions": [{"field": "Email", "operator": "is_not_null"}],
                        }
                    },
                }
            },
            {
                "metadata": {
                    "event_type": "mapping_started",
                    "job_id": "job-child",
                    "transformation_rules": {"fields": [{"action": "split", "source_field": "FullName"}]},
                }
            },
            {
                "metadata": {
                    "event_type": "migration_completed",
                    "source_job_id": "job-child",
                    "destination": "salesforce",
                    "extraction_rules": {"source_schema": ["Email"]},
                }
            },
        ]
        with patch(
            "services.chat_service.ChatService.get_messages_with_metadata_for_context",
            new=AsyncMock(return_value=history),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_lineage_job_ids",
            new=AsyncMock(return_value=["job-parent", "job-child"]),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                side_effect=lambda jid, uid, **kw: {
                    "job-parent": {
                        "source_type": "sql",
                        "source_identifier": "conn-1",
                        "document_type": "users",
                        "extracted_data": [{"Email": "a@b.com", "FullName": "A B"}],
                        "extraction_metadata": {
                            "query": "SELECT Email, FullName FROM users",
                            "extraction_rules": {"source_schema": ["Email", "FullName"]},
                        },
                    },
                    "job-child": {
                        "source_type": "sql",
                        "source_identifier": "conn-1",
                        "document_type": "users",
                        "extracted_data": [{"Email": "a@b.com", "FullName": "A B"}],
                        "extraction_metadata": {
                            "extraction_rules": {
                                "filter_rules": {
                                    "logic": "AND",
                                    "conditions": [{"field": "Email", "operator": "is_not_null"}],
                                }
                            }
                        },
                    },
                }.get(jid)
            ),
        ), patch(
            "services.migration.mapping_lineage.find_lineage_mapping_context",
            new=AsyncMock(return_value=(stored, "job-child")),
        ), patch(
            "services.flow.save_context._resolve_sf_token_id",
            new=AsyncMock(return_value=None),
        ):
            ctx = await resolve_flow_save_context(
                user_id="user-1",
                conversation_id="conv-1",
                records_job_id="job-child",
                mapping_draft={
                    "missing_field_preference": {"option": "placeholder", "placeholderValue": "N/A"}
                },
            )
        ex = ctx.migration_rules.get("extraction_rules") or {}
        assert ex.get("filter_rules")
        assert ex.get("source_schema")
        assert ctx.migration_rules.get("mapping_transformation")
        assert ctx.migration_rules["missing_field_preference"]["option"] == "placeholder"
        assert ctx.sql_query == "SELECT Email, FullName FROM users"

    asyncio.run(run())


def test_resolve_flow_save_context_gsheets_split_mapping():
    stored = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={
            "Name": [
                {"sheet": "Sheet1", "column": "FirstName", "is_split_field": True},
                {"sheet": "Sheet1", "column": "LastName", "is_split_field": True},
            ],
            "Email": {"sheet": "Sheet1", "column": "Email", "is_duplicate_check": True},
            "filter_rules": {"logic": "AND", "conditions": []},
        },
        document_type="Customer Contact List",
        destination="google_sheets",
        destination_name="Archian Contacts",
        spreadsheet_id="1ygTnVj0yD1mAYOczUfksAmV55D-o_0ImOPguhmFgbyA",
        transformation_rules={
            "fields": [{"action": "split_2", "source_field": "Name", "split_char": " "}]
        },
    )

    async def run():
        history = [
            {
                "metadata": {
                    "event_type": "extraction_completed",
                    "job_id": "job-1",
                    "source_type": "google_sheets",
                    "source_name": "Archian Data",
                }
            },
            {
                "metadata": {
                    "event_type": "mapping_started",
                    "job_id": "job-1",
                    "destination": "google_sheets",
                    "destination_name": "Archian Contacts",
                    "destination_spreadsheet_id": "1ygTnVj0yD1mAYOczUfksAmV55D-o_0ImOPguhmFgbyA",
                }
            },
            {
                "metadata": {
                    "event_type": "migration_completed",
                    "source_job_id": "job-1",
                    "destination": "google_sheets",
                    "destination_name": "Archian Contacts",
                    "destination_spreadsheet_id": "1ygTnVj0yD1mAYOczUfksAmV55D-o_0ImOPguhmFgbyA",
                }
            },
        ]
        with patch(
            "services.chat_service.ChatService.get_messages_with_metadata_for_context",
            new=AsyncMock(return_value=history),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_lineage_job_ids",
            new=AsyncMock(return_value=["job-1"]),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                return_value={
                    "source_type": "google_sheets",
                    "source_name": "Archian Data",
                    "source_identifier": "src-sheet-id",
                    "document_type": "Customer Contact List",
                    "extraction_goals": "migrate data to Archian Contacts",
                    "extracted_data": [{"Name": "Jane Doe", "Email": "j@example.com"}],
                    "extraction_metadata": {},
                }
            ),
        ), patch(
            "services.migration.mapping_lineage.find_lineage_mapping_context",
            new=AsyncMock(return_value=(stored, "job-1")),
        ):
            ctx = await resolve_flow_save_context(
                user_id="user-1",
                conversation_id="conv-1",
                records_job_id="job-1",
            )
        assert "filter_rules" not in ctx.mapping
        assert isinstance(ctx.mapping["Name"], list)
        assert len(ctx.mapping["Name"]) == 2
        assert ctx.destination_id == "1ygTnVj0yD1mAYOczUfksAmV55D-o_0ImOPguhmFgbyA"
        assert ctx.migration_rules.get("mapping_transformation")
        req = ctx.to_save_request("GS flow")
        assert isinstance(req.mapping["Name"], list)

    asyncio.run(run())
