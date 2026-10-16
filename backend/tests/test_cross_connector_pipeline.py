"""Regression: SQL→Salesforce compound pipelines."""
from services.planning.agent_runtime_enrichment import enrich_classification_for_agent_runtime
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef, SqlConnectionRef
from services.planning.endpoint_resolver import merge_endpoint_signals
from services.planning.plan_builder import build_plan_steps

def _catalog():
    cat = ResourceCatalog()
    cat.sql_connections.append(SqlConnectionRef(name="Test DB"))
    cat.salesforce_connections.append(SalesforceConnectionRef(name="SF 1"))
    return cat


DEPLOY_PROMPT = (
    "can you deploy customer data from Test DB database to SF 1 org, "
    "make sure all the emails are valid and there are no duplicate records"
)

EXTRACT_PROMPT = (
    "can you extarct customer data from Test DB database and migrate to SF 1 org, "
    "make sure all the emails are valid and there are no duplicate records"
)


def test_deploy_with_dedupe_not_post_extract_only():
    state = {
        "state": "extraction_completed",
        "active_job_id": "old-sf-job",
        "source": "salesforce",
        "source_name": "SF 1",
    }
    c = {
        "intent": "extract",
        "mode": "extract",
        "operations": ["extract", "map", "migrate"],
        "data_operations": ["dedupe", "filter"],
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF 1",
    }
    out = enrich_classification_for_agent_runtime(c, state, prompt=DEPLOY_PROMPT)
    assert out["mode"] == "extract"
    assert out["intent"] == "extract"


def test_dedupe_keywords_do_not_force_same_endpoint_when_migrating_to_sf():
    signals = merge_endpoint_signals(EXTRACT_PROMPT, {"in_place_transform": True})
    assert signals["destination_same_as_source"] is False


def test_sql_to_sf_cross_connector_plan_steps():
    cat = _catalog()
    llm = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "extract",
        "mode": "extract",
    }
    out = dict(llm)
    out["_plan_scope"] = {
        "operations": ["extract", "map", "migrate"],
        "data_operations": ["validate", "dedupe"],
    }

    plan = build_plan_steps(out, prompt=EXTRACT_PROMPT)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools[0] == "extract_sql"
    assert "run_data_ops_plan" in tools
    map_step = next(s for s in plan.steps if s.parameters.get("tool_id") == "map_fields")
    assert map_step.parameters.get("destination_name") == "SF 1"
    mig_step = next(s for s in plan.steps if s.parameters.get("tool_id") == "start_migration")
    assert mig_step.parameters.get("destination_name") == "SF 1"
