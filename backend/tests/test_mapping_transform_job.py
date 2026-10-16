"""Tests for mapping transform job persistence before migration."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.migration.mapping_transform_job import ensure_mapping_transform_job


def _run(coro):
    return asyncio.run(coro)


SAMPLE_ROWS = [
    {"FullName": "5", "Email": "a@test.com"},
    {"FullName": "John Doe", "Email": "b@test.com"},
]

SAMPLE_MAPPING = {
    "FullName": [
        {"object": "Contact", "field": "FirstName", "is_split_field": True},
        {"object": "Contact", "field": "LastName", "is_split_field": True, "is_required": True},
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
            "output_object": "Contact",
        }
    ]
}


@pytest.fixture
def mock_extraction_row():
    return {
        "extracted_data": SAMPLE_ROWS,
        "source_type": "salesforce",
        "document_type": "Contact",
        "has_transformation_rules": False,
        "record_count": 2,
    }


def test_no_rules_returns_unchanged(mock_extraction_row):
    result = _run(
        ensure_mapping_transform_job(
            user_id="user-1",
            source_job_id="job-1",
            mapping=SAMPLE_MAPPING,
            transformation_rules={"fields": []},
            destination="salesforce",
            extraction_result=mock_extraction_row,
        )
    )
    assert result.job_id == "job-1"
    assert result.created is False
    assert result.is_transformed_data is False


def test_creates_child_job_with_monotoken_in_last_name(mock_extraction_row):
    saved_job_id = {"value": None}

    async def fake_save(**kwargs):
        saved_job_id["value"] = kwargs["job_id"]
        return kwargs["job_id"]

    with patch(
        "services.extraction_results_service.ExtractionResultsService.save_extraction_result",
        new=AsyncMock(side_effect=fake_save),
    ):
        result = _run(
            ensure_mapping_transform_job(
                user_id="user-1",
                source_job_id="job-parent",
                mapping=SAMPLE_MAPPING,
                transformation_rules=SPLIT_RULES,
                destination="salesforce",
                org_id="00Dxxx",
                extraction_result=mock_extraction_row,
            )
        )

    assert result.created is True
    assert result.is_transformed_data is True
    assert result.job_id != "job-parent"
    assert result.transformed_data[0]["LastName"] == "5"
    assert result.transformed_data[0].get("FirstName", "") in ("", None) or result.transformed_data[0]["FirstName"] == ""
    assert result.transformed_data[1]["FirstName"] == "John"
    assert result.transformed_data[1]["LastName"] == "Doe"


def test_reuses_already_transformed_job(mock_extraction_row):
    mock_extraction_row["has_mapping_transform"] = True
    mock_extraction_row["extracted_data"] = [
        {"FirstName": "", "LastName": "5", "Email": "a@test.com"},
    ]

    with patch(
        "services.extraction_results_service.ExtractionResultsService.save_extraction_result",
        new=AsyncMock(),
    ) as save_mock:
        result = _run(
            ensure_mapping_transform_job(
                user_id="user-1",
                source_job_id="job-transformed",
                mapping=SAMPLE_MAPPING,
                transformation_rules=SPLIT_RULES,
                destination="salesforce",
                extraction_result=mock_extraction_row,
            )
        )

    save_mock.assert_not_called()
    assert result.created is False
    assert result.job_id == "job-transformed"
    assert result.is_transformed_data is True


def test_runs_mapping_transform_when_only_extraction_transform_applied(mock_extraction_row):
    """Post-extract transform sets has_transformation_rules but not has_mapping_transform."""
    mock_extraction_row["has_transformation_rules"] = True
    saved_job_id = {"value": None}

    async def fake_save(**kwargs):
        saved_job_id["value"] = kwargs["job_id"]
        assert kwargs.get("has_mapping_transform") is True
        return kwargs["job_id"]

    with patch(
        "services.extraction_results_service.ExtractionResultsService.save_extraction_result",
        new=AsyncMock(side_effect=fake_save),
    ):
        result = _run(
            ensure_mapping_transform_job(
                user_id="user-1",
                source_job_id="job-post-extract",
                mapping=SAMPLE_MAPPING,
                transformation_rules=SPLIT_RULES,
                destination="salesforce",
                org_id="00Dxxx",
                extraction_result=mock_extraction_row,
            )
        )

    assert result.created is True
    assert result.job_id != "job-post-extract"
    assert result.transformed_data[0]["LastName"] == "5"
