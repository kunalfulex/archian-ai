"""Extended precondition rules (Agent Runtime v4 P0.5)."""
from __future__ import annotations

from agent.preconditions import check_preconditions
from agent.session import AgentSession


def test_requires_destination_for_map_and_migrate():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="migrate",
        active_job_id="job-1",
        mapping_id="map-1",
    )
    assert not check_preconditions("start_migration", session, {}).ok
    session.destination = "salesforce"
    assert check_preconditions("start_migration", session, {}).ok


def test_requires_schema_deploy_when_pending():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="migrate",
        classification_snapshot={
            "schema_gate_missing_fields": ["CustomField__c"],
        },
    )
    assert not check_preconditions("metadata_deploy_salesforce", session, {}).ok
    session.classification_snapshot["schema_deploy_approved"] = True
    assert check_preconditions("metadata_deploy_salesforce", session, {}).ok


def test_no_schema_precondition_when_no_missing_fields():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="migrate",
        classification_snapshot={"schema_gate_missing_fields": []},
    )
    assert check_preconditions("schema_gate_propose", session, {}).ok
