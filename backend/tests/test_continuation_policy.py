from services.planning.continuation_policy import apply_extraction_session_continuation
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef


def test_continuation_keeps_job_pins_without_classifier_dest():
    """Session pins apply when classifier did not name a destination (no prompt keyword override)."""
    catalog = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="Org A"),
            SalesforceConnectionRef(name="Org B"),
        ]
    )
    classification = {"source": "unknown", "destination": "unknown", "intent": "map_data"}
    extraction_meta = {
        "source_type": "salesforce",
        "source_name": "Org A",
        "destination": "salesforce",
        "destination_name": "Org A",
    }
    out = apply_extraction_session_continuation(
        classification,
        extraction_meta=extraction_meta,
        prompt="map to Org B",
        catalog=catalog,
    )
    assert out["source"] == "salesforce"
    assert out["source_name"] == "Org A"
    assert out["destination_name"] == "Org A"


def test_continuation_inherits_map_destination_from_mapping_started_event():
    catalog = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )
    history = [
        {
            "metadata": {
                "event_type": "mapping_started",
                "job_id": "job-1",
                "destination": "salesforce",
                "destination_name": "SF 2",
            }
        },
        {"type": "user", "text": "use contact object"},
    ]
    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "unknown",
        "intent": "map_data",
        "mode": "map",
    }
    extraction_meta = {
        "source_type": "salesforce",
        "source_name": "SF 1",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "job_id": "job-1",
    }
    out = apply_extraction_session_continuation(
        classification,
        extraction_meta=extraction_meta,
        prompt="use contact object",
        catalog=catalog,
        conversation_history=history,
    )
    assert out["destination_name"] == "SF 2"
    assert out["active_job_id"] == "job-1"


def test_compound_extract_migrate_clears_stale_active_job_id():
    catalog = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "intent": "extract",
        "mode": "extract",
    }
    extraction_meta = {
        "source_type": "salesforce",
        "source_name": "SF 1",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "job_id": "job-stale",
    }
    prompt = (
        "extract first name, last name, email from contact object SF 1 org "
        "and migrate them to SF 2 org"
    )
    out = apply_extraction_session_continuation(
        classification,
        extraction_meta=extraction_meta,
        prompt=prompt,
        catalog=catalog,
    )
    assert "active_job_id" not in out


def test_swapped_sf_orgs_compound_prompt_skips_session_pin():
    """After SF1→SF2 migration, SF2→SF1 compound prompt must not inherit prior source."""
    catalog = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "extract",
        "mode": "extract",
    }
    extraction_meta = {
        "source_type": "salesforce",
        "source_name": "SF 1",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "job_id": "job-stale",
    }
    prompt = (
        "extract first name, last name, email, account name and account phone "
        "from contact object SF 2 org and migrate them to SF 1 org"
    )
    out = apply_extraction_session_continuation(
        classification,
        extraction_meta=extraction_meta,
        prompt=prompt,
        catalog=catalog,
    )
    assert out["source_name"] == "SF 2"
    assert out["destination_name"] == "SF 1"
    assert "active_job_id" not in out
