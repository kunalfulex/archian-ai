"""Tests for in-connector metadata and relationship gap analysis."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from services.data_assessment.metadata_gap_analyzer import analyze_sql_gaps
from services.data_assessment.models import EntityRef


def test_analyze_sql_gaps_fk_missing_referenced_table():
    entities = [EntityRef(entity_id="orders", label="orders")]
    schema_payload = {
        "columns": [{"name": "id"}, {"name": "user_id"}],
        "foreign_keys": [
            {"column": "user_id", "referenced_table": "users", "referenced_column": "id"}
        ],
    }

    async def mock_fetch_all(query, values=None):
        if "sql_extracted_schemas" in query:
            return [
                {
                    "table_name": "orders",
                    "schema_data": json.dumps(schema_payload),
                }
            ]
        return []

    with patch("services.data_assessment.metadata_gap_analyzer.database") as db:
        db.fetch_all = AsyncMock(side_effect=mock_fetch_all)
        findings = asyncio.run(
            analyze_sql_gaps("user-1", "conn-1", entities, entity_profiles=[])
        )

    assert any(f.category == "relationship" for f in findings)
    assert any("users" in f.message for f in findings)
