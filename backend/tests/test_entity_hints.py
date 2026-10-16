"""Tests for shared entity hint assembly and user-intent priority."""
from __future__ import annotations

from services.intent.job_context import JobContext, ResourcePin
from services.planning.continuation_policy import apply_continuation_endpoint_pins_from_job
from services.planning.entity_hints import build_retrieve_entity_hints
from services.planning.entity_scoring import score_catalog_entity


def test_user_named_contact_beats_job_source_account_context():
    """Explicit user destination entity (tier-1 hints) outranks job source fallback."""
    contact_score = score_catalog_entity(
        entity_key="Contact",
        label="Contact",
        prompt="map the extracted data to SF 2 org",
        hints=["Contact"],
        semantics={"primary": {"category": "People"}},
        connector_type="salesforce",
    )
    account_score = score_catalog_entity(
        entity_key="Account",
        label="Account",
        prompt="map the extracted data to SF 2 org",
        hints=["Contact"],
        semantics={"primary": {"category": "Companies"}},
        connector_type="salesforce",
        context_hints=["Contact"],
    )
    assert contact_score.score > account_score.score


def test_job_source_fallback_when_user_vague():
    built = build_retrieve_entity_hints(
        role="destination",
        classification={"destination": "salesforce", "destination_name": "SF 2"},
        parsed_goal={"operations": ["map"], "destination_phrase": "SF 2 org"},
        migration_intent={"driving_entity_key": "Contact", "driving_entity_label": "Contact"},
        source_entity_key="Contact",
        source_entity_label="Contact",
    )
    assert "Contact" in built.required_entity_keys
    assert built.context_hints
    assert not any(h.lower() == "sf 2 org" for h in built.user_hints)


def test_new_destination_phrase_clears_stale_entity_pins_on_endpoint_rebind():
    """When PLAN_SCOPE names a new org, stale entity pins must not carry over."""
    job = JobContext(
        job_id="job-1",
        pipeline_stage="extracted",
        last_completed_step="extract",
        mapping_ready=False,
        source=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
            connector_id="sf-1",
        ),
    )
    cls = {
        "destination_object_api_name": "Contact",
        "planner_destination_entity_confirmed": True,
        "destination": "salesforce",
        "destination_name": "SF 1",
    }
    goal = {"operations": ["map"], "destination_phrase": "SF 2 org"}
    out = apply_continuation_endpoint_pins_from_job(cls, goal, job)
    assert out.get("planner_destination_entity_confirmed") is None
    assert out.get("destination_object_api_name") is None
    assert out.get("destination") is None
    assert out.get("destination_phrase") == "SF 2 org"
    assert out.get("_plan_scope") == goal
