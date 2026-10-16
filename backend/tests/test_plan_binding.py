"""Tests for LLM plan binding (Phase 6)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.planning.binding.contract import EntityCandidate, FieldCandidate, SourceBindLLMOutput
from services.planning.binding.validate import validate_plan_binding
from services.planning.extract_field_clarification import needs_extract_field_clarification
from services.planning.migration_intent_schemas import PromptMigrationIntent



def test_validate_explicit_source_fields_confirmed():
    candidates = [
        EntityCandidate(
            entity_key="users",
            label="Users",
            category="Customer Records",
            fields=[
                FieldCandidate(key="first_name", label="first_name"),
                FieldCandidate(key="last_name", label="last_name"),
                FieldCandidate(key="email", label="email"),
            ],
        ),
    ]
    bind = SourceBindLLMOutput(
        entity_key="users",
        extract_field_scope="explicit",
        field_keys=["first_name", "last_name", "email"],
        confidence=0.95,
    )
    result = asyncio.run(
        validate_plan_binding(
        classification={"source": "sql", "source_name": "Test DB", "destination": "unknown"},
        prompt="extract first name, last name and email from user table from Test DB database",
        parsed_goal={"operations": ["extract"], "source_phrase": "user table"},
        source_bind=bind,
        dest_bind=None,
        source_candidates=candidates,
        dest_candidates=[],
        user_id="test-user",
        )
    )
    assert result.contract.source.entity_key == "users"
    assert result.migration_intent is not None
    assert result.migration_intent.extract_fields_confirmed is True
    assert result.migration_intent.field_hints_strict is True
    assert not result.field_clarification
    assert not needs_extract_field_clarification(
        result.migration_intent,
        "extract first name, last name and email from user table from Test DB database",
    )


def test_validate_skips_source_bind_when_active_job_pinned():
    from services.planning.continuation_policy import pin_source_from_active_job

    cls = {
        "source": "sql",
        "source_name": "users",
        "source_table_name": "users",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "destination_object_api_name": "Contact",
        "active_job_id": "job-123",
        "operations": ["map"],
        "migration_intent": {
            "driving_entity_key": "users",
            "field_hints": ["first_name", "last_name", "email"],
        },
    }
    pinned, _ = pin_source_from_active_job(cls)
    dest_candidates = [
        EntityCandidate(entity_key="Contact", label="Contact"),
        EntityCandidate(entity_key="ContactCleanInfo", label="Contact Clean Info"),
    ]
    from services.planning.binding.contract import DestinationBindLLMOutput

    dest_bind = DestinationBindLLMOutput(
        entity_key="ContactCleanInfo",
        field_scope="none",
        confidence=0.7,
    )
    result = asyncio.run(
        validate_plan_binding(
            classification=cls,
            prompt="map the data to SF 1 Contact object",
            parsed_goal={"operations": ["map"]},
            source_bind=SourceBindLLMOutput(),
            dest_bind=dest_bind,
            source_candidates=[],
            dest_candidates=dest_candidates,
            user_id="test-user",
            source_pinned=pinned,
        )
    )
    assert result.contract.source.entity_key == "users"
    assert not result.source_clarification
    assert result.contract.destination.entity_key == "Contact"


def test_should_skip_source_binding_for_map_continuation():
    from services.planning.continuation_policy import should_skip_source_binding

    cls = {
        "active_job_id": "job-1",
        "operations": ["map"],
        "intent": "map_data",
    }
    assert should_skip_source_binding(cls, {"operations": ["map"]}, prompt="map to Contact")
    assert not should_skip_source_binding(
        cls,
        {"operations": ["extract", "map"]},
        prompt="extract and map",
    )


def test_validate_sf_display_labels_map_to_api_names():
    candidates = [
        EntityCandidate(
            entity_key="Contact",
            label="Contact",
            fields=[
                FieldCandidate(key="FirstName", label="First Name"),
                FieldCandidate(key="LastName", label="Last Name"),
                FieldCandidate(key="Email", label="Email"),
            ],
        ),
    ]
    bind = SourceBindLLMOutput(
        entity_key="Contact",
        extract_field_scope="explicit",
        field_keys=["FirstName", "LastName", "Email"],
        confidence=0.95,
    )
    result = asyncio.run(
        validate_plan_binding(
            classification={"source": "salesforce", "source_name": "SF 2", "destination": "unknown"},
            prompt="extract contact first name, last name and email from SF 2",
            parsed_goal={"operations": ["extract"]},
            source_bind=bind,
            dest_bind=None,
            source_candidates=candidates,
            dest_candidates=[],
            user_id="test-user",
        )
    )
    assert result.migration_intent is not None
    assert result.migration_intent.field_hints == ["FirstName", "LastName", "Email"]
    assert result.migration_intent.extract_fields_confirmed is True


def test_validate_related_contact_account_fields():
    from services.planning.binding.contract import RelationshipTargetCandidate

    candidates = [
        EntityCandidate(
            entity_key="Contact",
            label="Contact",
            fields=[
                FieldCandidate(key="FirstName", label="First Name"),
                FieldCandidate(key="LastName", label="Last Name"),
                FieldCandidate(key="Email", label="Email"),
            ],
            relationship_targets=[
                RelationshipTargetCandidate(
                    related_entity_key="Account",
                    lookup_field="AccountId",
                    label="Account",
                    category="Business Account",
                )
            ],
        ),
    ]
    bind = SourceBindLLMOutput(
        entity_key="Contact",
        extract_field_scope="explicit",
        field_keys=["FirstName", "LastName", "Email"],
        related_entity_key="Account",
        related_field_keys=["name", "phone"],
        confidence=0.95,
    )
    prompt = (
        "extract contact first name, last name, email, account name and account phone "
        "from SF 2 org"
    )

    with patch(
        "services.planning.catalog_grounding.lookup_source_relationship",
        new=AsyncMock(return_value=("Contact", "Account", "AccountId", "Account")),
    ):
        result = asyncio.run(
            validate_plan_binding(
                classification={
                    "source": "salesforce",
                    "source_name": "SF 2",
                    "destination": "unknown",
                },
                prompt=prompt,
                parsed_goal={"operations": ["extract"]},
                source_bind=bind,
                dest_bind=None,
                source_candidates=candidates,
                dest_candidates=[],
                user_id="test-user",
                catalog_ctx={"org_id": "org-1", "connector_type": "salesforce"},
            )
        )

    assert result.migration_intent is not None
    assert result.migration_intent.include_related_data is True
    assert result.migration_intent.parent_entity_key == "Account"
    assert result.migration_intent.related_field_hints == ["name", "phone"]
    assert "Account" in (result.migration_intent.extraction_summary or "")


def test_validate_dest_pinned_sql_user_table():
    from services.planning.entity_pins import pin_destination_from_classification

    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "sql",
        "destination_name": "Test DB",
        "destination_table_name": "user",
        "planner_destination_entity_confirmed": True,
    }
    dest_pinned = pin_destination_from_classification(classification)
    source_bind = SourceBindLLMOutput(
        entity_key="Contact",
        extract_field_scope="all_descriptive",
        confidence=0.9,
    )
    result = asyncio.run(
        validate_plan_binding(
            classification=classification,
            prompt="extract contact from SF1 migrate to Test DB user table",
            parsed_goal={"operations": ["extract", "migrate"]},
            source_bind=source_bind,
            dest_bind=None,
            source_candidates=[
                EntityCandidate(
                    entity_key="Contact",
                    label="Contact",
                    fields=[FieldCandidate(key="Email", label="Email")],
                )
            ],
            dest_candidates=[],
            user_id="test-user",
            dest_pinned=dest_pinned,
        )
    )
    assert result.contract.destination.entity_key == "user"
    assert result.contract.destination.resolution_source == "user_explicit"
    assert result.destination_clarification is None

