"""Data quality analysis on GSheets join/search persist."""

import asyncio
from unittest.mock import AsyncMock, patch

from services.google_sheets.extraction_persist import persist_gsheets_operation_as_extraction
from services.google_sheets.models import SpreadsheetContext


def test_persist_includes_data_quality_report():
    ctx = SpreadsheetContext(
        found=True,
        spreadsheet_id="sid",
        spreadsheet_name="Employee list",
        sheet_name="Sheet1",
    )
    rows = [{"Email": "a@b.com", "First Name": "Ann"}]
    fake_report = {
        "summary": {
            "data_quality_score": 88.5,
            "has_issues": True,
            "recommendations": ["Fill missing values in: Phone"],
        },
        "summary_text": "**Data Quality Analysis** (Score: 88.5/100)\n",
    }

    with patch(
        "services.extraction_results_service.ExtractionResultsService.save_extraction_result",
        new_callable=AsyncMock,
    ), patch(
        "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
        new_callable=AsyncMock,
        return_value={"source_type": "google_sheets", "source_name": "Employee list", "record_count": 1},
    ), patch(
        "utils.extraction_sse_payload.build_job_status_after_db_save",
        new_callable=AsyncMock,
        return_value={"job_id": "j1", "status": "completed", "result": {}},
    ), patch(
        "asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=fake_report,
    ) as mock_thread:
        job_id, sse = asyncio.run(
            persist_gsheets_operation_as_extraction(
            user_id="u1",
            conversation_id=None,
            classification={},
            ctx=ctx,
            rows=rows,
            extraction_goals="join",
            document_type="Joined Data",
            document_description="test",
            operation_summary="### Join done",
            )
        )
        mock_thread.assert_called_once()
        assert job_id
        assert "extraction_job_finished" in sse
