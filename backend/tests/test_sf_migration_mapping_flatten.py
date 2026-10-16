"""Salesforce migrate must not flatten split arrays before batch transform."""
import asyncio
from unittest.mock import AsyncMock, patch

from services.data_transformation_service import DataTransformationService
from utils.mapping_utils import transform_mapping_for_migration


SAMPLE_ROWS = [{"FullName": "5", "Email": "a@test.com"}]

SPLIT_MAPPING = {
    "FullName": [
        {"object": "Contact", "field": "FirstName", "is_split_field": True},
        {
            "object": "Contact",
            "field": "LastName",
            "is_split_field": True,
            "is_required": True,
        },
    ],
    "Email": {"object": "Contact", "field": "Email"},
}

SPLIT_RULES = {
    "fields": [
        {
            "source_field": "FullName",
            "action": "split_2",
            "split_char": " ",
            "output_fields": ["FirstName", "LastName"],
        }
    ]
}


def _run(coro):
    return asyncio.run(coro)


def test_preflattened_mapping_skips_name_split():
    """Regression: flattening before transform_data_for_migration breaks split."""
    flat = transform_mapping_for_migration(SPLIT_MAPPING)
    assert "FullName" not in flat
    assert "FirstName" in flat

    transformed, meta, _ = _run(
        DataTransformationService.transform_data_for_migration(
            data=SAMPLE_ROWS,
            mapping=flat,
            transformation_rules=SPLIT_RULES,
            user_id=None,
            destination="salesforce",
        )
    )
    split_applied = [
        t for t in meta.get("transformations_applied", []) if "split" in str(t.get("transformation", ""))
    ]
    assert split_applied == []
    assert "FullName" in transformed[0]


def test_array_mapping_splits_monotoken_to_last_name():
    transformed, meta, _ = _run(
        DataTransformationService.transform_data_for_migration(
            data=SAMPLE_ROWS,
            mapping=SPLIT_MAPPING,
            transformation_rules=SPLIT_RULES,
            user_id=None,
            destination="salesforce",
        )
    )
    split_applied = [
        t for t in meta.get("transformations_applied", []) if "split" in str(t.get("transformation", ""))
    ]
    assert len(split_applied) >= 1
    assert transformed[0]["LastName"] == "5"
    assert transformed[0].get("FirstName", "") in ("", None) or transformed[0]["FirstName"] == ""
    assert "FullName" not in transformed[0]


def test_salesforce_runner_passes_split_arrays_to_background():
    from services.migration.context import MigrationContext
    from services.migration.salesforce_runner import start_salesforce_migration

    ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping=dict(SPLIT_MAPPING),
        document_type="Contact",
        destination="salesforce",
        destination_name="SF 2",
        transformation_rules=SPLIT_RULES,
        is_transformed_data=False,
    )

    captured = {}

    async def fake_start_background_migration(**kwargs):
        captured.update(kwargs)
        return "bg-job-1"

    extraction_row = {
        "extracted_data": SAMPLE_ROWS,
        "source_type": "salesforce",
        "record_count": 1,
    }

    async def run():
        with patch(
            "services.migration.salesforce_runner.validate_extraction_for_migration",
            new=AsyncMock(return_value=1),
        ), patch(
            "services.migration.salesforce_runner.validate_plan_for_migration",
            new=AsyncMock(),
        ), patch(
            "services.migration.salesforce_runner.check_migration_credits",
            new=AsyncMock(),
        ), patch(
            "services.migration.salesforce_runner.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(return_value=extraction_row),
        ), patch(
            "services.migration.salesforce_runner._resolve_destination_token",
            new=AsyncMock(
                return_value={
                    "org_id": "00Dxxx",
                    "id": "tok-1",
                    "connected_app_id": 1,
                    "org_display_name": "SF 2",
                }
            ),
        ), patch(
            "services.migration.salesforce_runner.get_valid_salesforce_token",
            new=AsyncMock(
                return_value={
                    "access_token": "at",
                    "instance_url": "https://example.my.salesforce.com",
                }
            ),
        ), patch(
            "services.migration.salesforce_runner._resolve_source_salesforce_credentials",
            new=AsyncMock(return_value=(None, None)),
        ), patch(
            "services.migration.salesforce_runner.MigrationRecordsService.create_migration_batch",
            new=AsyncMock(return_value="batch-1"),
        ), patch(
            "services.migration.salesforce_runner.background_migration_service.start_background_migration",
            new=AsyncMock(side_effect=fake_start_background_migration),
        ):
            await start_salesforce_migration(ctx)

    _run(run())

    passed_mapping = captured.get("mapping") or {}
    assert isinstance(passed_mapping.get("FullName"), list)
    assert captured.get("is_transformed_data") is False
    assert captured.get("transformation_rules") == SPLIT_RULES
