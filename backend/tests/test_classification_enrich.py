"""Tests for classify enrichment (post-extract routing)."""

from handlers.classification_handler import (
    enrich_classification_for_agent_runtime,
    inherit_session_resources_from_intent_state,
    latest_extraction_session_for_classifier,
)
from services.planning.continuation_policy import apply_extraction_session_continuation
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef
from services.planning.endpoint_resolver import merge_endpoint_signals


def test_enrich_data_ops_followup_uses_classifier_pins():
    intent_state = {"state": "extraction_completed"}
    classification = {
        "intent": "transform",
        "mode": "data_ops",
        "operations": ["transform"],
        "data_operations": ["transform"],
        "reasoning": "User asks for another refinement.",
        "source": "salesforce",
        "destination": "salesforce",
    }
    out = enrich_classification_for_agent_runtime(
        classification,
        intent_state,
        prompt="can you also add US country code to Account Phone",
    )
    assert out["mode"] == "data_ops"
    assert out["operations"] == ["transform"]


def test_enrich_compound_after_prior_sf_job_preserves_classifier_intent():
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "salesforce",
                "destination": "salesforce",
                "source_name": "Production 1",
                "destination_name": "Production 1",
                "job_id": "3a041c8b-7e57-4013-a67a-1bfff0787cb2",
            }
        }
    ]
    classification = {
        "intent": "migrate",
        "mode": "migrate",
        "source": "sql",
        "destination": "sql",
        "source_name": "Test DB",
        "destination_name": "Test DB",
    }
    out = enrich_classification_for_agent_runtime(
        classification,
        {"state": "extraction_completed"},
        prompt=(
            "can you extarct the customer data from databasse Test Db "
            "and migrate them into same database"
        ),
        conversation_history=history,
    )
    assert out["intent"] == "migrate"
    assert out["mode"] == "migrate"
    assert out["source"] == "sql"


def test_enrich_compound_extract_migrate_preserves_classifier_mode():
    classification = {
        "intent": "migrate",
        "mode": "migrate",
        "source": "salesforce",
        "destination": "sql",
        "destination_name": "Test DB",
    }
    p = (
        "extract records first name, last name, email from production 2 "
        "from contact and migrate them to Test DB"
    )
    out = enrich_classification_for_agent_runtime(classification, None, prompt=p)
    assert out["intent"] == "migrate"
    assert out["mode"] == "migrate"


def test_inherit_session_resources_for_start_mapping():
    intent_state = {
        "state": "extraction_completed",
        "active_source": "salesforce",
        "active_destination": "sql",
        "source_name": "Production 1",
        "destination_name": "Test DB",
    }
    llm_result = {
        "source": "unknown",
        "destination": "unknown",
        "intent": "map_data",
        "mode": "map",
        "destination_name": "Test DB",
        "session_action": "continue_active_job",
        "active_job_id": "job-inherit-1",
    }
    out = inherit_session_resources_from_intent_state(
        llm_result,
        intent_state,
        prompt="start mapping",
    )
    assert out["source"] == "salesforce"
    assert out["destination"] == "sql"
    assert out["source_name"] == "Production 1"
    assert out["destination_name"] == "Test DB"


def test_inherit_from_extraction_completed_metadata_when_intent_state_sparse():
    history = [
        {
            "type": "assistant",
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "salesforce",
                "destination": "sql",
                "source_name": "Production 1",
                "destination_name": "Test DB",
            },
        }
    ]
    out = inherit_session_resources_from_intent_state(
        {
            "source": "unknown",
            "destination": "unknown",
            "intent": "map_data",
            "mode": "map",
            "session_action": "continue_active_job",
            "active_job_id": "job-meta-1",
        },
        None,
        conversation_history=history,
        prompt="start mapping",
    )
    assert out["source"] == "salesforce"
    assert out["destination"] == "sql"


def test_latest_extraction_session_prefers_metadata_over_stale_intent_state():
    history = [
        {
            "type": "assistant",
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "salesforce",
                "destination": "salesforce",
                "source_name": "Production 1",
                "destination_name": "Production 1",
                "job_id": "9db15e91-ba04-46bb-8000-47a07322bced",
                "intent_state": {
                    "active_source": "salesforce",
                    "active_destination": "sql",
                    "destination_name": "Test DB",
                    "state": "extraction_completed",
                    "is_active": True,
                },
            },
        }
    ]
    session = latest_extraction_session_for_classifier(history)
    assert session["active_destination"] == "salesforce"
    assert session["destination_name"] == "Production 1"


def test_extraction_session_continuation_respects_classifier_map_destination():
    """Map continuation keeps classifier destination when it names a target."""
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "salesforce",
                "destination": "salesforce",
                "source_name": "Production 1",
                "destination_name": "Production 1",
            }
        }
    ]
    llm_result = {
        "source": "salesforce",
        "destination": "sql",
        "destination_name": "Test DB",
        "intent": "map_data",
        "mode": "map",
    }
    out = apply_extraction_session_continuation(
        {**llm_result, "session_action": "continue_active_job"},
        extraction_meta=history[0]["metadata"],
        prompt="start mapping",
        catalog=ResourceCatalog(),
        conversation_history=history,
    )
    assert out["destination"] == "sql"
    assert out["destination_name"] == "Test DB"
    assert out["source"] == "salesforce"
    assert out["source_name"] == "Production 1"


def test_enrich_keeps_llm_map_data_on_remap_prompt():
    intent_state = {"state": "extraction_completed"}
    classification = {
        "intent": "map_data",
        "mode": "map",
        "operations": None,
        "reasoning": "User requested to remap and exclude the name field.",
        "source": "salesforce",
        "destination": "salesforce",
    }
    out = enrich_classification_for_agent_runtime(
        classification,
        intent_state,
        prompt="can you remap it again and ignore the name field from mapping",
    )
    assert out["intent"] == "map_data"
    assert out["mode"] == "map"
    assert out.get("operations") is None


def test_enrich_no_data_ops_when_completed_but_prompt_unrelated():
    intent_state = {"state": "extraction_completed"}
    classification = {
        "intent": "general",
        "reasoning": "Small talk.",
        "source": "salesforce",
    }
    out = enrich_classification_for_agent_runtime(
        classification,
        intent_state,
        prompt="thanks!",
    )
    assert out.get("mode") != "data_ops"


def test_resource_hints_removed_from_handler_exports():
    import handlers.classification_handler as handler

    assert not hasattr(handler, "apply_resource_hints_from_current_prompt")


def test_sql_extract_map_continuation_pins_from_session_metadata():
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "sql",
                "destination": "sql",
                "source_name": "Test DB",
                "destination_name": "Test DB",
            }
        }
    ]
    llm = {
        "source": "sql",
        "destination": "sql",
        "source_name": "users",
        "destination_name": "Test DB",
        "intent": "map_data",
        "mode": "map",
    }
    prompt = "map the data production 1 org"
    out = apply_extraction_session_continuation(
        {**llm, "session_action": "continue_active_job"},
        extraction_meta=history[0]["metadata"],
        prompt=prompt,
        catalog=ResourceCatalog(),
        conversation_history=history,
    )
    assert out["source"] == "sql"
    assert out["source_name"] == "Test DB"
    assert out["destination_name"] == "Test DB"


def test_map_continuation_keeps_session_destination_pins():
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "salesforce",
                "destination": "salesforce",
                "source_name": "Production 1",
                "destination_name": "Production 1",
            }
        }
    ]
    llm = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "Production 1",
        "destination_name": "Production 1",
        "intent": "map_data",
        "mode": "map",
    }
    prompt = "start the mapping to production 2 org"
    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name=n, environment_type="production")
            for n in ("Production 1", "Production 2")
        ]
    )
    out = apply_extraction_session_continuation(
        {**llm, "session_action": "continue_active_job"},
        extraction_meta=history[0]["metadata"],
        prompt=prompt,
        catalog=cat,
        conversation_history=history,
    )
    assert out["source_name"] == "Production 1"
    assert out["destination_name"] == "Production 1"


def test_map_continuation_without_catalog_keeps_job_pins():
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "salesforce",
                "destination": "salesforce",
                "source_name": "Org Alpha",
                "destination_name": "Org Alpha",
            }
        }
    ]
    llm = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "Org Alpha",
        "destination_name": "Org Alpha",
        "intent": "map_data",
        "mode": "map",
    }
    prompt = "map to Org Beta"
    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name=n, environment_type="production")
            for n in ("Org Alpha", "Org Beta")
        ]
    )
    out = apply_extraction_session_continuation(
        {**llm, "session_action": "continue_active_job"},
        extraction_meta=history[0]["metadata"],
        prompt=prompt,
        catalog=cat,
        conversation_history=history,
    )
    assert out["source_name"] == "Org Alpha"
    assert out["destination_name"] == "Org Alpha"


def test_same_endpoint_signals_preserved_in_classification():
    """LLM must emit explicit destination; no policy overrides."""
    llm_wrong = {
        "source": "gsheets",
        "destination": "salesforce",
        "destination_name": "Contact",
        "intent": "map_data",
        "mode": "map",
        "destination_same_as_source": True,
    }
    signals = merge_endpoint_signals(
        "start the mapping to same sheet",
        llm_wrong,
    )
    assert signals["destination_same_as_source"] is True
