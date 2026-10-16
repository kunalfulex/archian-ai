"""S07/S08 respect plan_contract from binding — no catalog re-pick."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.pipeline import run_intent_pipeline
from services.intent.types import IntentStepId
from services.planning.schemas import PlanContract, PlanEntityRef


def test_s07_uses_binding_contract_without_catalog_resolve():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            entity_key="users",
            catalog_label="users",
            resolution_source="llm_bind",
            confidence=0.92,
        ),
        migration_intent={
            "driving_entity_key": "users",
            "field_hints": ["email", "first_name"],
            "extract_field_scope": "explicit",
            "extract_fields_confirmed": True,
            "field_hints_strict": True,
        },
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "plan_contract": contract.model_dump(mode="json"),
        "migration_intent": contract.migration_intent,
        "extract_fields_confirmed": True,
    }

    async def _run():
        with patch(
            "services.intent.steps.entity._resolve_source_via_catalog",
            new=AsyncMock(side_effect=AssertionError("catalog resolve must not run")),
        ):
            return await run_intent_pipeline(
                "extract email and first name from users",
                classification=cls,
                phase="post_planning",
            )

    result = asyncio.run(_run())
    assert IntentStepId.S07_ENTITY in result.steps_completed
    assert result.state.source_entity.entity_key == "users"
    assert result.state.source_entity.resolution_source == "llm_bind"


def test_s08_confirms_fields_when_binding_locked():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            entity_key="users",
            resolution_source="llm_bind",
            confidence=0.9,
        ),
        migration_intent={
            "driving_entity_key": "users",
            "field_hints": ["email"],
            "extract_field_scope": "explicit",
            "extract_fields_confirmed": True,
        },
    )
    cls = {
        "source": "sql",
        "plan_contract": contract.model_dump(mode="json"),
        "migration_intent": contract.migration_intent,
    }

    async def _run():
        return await run_intent_pipeline(
            "extract email from users",
            classification=cls,
            phase="post_planning",
        )

    result = asyncio.run(_run())
    assert IntentStepId.S08_FIELD_SCOPE in result.steps_completed
    assert result.state.fields.confirmed is True
    assert "email" in result.state.fields.hints


def test_resolve_side_entity_returns_binding_pin():
    from services.planning.entity_resolver import _resolve_side_entity

    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            entity_key="users",
            resolution_source="llm_bind",
            confidence=0.9,
        )
    )
    cls = {
        "source": "sql",
        "source_connector_id": "sql-1",
        "plan_contract": contract.model_dump(mode="json"),
    }

    async def _run():
        return await _resolve_side_entity(
            user_id="u1",
            classification=cls,
            role="source",
            prompt="extract users",
            parsed_goal={"operations": ["extract"]},
        )

    ref = asyncio.run(_run())
    assert ref.entity_key == "users"
    assert ref.resolution_source == "llm_bind"
