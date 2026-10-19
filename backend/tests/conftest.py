"""Shared pytest fixtures for backend tests."""
from __future__ import annotations

import os

# Must be set before collection imports SQLConnectorService (module-level singleton).
# Generate at runtime so the repo never embeds a Fernet key (gitleaks).
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")
if not os.environ.get("SQL_CONNECTOR_ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet

    os.environ["SQL_CONNECTOR_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest


@pytest.fixture(autouse=True)
def _plan_scope_fallback_for_direct_build_tests(monkeypatch, request):
    """
    Unit tests that call build_plan_steps() without orchestrator may omit _plan_scope.
    Allow parse_goal fallback except in tests that assert MissingPlanScopeError.
    """
    node_path = str(getattr(request.node, "path", "") or "")
    if "test_plan_scope_required" in node_path:
        return

    import services.planning.goal_parser as goal_parser

    original = goal_parser.resolve_plan_scope_from_classification

    def _wrapped(classification, prompt=None, *, plan=None, allow_fallback=False, **kwargs):
        has_goal = bool(classification.get("_plan_scope"))
        if not has_goal and plan is not None:
            meta = getattr(plan, "metadata", None) or {}
            has_goal = bool(meta.get("goal") if isinstance(meta, dict) else None)
        if has_goal or allow_fallback:
            return original(
                classification,
                prompt,
                plan=plan,
                allow_fallback=allow_fallback,
                **kwargs,
            )
        return original(
            classification,
            prompt,
            plan=plan,
            allow_fallback=True,
            **kwargs,
        )

    monkeypatch.setattr(goal_parser, "resolve_plan_scope_from_classification", _wrapped)


@pytest.fixture
def stub_llm_goal_parser(monkeypatch):
    """Rule-based goal parsing and no DB for destination enrichment in planner tests."""

    async def _rule_based(classification, prompt, **kwargs):
        from services.planning.goal_parser import _apply_plan_scope_llm_data, parse_goal
        from services.planning.schemas import normalize_connector_type
        from services.planning.scope_validator import validate_plan_scope_goal

        goal = parse_goal(classification, prompt)
        cls = dict(classification or {})
        ops = [str(o).lower() for o in (goal.get("operations") or [])]

        src = normalize_connector_type(cls.get("source") or "")
        dest = normalize_connector_type(cls.get("destination") or "")
        dest_bound = bool(
            (cls.get("destination_spreadsheet_id") or "").strip()
            or cls.get("planner_destination_confirmed")
        )

        scope_patch: dict = {}
        if "extract" in ops and src and dest and src != dest and dest == "google_sheets":
            if cls.get("planner_create_spreadsheet"):
                scope_patch = {
                    "pipeline_ops": ["extract", "create", "map", "migrate"],
                    "create_destination": True,
                    "needs_destination": True,
                }
                title = (
                    (cls.get("planner_new_spreadsheet_name") or "").strip()
                    or (cls.get("planner_suggested_spreadsheet_title") or "").strip()
                )
                if title:
                    scope_patch["spreadsheet_title"] = title
            elif not dest_bound:
                scope_patch = {"pipeline_ops": ["extract", "map", "migrate"]}
            else:
                scope_patch = {"pipeline_ops": ["extract", "map", "migrate"]}

        if scope_patch:
            goal = _apply_plan_scope_llm_data(goal, scope_patch)

        ops = [str(o).lower() for o in (goal.get("operations") or [])]
        dest = normalize_connector_type(cls.get("destination") or "")
        src = normalize_connector_type(cls.get("source") or "")
        if (
            any(o in ops for o in ("map", "migrate"))
            and dest
            and dest not in ("unknown", "file")
            and dest != src
        ):
            goal = _apply_plan_scope_llm_data(goal, {"needs_destination": True})

        return validate_plan_scope_goal(goal, classification)

    async def _noop_destination(classification, **kwargs):
        return classification

    async def _stub_resolve_plan_entities(classification, **kwargs):
        from services.planning.schemas import PlanContract, PlanEntityRef, normalize_connector_type

        dest_key = (classification.get("destination_object_api_name") or "").strip() or None
        dest_type = normalize_connector_type(classification.get("destination") or "unknown")
        if not dest_key and dest_type == "sql":
            dest_key = (classification.get("destination_table_name") or "").strip() or None
        if (
            not dest_key
            and dest_type in ("google_sheets", "gsheets")
            and classification.get("planner_create_spreadsheet")
        ):
            dest_key = (classification.get("planner_new_spreadsheet_name") or "new_sheet").strip()
        if not dest_key and dest_type in ("google_sheets", "gsheets"):
            if (classification.get("destination_spreadsheet_id") or "").strip():
                dest_key = (
                    (classification.get("destination_sheet_name") or "").strip()
                    or (classification.get("destination_name") or "").strip()
                    or "Sheet1"
                )
        src_key = (classification.get("source_object_api_name") or "").strip() or None
        src_type = normalize_connector_type(classification.get("source") or "unknown")
        if not src_key and src_type == "salesforce":
            src_key = "Contact"
        contract = PlanContract(
            source=PlanEntityRef(
                connector_type=src_type,
                display_name=classification.get("source_name"),
                entity_key=src_key,
                catalog_label=src_key,
                entity_label=src_key,
                resolution_source="user_explicit" if src_key else "pending",
                confidence=0.95 if src_key else 0.0,
            ),
            destination=PlanEntityRef(
                connector_type=dest_type,
                display_name=classification.get("destination_name"),
                entity_key=dest_key,
                catalog_label=dest_key,
                entity_label=dest_key,
                resolution_source="user_explicit" if dest_key else "pending",
                confidence=0.95 if dest_key else 0.0,
            ),
        )
        return contract

    async def _noop_build_entity_clarifications(*_a, **_k):
        return []

    monkeypatch.setattr(
        "services.planning.goal_parser.enrich_scope_from_llm",
        _rule_based,
    )
    monkeypatch.setattr(
        "services.planning.destination_object_enrichment.enrich_destination_target_from_prompt",
        _noop_destination,
    )
    monkeypatch.setattr(
        "services.planning.entity_resolver.resolve_plan_entities",
        _stub_resolve_plan_entities,
    )
    monkeypatch.setattr(
        "services.planning.entity_resolver.build_entity_clarifications",
        _noop_build_entity_clarifications,
    )

    async def _stub_migration_intent(_classification):
        return None

    async def _stub_field_clarification(migration_intent, **kwargs):
        return migration_intent

    monkeypatch.setattr(
        "services.planning.extract_field_clarification.resolve_field_clarification_for_plan",
        _stub_field_clarification,
    )

    async def _stub_run_plan_binding_pipeline(
        out,
        plan,
        *,
        user_id,
        prompt,
        parsed_goal,
        catalog,
        conversation_id=None,
        conversation_history=None,
    ):
        from services.planning.entity_resolver import apply_plan_contract_to_classification
        from services.planning.migration_intent_schemas import PromptMigrationIntent
        from services.planning.schemas import PlanContract, PlanEntityRef, normalize_connector_type

        dest_key = (out.get("destination_object_api_name") or "").strip() or None
        dest_type = normalize_connector_type(out.get("destination") or "unknown")
        if not dest_key and dest_type == "sql":
            dest_key = (out.get("destination_table_name") or "").strip() or None
        if (
            not dest_key
            and dest_type in ("google_sheets", "gsheets")
            and out.get("planner_create_spreadsheet")
        ):
            dest_key = (out.get("planner_new_spreadsheet_name") or "new_sheet").strip()
        if not dest_key and dest_type in ("google_sheets", "gsheets"):
            if (out.get("destination_spreadsheet_id") or "").strip():
                dest_key = (
                    (out.get("destination_sheet_name") or "").strip()
                    or (out.get("destination_name") or "").strip()
                    or "Sheet1"
                )
        src_key = (
            (out.get("source_object_api_name") or out.get("source_table_name") or "").strip()
            or None
        )
        src_type = normalize_connector_type(out.get("source") or "unknown")
        if not src_key and src_type == "salesforce":
            src_key = "Contact"

        mi_raw = dict(out.get("migration_intent") or {})
        if src_key and not mi_raw.get("driving_entity_key"):
            mi_raw.setdefault("driving_entity_key", src_key)
            mi_raw.setdefault("driving_entity_label", src_key)
        if not mi_raw.get("extract_field_scope"):
            mi_raw.setdefault("extract_field_scope", "all_descriptive")
        mi_raw.setdefault("extract_fields_confirmed", True)
        mi_raw.setdefault("needs_extract_clarification", False)

        migration_intent = None
        if mi_raw:
            try:
                migration_intent = PromptMigrationIntent.model_validate(mi_raw)
            except Exception:
                migration_intent = None

        plan_contract = PlanContract(
            source=PlanEntityRef(
                connector_type=src_type,
                display_name=out.get("source_name"),
                entity_key=src_key,
                catalog_label=src_key,
                entity_label=src_key,
                resolution_source="llm_bind" if src_key else "pending",
                confidence=0.92 if src_key else 0.0,
            ),
            destination=PlanEntityRef(
                connector_type=dest_type,
                display_name=out.get("destination_name"),
                entity_key=dest_key,
                catalog_label=dest_key,
                entity_label=dest_key,
                resolution_source="llm_bind" if dest_key else "pending",
                confidence=0.92 if dest_key else 0.0,
            ),
            migration_intent=mi_raw or None,
        )
        out = apply_plan_contract_to_classification(out, plan_contract)
        plan.metadata = dict(plan.metadata or {})
        plan.metadata["plan_contract"] = plan_contract.model_dump(mode="json")
        if migration_intent:
            plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
            out["migration_intent"] = migration_intent.model_dump(mode="json")
        return out, plan, migration_intent, plan_contract

    monkeypatch.setattr(
        "services.planning.binding.pipeline.run_plan_binding_pipeline",
        _stub_run_plan_binding_pipeline,
    )
    monkeypatch.setattr(
        "services.pipeline_learning.service.PipelineLearningService.enabled",
        staticmethod(lambda: False),
    )

    async def _identity_compose(clar, **kwargs):
        return clar

    async def _noop_enrich_plan(plan, **kwargs):
        return plan

    monkeypatch.setattr(
        "services.planning.clarification_compose.compose_clarification_prompt",
        _identity_compose,
    )
    monkeypatch.setattr(
        "services.planning.clarification_compose.enrich_blocking_clarification_on_plan",
        _noop_enrich_plan,
    )

    async def _noop_dest_catalog_llm(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
        _noop_dest_catalog_llm,
    )
