"""S11 plan assembly — entity resolution, field scope, and plan step materialization."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.plan_builder import build_plan_steps, apply_plan_execution_summary
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import ExecutionPlan, PlanContract


async def assemble_migration_plan_tail(
    out: Dict[str, Any],
    plan: ExecutionPlan,
    *,
    user_id: str,
    prompt: str,
    parsed_goal: Dict[str, Any],
    catalog: ResourceCatalog,
    migration_intent: Any,
) -> tuple[Dict[str, Any], ExecutionPlan, Any, Optional[PlanContract]]:
    """
    Resolve entities, field scope, destination entity, and optionally build plan steps.

    Returns (classification, plan, migration_intent, plan_contract).
    """
    from services.planning.connector_guards import classification_destination_endpoint_resolved
    from services.planning.destination_object_enrichment import (
        enrich_destination_target_from_prompt,
        should_defer_destination_object_enrichment_early,
    )
    from services.planning.binding.guards import (
        load_plan_contract,
        should_skip_source_entity_resolver,
    )
    from services.planning.entity_resolver import (
        apply_plan_contract_to_classification,
        resolve_plan_entities,
    )
    from services.planning.extract_field_clarification import resolve_field_clarification_for_plan
    from services.planning.plan_extract_gates import (
        extract_fields_locked,
        needs_source_entity_clarification,
        ready_for_destination_entity_step,
        ready_to_build_file_extract_plan,
        ready_to_build_migration_plan,
    )

    plan_contract = load_plan_contract(plan, out)

    if (
        should_defer_destination_object_enrichment_early(out)
        and classification_destination_endpoint_resolved(out)
        and migration_intent
    ):
        out = await enrich_destination_target_from_prompt(
            out,
            user_id=str(user_id),
            prompt=prompt,
            migration_intent=migration_intent,
        )

    from services.planning.op_requirements import (
        EXTRACT_FIELDS,
        read_plan_requirements,
        requirements_need_dest_bind,
        requirements_need_source_bind,
    )

    plan_reqs = read_plan_requirements(out, parsed_goal=parsed_goal)
    need_source_entity = requirements_need_source_bind(plan_reqs, out)
    need_dest_entity = requirements_need_dest_bind(plan_reqs)

    resolve_sides: list[str] = []
    if need_source_entity and not should_skip_source_entity_resolver(plan_contract):
        if plan_contract is None or not plan_contract.source.is_resolved():
            resolve_sides.append("source")
    if need_dest_entity:
        if plan_contract is None or not plan_contract.destination.is_resolved():
            resolve_sides.append("destination")

    if resolve_sides:
        plan_contract = await resolve_plan_entities(
            out,
            user_id=str(user_id),
            prompt=prompt,
            parsed_goal=parsed_goal,
            catalog=catalog,
            migration_intent=migration_intent,
            existing_contract=plan_contract,
            resolve_sides=tuple(resolve_sides),
        )
    elif plan_contract is None:
        plan_contract = PlanContract()
    out = apply_plan_contract_to_classification(out, plan_contract)
    plan.metadata["plan_contract"] = plan_contract.model_dump(mode="json")

    if need_source_entity and needs_source_entity_clarification(out, plan_contract, parsed_goal):
        if migration_intent:
            plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
        return out, plan, migration_intent, plan_contract

    needs_extract_fields = any(s.slot == EXTRACT_FIELDS for s in plan_reqs.all_slots())
    if needs_extract_fields:
        migration_intent = await resolve_field_clarification_for_plan(
            migration_intent,
            user_id=str(user_id),
            classification=out,
            prompt=prompt,
            plan_contract=plan_contract,
        )

    if (
        needs_extract_fields
        and migration_intent
        and migration_intent.needs_extract_clarification
        and not extract_fields_locked(out, migration_intent)
    ):
        plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
        return out, plan, migration_intent, plan_contract

    if (
        should_defer_destination_object_enrichment_early(out)
        and classification_destination_endpoint_resolved(out)
        and migration_intent
        and migration_intent.extract_fields_confirmed
    ):
        out = await enrich_destination_target_from_prompt(
            out,
            user_id=str(user_id),
            prompt=prompt,
            migration_intent=migration_intent,
        )
        sides: tuple[str, ...] = (
            ("destination",)
            if should_skip_source_entity_resolver(plan_contract)
            else ("source", "destination")
        )
        plan_contract = await resolve_plan_entities(
            out,
            user_id=str(user_id),
            prompt=prompt,
            parsed_goal=parsed_goal,
            catalog=catalog,
            migration_intent=migration_intent,
            existing_contract=plan_contract,
            resolve_sides=sides,
        )
        out = apply_plan_contract_to_classification(out, plan_contract)
        plan.metadata["plan_contract"] = plan_contract.model_dump(mode="json")

    if ready_for_destination_entity_step(
        out, plan_contract, migration_intent, prompt, parsed_goal
    ):
        from services.planning.entity_resolver import build_entity_clarifications

        dest_clars = await build_entity_clarifications(
            plan_contract,
            out,
            user_id=str(user_id),
            prompt=prompt,
            parsed_goal=parsed_goal,
            roles=("destination",),
            plan_migration_intent=(
                migration_intent.model_dump(mode="json")
                if migration_intent and hasattr(migration_intent, "model_dump")
                else (plan.metadata or {}).get("migration_intent")
            ),
        )
        if dest_clars:
            if migration_intent:
                plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
            return out, plan, migration_intent, plan_contract

    ready_to_build = ready_to_build_migration_plan(
        out, plan_contract, migration_intent, prompt, parsed_goal
    ) or ready_to_build_file_extract_plan(out, parsed_goal)
    blocking = plan.blocking_clarifications()
    if ready_to_build and not blocking:
        out["_plan_scope"] = parsed_goal
        if plan.metadata is None:
            plan.metadata = {}
        plan.metadata["goal"] = parsed_goal
        plan = build_plan_steps(out, prompt=prompt, plan=plan)
        if migration_intent:
            plan.metadata["migration_intent"] = migration_intent.model_dump(mode="json")
        plan = apply_plan_execution_summary(
            plan,
            migration_intent=migration_intent,
            plan_contract=plan_contract,
        )
    else:
        from services.planning.observability import log_planner_debug

        blocking = plan.blocking_clarifications()
        event = (
            "assemble_tail_build_blocked_by_clarifications"
            if ready_to_build and blocking
            else "assemble_tail_no_plan_steps"
        )
        log_planner_debug(
            event,
            classification=out,
            plan=plan,
            extra={
                "ready_to_build": ready_to_build,
                "blocking_count": len(blocking),
                "ready_for_destination_entity": ready_for_destination_entity_step(
                    out, plan_contract, migration_intent, prompt, parsed_goal
                ),
                "needs_source_entity": needs_source_entity_clarification(
                    out, plan_contract, parsed_goal
                ),
                "needs_extract_clarification": bool(
                    migration_intent and getattr(migration_intent, "needs_extract_clarification", False)
                ),
                "extract_fields_confirmed": bool(
                    migration_intent and getattr(migration_intent, "extract_fields_confirmed", False)
                ),
                "source_entity_key": getattr(plan_contract.source, "entity_key", None)
                if plan_contract
                else None,
                "dest_entity_key": getattr(plan_contract.destination, "entity_key", None)
                if plan_contract
                else None,
            },
        )

    return out, plan, migration_intent, plan_contract
