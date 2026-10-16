"""Tests for lake layout designer."""
from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.lake_designer import (
    build_bronze_tables,
    build_logical_model,
    build_source_to_lake_map,
    generate_lake_ddl,
    resolve_lake_dialect,
    resolve_lake_schemas,
)
from services.enterprise_graph.models import GraphMember


def _source_entity(**kwargs):
    defaults = {
        "member_id": "m1",
        "entity_id": "Account",
        "label": "Account",
        "connector_type": "salesforce",
        "normalized_name": "account",
        "column_names": ["Id", "Name", "Email"],
    }
    defaults.update(kwargs)
    return SourceEntity(**defaults)


def _member(**kwargs):
    defaults = {
        "id": "m1",
        "graph_id": "g1",
        "connector_type": "sql",
        "connector_ref": {"connector_id": "x"},
        "role": "source",
        "display_name": "Member",
        "sort_order": 0,
    }
    defaults.update(kwargs)
    return GraphMember(**defaults)


def test_resolve_lake_dialect_default_snowflake_with_lake_member():
    members = [_member(id="lake1", role="lake")]
    assert resolve_lake_dialect({}, members) == "snowflake"


def test_resolve_lake_schemas_defaults():
    bronze, silver = resolve_lake_schemas({}, [])
    assert "archian_bronze" in bronze.lower()
    assert "archian_silver" in silver.lower()


def test_build_bronze_tables_for_sources_only():
    members = [
        _member(
            id="m1",
            connector_type="salesforce",
            connector_ref={"connector_id": "s1"},
            role="source",
            display_name="SF Prod",
        ),
        _member(id="lake1", role="lake"),
    ]
    inventory = [
        _source_entity(),
        _source_entity(member_id="lake1", entity_id="skip", label="Skip"),
    ]
    tables = build_bronze_tables(inventory, members)
    assert len(tables) == 1
    assert tables[0]["name"].startswith("raw__")
    assert tables[0]["source_entity_id"] == "Account"
    col_names = [c["name"] for c in tables[0]["columns"]]
    assert "Id" in col_names or "id" in [c.lower() for c in col_names]


def test_build_source_to_lake_map():
    bronze = [
        {
            "name": "raw__sf__account",
            "source_member_id": "m1",
            "source_entity_id": "Account",
            "columns": [
                {"name": "Id", "source_column": "Id"},
                {"name": "_archian_loaded_at", "lineage": True},
            ],
        }
    ]
    smap = build_source_to_lake_map(bronze)
    assert smap["mapping_count"] == 1
    assert smap["mappings"][0]["bronze_table"] == "raw__sf__account"


def test_generate_snowflake_lake_ddl():
    model = build_logical_model(
        bronze_tables=[
            {
                "name": "raw__sf__account",
                "columns": [{"name": "Id", "type": "VARCHAR"}],
            }
        ],
        silver_views=[],
        bronze_schema="BRONZE",
        silver_schema="SILVER",
    )
    ddl = generate_lake_ddl(
        model, "snowflake", bronze_schema="ARCHIAN_BRONZE", silver_schema="ARCHIAN_SILVER"
    )
    assert "CREATE SCHEMA IF NOT EXISTS ARCHIAN_BRONZE" in ddl
    assert "CREATE TABLE IF NOT EXISTS ARCHIAN_BRONZE.raw__sf__account" in ddl
