"""Tests for LLM warehouse output validation."""
from services.semantic_mapping.warehouse_validator import validate_and_normalize


def test_validate_rejects_quality_artifact_columns():
    bundle = {
        "canonical_entities": [
            {
                "id": "c1",
                "columns": [{"name": "email"}, {"name": "name"}],
            }
        ]
    }
    raw = {
        "tables": [
            {
                "name": "dim_customer",
                "table_type": "dimension",
                "columns": [
                    {"name": "email", "type": "STRING"},
                    {"name": "total_missing", "type": "INT64"},
                ],
                "canonical_entity_ids": ["c1"],
            }
        ],
        "rationale": "Test model",
    }
    model, warnings = validate_and_normalize(raw, bundle, dialect="logical")
    col_names = {c["name"] for c in model["tables"][0]["columns"]}
    assert "email" in col_names
    assert "total_missing" not in col_names
    assert model["design_method"] == "llm"
