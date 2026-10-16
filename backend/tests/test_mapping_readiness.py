"""Job-scoped mapping readiness helpers."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.migration.context import MigrationContext
from services.migration.mapping_readiness import (
    goal_data_ops_schema_changing,
    is_row_only_operations,
    mapping_ready_for_job,
    schemas_compatible,
)


def test_schemas_compatible_ignores_order():
    assert schemas_compatible(["a", "b"], ["b", "a"]) is True
    assert schemas_compatible(["a", "b"], ["a", "c"]) is False


def test_row_only_operations():
    assert is_row_only_operations(["filter"]) is True
    assert is_row_only_operations(["filter", "dedupe"]) is True
    assert is_row_only_operations(["transform"]) is False


def test_goal_data_ops_schema_changing():
    assert goal_data_ops_schema_changing(["filter"]) is False
    assert goal_data_ops_schema_changing(["transform"]) is True


def test_mapping_ready_for_job_on_target():
    async def run():
        ctx = MigrationContext(
            user_id="user-1",
            extraction_job_id="job-1",
            mapping={"Email": {"field": "Email"}},
            document_type="Contact",
            destination="salesforce",
        )
        with patch(
            "services.migration.context_repository.get_context",
            new=AsyncMock(return_value=ctx),
        ):
            ready = await mapping_ready_for_job(
                "user-1", "conv-1", "job-1"
            )
        assert ready is True

    asyncio.run(run())


def test_mapping_ready_for_job_lineage_compatible():
    parent_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-v2",
        mapping={"Email": {"field": "Email"}},
        document_type="Contact",
        destination="salesforce",
    )

    async def run():
        with patch(
            "services.migration.context_repository.get_context",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.mapping_lineage.find_lineage_mapping_context",
            new=AsyncMock(return_value=(parent_ctx, "job-v2")),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                side_effect=[
                    {"extracted_data": [{"Email": "a", "Name": "x"}]},
                    {"extracted_data": [{"Email": "b", "Name": "y"}]},
                ]
            ),
        ):
            ready = await mapping_ready_for_job(
                "user-1", "conv-1", "job-v3"
            )
        assert ready is True

    asyncio.run(run())
