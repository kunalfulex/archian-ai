"""Tests for warehouse schema designer."""
import asyncio
from unittest.mock import AsyncMock, patch

from services.enterprise_graph.models import GraphMember
from services.enterprise_graph.warehouse_designer import (
    build_logical_model,
    build_warehouse_proposal,
    generate_ddl,
    normalize_snowflake_proposal_ddl,
    resolve_warehouse_dialect,
    resolve_warehouse_target_dataset,
)


def test_resolve_dialect_from_settings():
    members = []
    assert resolve_warehouse_dialect({"warehouse_dialect": "bigquery"}, members) == "bigquery"


def test_resolve_dialect_default_logical():
    assert resolve_warehouse_dialect({}, []) == "logical"


def test_build_logical_model_fact_and_dim():
    entities = [
        {
            "id": "c1",
            "name": "Customer",
            "domain_tags": ["party"],
            "attributes": {"columns": ["email", "name"]},
            "source_lineage": [{"member_id": "m1", "entity_id": "Account", "label": "Account"}],
        },
        {
            "id": "c2",
            "name": "Order",
            "domain_tags": ["commerce"],
            "attributes": {"columns": ["total_amount", "order_date"]},
            "source_lineage": [{"member_id": "m1", "entity_id": "Order", "label": "Order"}],
        },
    ]
    graph = {
        "edges": [
            {"source_id": "c2", "target_id": "c1", "label": "AccountId", "edge_type": "lookup"},
        ]
    }
    model = build_logical_model(canonical_entities=entities, canonical_graph=graph)
    assert model["summary"]["table_count"] == 2
    assert model["summary"]["fact_count"] >= 1
    assert model["summary"]["dimension_count"] >= 1
    facts = [t for t in model["tables"] if t["table_type"] == "fact"]
    assert any(t["name"].startswith("fact_") for t in facts)


def test_generate_bigquery_ddl():
    model = {
        "tables": [
            {
                "name": "dim_customer",
                "table_type": "dimension",
                "columns": [
                    {"name": "dim_customer_key", "role": "surrogate_key", "nullable": False},
                    {"name": "email", "role": "attribute", "nullable": True},
                ],
            }
        ]
    }
    ddl = generate_ddl(model, "bigquery")
    assert "CREATE TABLE IF NOT EXISTS" in ddl
    assert "dim_customer" in ddl
    assert "Archian AI warehouse proposal" in ddl


def test_resolve_warehouse_target_dataset_uses_snowflake_connector_schema():
    config = {
        "database_name": "TESTDATABASE",
        "additional_config": {"warehouse": "COMPUTE_WH", "schema": "ANALYTICS", "role": "ARCHIAN_RO"},
    }
    dataset = resolve_warehouse_target_dataset({}, [], "snowflake", connector_config=config)
    assert dataset == "ANALYTICS"


def test_normalize_snowflake_proposal_ddl_replaces_legacy_schema():
    ddl = (
        "CREATE TABLE IF NOT EXISTS archian_warehouse.dim_customer (\n"
        "  dim_customer_key VARCHAR\n);"
    )
    out = normalize_snowflake_proposal_ddl(ddl, "PUBLIC")
    assert "PUBLIC.dim_customer" in out
    assert "archian_warehouse" not in out.lower()


def test_generate_snowflake_ddl_uses_target_schema():
    model = {
        "tables": [
            {
                "name": "dim_customer",
                "table_type": "dimension",
                "columns": [{"name": "email", "role": "attribute", "nullable": True}],
            }
        ]
    }
    ddl = generate_ddl(model, "snowflake", dataset="PUBLIC")
    assert "CREATE TABLE IF NOT EXISTS PUBLIC.dim_customer" in ddl


def test_build_warehouse_proposal_payload():
    canonical_entities = [
        {
            "id": "c1",
            "name": "Product",
            "domain_tags": ["catalog"],
            "attributes": {"columns": ["sku"]},
            "source_lineage": [],
        }
    ]
    canonical_graph = {"edges": []}
    mock_logical_model = build_logical_model(
        canonical_entities=canonical_entities,
        canonical_graph=canonical_graph,
    )

    async def _run():
        with patch(
            "services.semantic_mapping.warehouse_designer_llm.propose_logical_model_llm",
            new_callable=AsyncMock,
            return_value=(mock_logical_model, "mocked warehouse design"),
        ):
            return await build_warehouse_proposal(
                graph_id="g1",
                run_id="r1",
                graph_settings={"warehouse_dialect": "postgres"},
                members=[],
                canonical_entities=canonical_entities,
                canonical_graph=canonical_graph,
            )

    proposal = asyncio.run(_run())
    assert proposal["dialect"] == "postgres"
    assert proposal["logical_model"]["tables"]
    assert proposal["ddl_text"]
    assert proposal["status"] == "draft"
    assert proposal.get("design_method") == "llm"
