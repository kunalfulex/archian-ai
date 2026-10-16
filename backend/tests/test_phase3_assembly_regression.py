"""Phase 3 regression — assembly dead-ends, supersede, and clar continuation."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.pipeline_turn import merge_fresh_supersede_into_classification, supersede_pending_for_fresh_turn
from services.intent.pipeline_turn_routing import PipelineTurnDecision, resolve_pipeline_turn
from services.planning.create_metadata import (
    should_materialize_create_step,
    spreadsheet_title_slot_satisfied,
    sync_create_assembly_readiness,
)
from services.planning.plan_builder import build_plan_steps
from services.planning.plan_scope import sync_scope_into_classification
from tests.plan_scope_helpers import with_plan_scope


def test_create_title_confirmed_via_destination_name_materializes_create_step():
    """Regression: title on destination_name but not planner_new_spreadsheet_name."""
    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "Kunal GS",
        "spreadsheet_headers": ["Name", "Email"],
    }
    classification = sync_scope_into_classification(
        {
            "destination": "google_sheets",
            "intent": "create",
            "planner_create_spreadsheet": True,
            "planner_spreadsheet_title_confirmed": True,
            "destination_name": "Kunal GS",
            "planner_spreadsheet_headers": ["Name", "Email"],
            "_destination_endpoint_clarified": True,
            "_plan_scope": goal,
        },
        goal,
        ["create"],
    )
    synced = sync_create_assembly_readiness(classification, goal)
    assert spreadsheet_title_slot_satisfied(synced, goal)
    assert should_materialize_create_step(synced, goal)
    plan = build_plan_steps(synced, prompt="create google sheet Kunal GS")
    assert len(plan.steps) == 1
    assert plan.steps[0].parameters.get("tool_id") == "google_sheets_create_from_prompt"
    assert plan.steps[0].parameters.get("spreadsheet_title") == "Kunal GS"


def test_sf_to_sql_migrate_builds_extract_map_migrate_steps():
    goal = {"operations": ["extract", "map", "migrate"]}
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Contact",
            "destination": "sql",
            "destination_name": "Test DB",
            "destination_table_name": "users",
            "intent": "migrate",
            "mode": "migrate",
            "planner_source_entity_confirmed": True,
            "planner_destination_entity_confirmed": True,
        },
        operations=["extract", "map", "migrate"],
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["extract", "map", "migrate"],
    )
    plan = build_plan_steps(
        classification,
        prompt="extract Contact from SF 1 and migrate to users in Test DB",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["extract_salesforce", "map_fields", "start_migration"]


def test_fresh_migrate_supersedes_open_create_clarification():
    from services.intent.pipeline_turn_routing import PipelineTurnDecision

    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "create google sheet",
                    "clarifications": [
                        {
                            "id": "c1",
                            "clarification_type": "pick_google_account",
                            "required": True,
                        }
                    ],
                },
            }
        }
    ]

    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value.choices = [
                type(
                    "C",
                    (),
                    {
                        "message": type(
                            "M",
                            (),
                            {
                                "content": (
                                    '{"branch": "fresh", "confidence": 0.95,'
                                    '"reasoning_short": "new migrate goal"}'
                                )
                            },
                        )()
                    },
                )()
            ]
            return await resolve_pipeline_turn(
                "extract Contact from SF 1 and migrate to users in Test DB",
                user_id="u1",
                conversation_history=history,
            )

    decision = asyncio.run(_run())
    assert decision.branch == "fresh"
    supersede = supersede_pending_for_fresh_turn(history)
    out = merge_fresh_supersede_into_classification({"intent": "migrate"}, supersede)
    assert out.get("_fresh_supersedes_pending") is True


def test_typed_clar_continuation_routes_to_planning_not_s01():
    from services.intent.turn_type_routing import TurnTypeRoutingDecision
    from services.planning.clarification_turn import classify_architect_turn

    planning_decision = PipelineTurnDecision(
        branch="planning_continuation",
        confidence=0.92,
        decision_source="llm",
    )
    resumed = {"plan_status": "awaiting_approval", "needs_clarification": False}

    async def _run():
        with patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=TurnTypeRoutingDecision(
                turn_family="pipeline",
                confidence=0.9,
                decision_source="llm",
            ),
        ), patch(
            "services.intent.pipeline_turn_routing.resolve_pipeline_turn",
            new_callable=AsyncMock,
            return_value=planning_decision,
        ), patch(
            "services.intent.pipeline_turn.continue_planning_from_prompt",
            new_callable=AsyncMock,
            return_value=resumed,
        ) as mock_continue, patch(
            "services.intent.session_continuation.resolve_session_continuation",
            new_callable=AsyncMock,
        ) as mock_s01:
            out = await classify_architect_turn(
                "SF 2",
                "user-1",
                conversation_history=[
                    {
                        "metadata": {
                            "event_type": "planner_clarification_required",
                            "planner_pending": {
                                "original_prompt": "extract from sf 1",
                                "clarifications": [{"id": "c1", "required": True}],
                            },
                        }
                    }
                ],
            )
            mock_continue.assert_called_once()
            mock_s01.assert_not_called()
            return out

    out = asyncio.run(_run())
    assert out is resumed


def test_sf_to_sql_assemble_tail_builds_steps(stub_llm_goal_parser):
    """Binding + assemble tail: SF Contact → SQL users when entities are pinned."""
    import asyncio
    import uuid

    from services.intent.plan_assembly import assemble_migration_plan_tail
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.planning.plan_scope import sync_scope_into_classification
    from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef, SqlConnectionRef
    from services.planning.schemas import ExecutionPlan
    from tests.plan_scope_helpers import with_plan_scope

    goal = {"operations": ["extract", "map", "migrate"], "needs_destination": True}
    migration_intent = PromptMigrationIntent.model_validate(
        {
            "driving_entity_key": "Contact",
            "driving_entity_label": "Contact",
            "extract_field_scope": "all_descriptive",
            "extract_fields_confirmed": True,
            "needs_extract_clarification": False,
        }
    )
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Contact",
            "destination": "sql",
            "destination_name": "Test DB",
            "destination_table_name": "users",
            "intent": "migrate",
            "planner_source_entity_confirmed": True,
            "planner_destination_entity_confirmed": True,
            "_source_endpoint_clarified": True,
            "_destination_endpoint_clarified": True,
            "migration_intent": migration_intent.model_dump(mode="json"),
        },
        operations=["extract", "map", "migrate"],
    )
    classification = sync_scope_into_classification(
        classification,
        {**goal, **classification["_plan_scope"]},
        ["extract", "map", "migrate"],
    )
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        status="draft",
        metadata={
            "classification": dict(classification),
            "goal": goal,
            "migration_intent": migration_intent.model_dump(mode="json"),
        },
    )
    catalog = ResourceCatalog(
        salesforce_connections=[SalesforceConnectionRef(name="SF 1")],
        sql_connections=[SqlConnectionRef(name="Test DB")],
    )

    async def _run():
        return await assemble_migration_plan_tail(
            classification,
            plan,
            user_id="test-user",
            prompt="extract Contact from SF 1 and migrate to users in Test DB",
            parsed_goal=goal,
            catalog=catalog,
            migration_intent=migration_intent,
        )

    out, plan, _, _ = asyncio.run(_run())
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["extract_salesforce", "map_fields", "start_migration"]
