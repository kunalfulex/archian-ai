"""E2E-style: binding contract prevents resolve_plan_entities on source."""
from __future__ import annotations

import asyncio


def test_plan_assembly_skips_source_resolve_when_llm_bind(monkeypatch):
    from services.intent.plan_assembly import assemble_migration_plan_tail
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.planning.resource_catalog import ResourceCatalog
    from services.planning.schemas import ExecutionPlan, PlanContract, PlanEntityRef

    resolve_calls: list[tuple[str, ...]] = []

    async def _track_resolve(_classification, **kwargs):
        resolve_calls.append(tuple(kwargs.get("resolve_sides") or ("source", "destination")))
        return kwargs.get("existing_contract") or PlanContract()

    monkeypatch.setattr(
        "services.planning.entity_resolver.resolve_plan_entities",
        _track_resolve,
    )
    async def _noop_field_clarification(mi, **kwargs):
        return mi

    monkeypatch.setattr(
        "services.planning.extract_field_clarification.resolve_field_clarification_for_plan",
        _noop_field_clarification,
    )

    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="salesforce",
            entity_key="Contact",
            resolution_source="llm_bind",
            confidence=0.92,
        ),
        destination=PlanEntityRef(
            connector_type="google_sheets",
            entity_key="Leads",
            resolution_source="llm_bind",
            confidence=0.9,
        ),
    )
    mi = PromptMigrationIntent(
        driving_entity_key="Contact",
        extract_fields_confirmed=True,
        field_hints=["Email"],
        extract_field_scope="explicit",
    )
    out = {
        "source": "salesforce",
        "destination": "google_sheets",
        "source_name": "Prod",
        "destination_name": "Leads",
        "source_connector_id": "sf1",
        "destination_connector_id": "gs1",
        "planner_source_entity_confirmed": True,
        "planner_destination_entity_confirmed": True,
        "extract_fields_confirmed": True,
        "_plan_scope": {"operations": ["extract", "map", "migrate"]},
    }
    plan = ExecutionPlan(metadata={"plan_contract": contract.model_dump(mode="json")})

    asyncio.run(
        assemble_migration_plan_tail(
            out,
            plan,
            user_id="u1",
            prompt="extract contacts email to leads",
            parsed_goal={"operations": ["extract", "map", "migrate"]},
            catalog=ResourceCatalog(),
            migration_intent=mi,
        )
    )

    assert not any("source" in sides for sides in resolve_calls), resolve_calls
