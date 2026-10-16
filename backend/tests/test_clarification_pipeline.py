"""Tests for unified clarification pipeline (source stages)."""
import asyncio

from services.planning.clarification_pipeline import (
    PlannerSession,
    advance_planner_pipeline,
    apply_intent_source_entity_to_classification,
)
from services.planning.migration_intent_schemas import ExtractAmbiguity, PromptMigrationIntent
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef, SqlConnectionRef
from services.planning.schemas import ExecutionPlan, PlanContract, PlanEntityRef


def _migrate_scope():
    return {
        "operations": ["extract", "migrate"],
        "needs_source": True,
        "needs_destination": True,
    }


def _with_plan_scope(classification: dict) -> dict:
    scope = _migrate_scope()
    out = dict(classification)
    out.setdefault("operations", scope["operations"])
    out["_plan_scope"] = scope
    out["_pipeline_tokens"] = list(scope["operations"])
    return out


def test_apply_intent_source_entity_without_intent_does_not_keyword_pin():
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "_source_endpoint_clarified": True,
    }
    out = apply_intent_source_entity_to_classification(
        cls, None, prompt="deploy user data to salesforce"
    )
    assert "source_table_name" not in out


def test_apply_intent_source_entity_sets_sql_table():
    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        driving_entity_label="users",
    )
    cls = {"source": "sql", "source_name": "Test DB", "_source_endpoint_clarified": True}
    out = apply_intent_source_entity_to_classification(cls, intent)
    assert out["source_table_name"] == "users"


def test_advance_pipeline_emits_extract_fields_clarification():
    from unittest.mock import AsyncMock, patch

    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        driving_entity_label="users",
        needs_extract_clarification=True,
        extract_field_scope="vague",
        extract_ambiguities=[
            ExtractAmbiguity(ambiguity_type="pick_extract_fields", prompt="Which columns?")
        ],
    )
    scope = _migrate_scope()
    classification = _with_plan_scope({
        "source": "sql",
        "source_name": "Test DB",
        "source_connector_id": "sql-1",
        "_source_endpoint_clarified": True,
        "destination": "salesforce",
        "intent": "migrate",
    })
    session = PlannerSession(
        classification=classification,
        plan=ExecutionPlan(
            plan_id="p1",
            metadata={"migration_intent": intent.model_dump(mode="json")},
        ),
        migration_intent=intent,
        prompt="deploy user data to salesforce",
        parsed_goal=scope,
    )
    session.classification = apply_intent_source_entity_to_classification(
        session.classification, intent
    )
    catalog = ResourceCatalog(
        sql_connections=[SqlConnectionRef(name="Test DB", connector_id="sql-1")]
    )
    with patch(
        "services.planning.binding.ensure.ensure_plan_binding_if_needed",
        new=AsyncMock(
            return_value=(session.classification, session.plan, intent, None)
        ),
    ):
        result = asyncio.run(
            advance_planner_pipeline(session, user_id="user-1", catalog=catalog)
        )
    blocking = result.plan.blocking_clarifications()
    assert blocking
    assert blocking[0].clarification_type == "pick_extract_fields"


def test_advance_pipeline_emits_sql_connector_clarification():
    session = PlannerSession(
        classification=_with_plan_scope({
            "source": "sql",
            "destination": "salesforce",
            "intent": "migrate",
        }),
        plan=ExecutionPlan(plan_id="p1"),
        prompt="deploy user data to salesforce",
    )
    catalog = ResourceCatalog(
        sql_connections=[
            SqlConnectionRef(name="Test DB", connector_id="sql-1"),
            SqlConnectionRef(name="UAT DB", connector_id="sql-2"),
        ]
    )
    result = asyncio.run(
        advance_planner_pipeline(session, user_id="user-1", catalog=catalog)
    )
    blocking = result.plan.blocking_clarifications()
    assert blocking
    assert blocking[0].clarification_type == "pick_sql_connector"


def test_extract_fields_complete_when_confirmed():
    from services.planning.clarification_pipeline import _extract_fields_complete

    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        extract_fields_confirmed=True,
    )
    session = PlannerSession(
        classification={"extract_fields_confirmed": True},
        plan=ExecutionPlan(plan_id="p1"),
        migration_intent=intent,
        prompt="deploy user data to salesforce",
    )
    assert _extract_fields_complete(session) is True


def test_extract_fields_incomplete_when_ambiguity_present():
    from services.planning.clarification_pipeline import _extract_fields_complete

    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        needs_extract_clarification=True,
        extract_ambiguities=[
            ExtractAmbiguity(ambiguity_type="pick_extract_fields", prompt="Which columns?")
        ],
    )
    session = PlannerSession(
        classification={},
        plan=ExecutionPlan(plan_id="p1"),
        migration_intent=intent,
        prompt="deploy user data to salesforce",
    )
    assert _extract_fields_complete(session) is False


def test_plan_assembly_tail_skips_extract_reclarification_when_fields_confirmed():
    """After column pick, stored migration intent must not block plan assembly."""
    from unittest.mock import AsyncMock, patch

    confirmed_intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        extract_fields_confirmed=True,
        field_hints=["email", "name"],
        field_hints_strict=True,
        needs_extract_clarification=False,
        extract_ambiguities=[],
    )
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            entity_key="users",
            confidence=0.95,
            resolution_source="plan_pinned",
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            entity_key="Contact",
            confidence=0.95,
            resolution_source="plan_pinned",
        ),
    )
    plan = ExecutionPlan(
        plan_id="p1",
        metadata={
            "migration_intent": confirmed_intent.model_dump(mode="json"),
            "plan_contract": contract.model_dump(mode="json"),
        },
    )
    session = PlannerSession(
        classification={
            "source": "sql",
            "source_name": "Test DB",
            "source_connector_id": "sql-1",
            "source_table_name": "users",
            "destination": "salesforce",
            "destination_name": "SF 1",
            "destination_connector_id": "sf-1",
            "destination_object_api_name": "Contact",
            "intent": "extract",
            "operations": ["extract", "map", "migrate"],
            "extract_fields_confirmed": True,
            "planner_destination_entity_confirmed": True,
            "_source_endpoint_clarified": True,
            "_destination_endpoint_clarified": True,
            "_destination_connector_validated": True,
            "planner_destination_confirmed": True,
        },
        plan=plan,
        prompt="deploy user data to salesforce",
        parsed_goal={"operations": ["extract", "map", "migrate"]},
    )
    catalog = ResourceCatalog(
        sql_connections=[SqlConnectionRef(name="Test DB", connector_id="sql-1")],
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1", token_id="sf-1"),
        ],
    )

    async def _noop_entity_clars(*_a, **_k):
        return []

    with patch(
        "services.planning.entity_resolver.resolve_plan_entities",
        new=AsyncMock(return_value=contract),
    ), patch(
        "services.planning.clarification_queue.build_entity_slot_clarifications",
        new=AsyncMock(side_effect=_noop_entity_clars),
    ), patch(
        "services.planning.extract_field_clarification.resolve_field_clarification_for_plan",
        new=AsyncMock(return_value=confirmed_intent),
    ), patch(
        "services.planning.destination_object_enrichment.enrich_destination_target_from_prompt",
        new=AsyncMock(side_effect=lambda cls, **k: cls),
    ):
        result = asyncio.run(
            advance_planner_pipeline(
                session,
                user_id="user-1",
                catalog=catalog,
            )
        )

    assert len(result.plan.steps or []) > 0
    assert result.plan.status == "awaiting_approval"
