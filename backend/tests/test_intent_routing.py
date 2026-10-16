from services.planning.agent_runtime_enrichment import enrich_classification_for_agent_runtime
from services.planning.intent_routing import is_map_migrate_classification


def test_migrate_classification_is_map_migrate():
    c = {"intent": "migrate_data", "mode": "migrate"}
    assert is_map_migrate_classification(c)


def test_extract_with_data_ops_mode_not_map_migrate():
    c = {"intent": "extract", "mode": "extract", "operations": ["extract"]}
    assert not is_map_migrate_classification(c)


def test_transform_is_not_map_migrate():
    c = {"intent": "transform", "mode": "data_ops", "operations": ["transform"]}
    assert not is_map_migrate_classification(c)


def test_enrich_preserves_classifier_migrate_intent():
    state = {"state": "extraction_completed"}
    c = {
        "intent": "migrate_data",
        "mode": "migrate",
        "operations": ["extract"],
        "source": "salesforce",
        "destination_name": "SF 2",
        "active_job_id": "job-1",
    }
    out = enrich_classification_for_agent_runtime(c, state, prompt="start the migration")
    assert out["intent"] == "migrate_data"
    assert out["mode"] == "migrate"


def test_enrich_preserves_classifier_map_intent():
    c = {
        "intent": "map_data",
        "mode": "map",
        "source": "salesforce",
        "destination_name": "SF 2",
        "destination_object_api_name": "Contact",
    }
    out = enrich_classification_for_agent_runtime(
        c, {"state": "extraction_completed"}, prompt="map it to Contact oject"
    )
    assert out["intent"] == "map_data"
    assert out["mode"] == "map"
