"""Warehouse designer integration with lake silver catalog."""
from services.enterprise_graph.config import should_use_lake_silver_for_warehouse
from services.enterprise_graph.lake_designer import normalize_lake_proposal_ddl
from services.enterprise_graph.warehouse_designer import (
    build_logical_model_with_lake_silver,
    build_source_to_warehouse_map,
    get_silver_column_catalog,
)


def test_should_use_lake_silver_for_warehouse():
    proposal = {
        "logical_model": {
            "silver_views": [{"canonical_entity_id": "c1", "name": "stg__core__account"}],
        }
    }
    assert should_use_lake_silver_for_warehouse({"lake_enabled": True}, proposal) is True
    assert should_use_lake_silver_for_warehouse({}, proposal) is False
    assert should_use_lake_silver_for_warehouse({"lake_enabled": True}, None) is False


def test_get_silver_column_catalog_from_bronze():
    lake_lm = {
        "zones": {"silver_schema": "SILVER"},
        "bronze_tables": [
            {
                "name": "raw__sf__account",
                "columns": [
                    {"name": "Id", "lineage": False},
                    {"name": "_archian_loaded_at", "lineage": True},
                ],
            }
        ],
        "silver_views": [
            {
                "canonical_entity_id": "c1",
                "name": "stg__core__account",
                "source_bronze_tables": ["raw__sf__account"],
            }
        ],
    }
    catalog = get_silver_column_catalog(lake_lm)
    assert "c1" in catalog
    assert catalog["c1"]["view_name"] == "stg__core__account"
    assert "Id" in catalog["c1"]["columns"]


def test_build_logical_model_with_lake_silver_lineage():
    lake_lm = {
        "zones": {"silver_schema": "SILVER"},
        "bronze_tables": [
            {
                "name": "raw__sf__account",
                "columns": [{"name": "email", "lineage": False}],
            }
        ],
        "silver_views": [
            {
                "canonical_entity_id": "c1",
                "name": "stg__core__account",
                "source_bronze_tables": ["raw__sf__account"],
            }
        ],
    }
    entities = [
        {
            "id": "c1",
            "name": "Account",
            "domain_tags": ["party"],
            "attributes": {"columns": ["email"]},
            "source_lineage": [],
        }
    ]
    model = build_logical_model_with_lake_silver(
        canonical_entities=entities,
        canonical_graph={"edges": []},
        lake_logical_model=lake_lm,
    )
    assert model["source_catalog"] == "lake_silver"
    assert model["summary"]["table_count"] >= 1
    smap = build_source_to_warehouse_map(model)
    silver_rows = [m for m in smap["mappings"] if m.get("source_layer") == "silver"]
    assert silver_rows
    assert silver_rows[0]["silver_view"] == "stg__core__account"


def test_normalize_lake_proposal_ddl_replaces_default_schemas():
    ddl = (
        "CREATE SCHEMA IF NOT EXISTS archian_bronze;\n"
        "CREATE TABLE IF NOT EXISTS archian_bronze.raw__a (id VARCHAR);\n"
        "CREATE OR REPLACE VIEW archian_silver.stg__a AS SELECT 1;"
    )
    out = normalize_lake_proposal_ddl(ddl, "MY_BRONZE", "MY_SILVER")
    assert "MY_BRONZE.raw__a" in out
    assert "MY_SILVER.stg__a" in out
