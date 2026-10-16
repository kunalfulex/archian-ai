"""Single clarification queue: build from slots, dedupe by slot, prune when satisfied."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from services.planning.op_requirements import (
    CREATE_HEADERS,
    CREATE_TITLE,
    DESTINATION_CONNECTOR,
    DESTINATION_ENTITY,
    EXTRACT_FIELDS,
    SOURCE_ENTITY,
    PlanRequirements,
    RequirementSlot,
    _slot_satisfied,
    assess_plan_requirements,
)
from services.planning.schemas import Clarification, ExecutionPlan, ClarificationOption

# Ranked slots: assess records gaps only; builders here materialize full Clarification objects.
_RANKED_CLARIFICATION_SLOTS = frozenset({EXTRACT_FIELDS, SOURCE_ENTITY, DESTINATION_ENTITY})

# Clar types materialized by ranked builders (must not be wiped by fresh supersede).
RANKED_SLOT_CLARIFICATION_TYPES = frozenset(
    {
        "pick_extract_fields",
        "pick_source_entity",
        "pick_destination_entity",
        "migration_intent_separate_pipelines",
        "extract_entity_role",
        "extract_related_vs_secondary",
    }
)


def open_ranked_slot_clarification_types(
    clarifications: Any,
) -> set[str]:
    """Return ranked-slot clarification types still open on a pending payload."""
    types: set[str] = set()
    for clar in clarifications or []:
        if isinstance(clar, dict):
            ctype = (clar.get("clarification_type") or "").strip()
            required = clar.get("required", True)
            answered = bool(clar.get("selected_option_id"))
        else:
            ctype = (getattr(clar, "clarification_type", None) or "").strip()
            required = bool(getattr(clar, "required", True))
            answered = bool(getattr(clar, "selected_option_id", None))
        if not ctype or not required or answered:
            continue
        if ctype in RANKED_SLOT_CLARIFICATION_TYPES:
            types.add(ctype)
    return types

# Clar types that satisfy the destination connector slot (mutually exclusive).
_DESTINATION_CONNECTOR_CLAR_TYPES = frozenset(
    {
        "pick_destination_connector",
        "pick_google_account",
        "pick_salesforce_org",
        "pick_sql_connector",
        "pick_connector_by_label",
    }
)

_DESTINATION_ENTITY_CLAR_TYPES = frozenset(
    {
        "pick_workbook",
        "pick_workbook_ambiguous",
        "pick_destination_entity",
        "pick_tab",
    }
)

_SOURCE_ENTITY_CLAR_TYPES = frozenset(
    {
        "pick_source_workbook",
        "pick_source_entity",
    }
)

# Resume answers that must not re-run PLAN_SCOPE (connector/workbook/entity binding only).
METADATA_CLARIFICATION_TYPES = frozenset(
    {
        "pick_google_account",
        "create_workbook_name",
        "pick_destination_connector",
        "pick_workbook",
        "pick_source_workbook",
        "pick_tab",
        "pick_workbook_ambiguous",
        "pick_destination_entity",
        "pick_source_entity",
    }
)

_CLAR_TYPE_TO_SLOT: Dict[str, str] = {
    "pick_source_connector": "source_connector",
    "pick_destination_connector": DESTINATION_CONNECTOR,
    "pick_google_account": DESTINATION_CONNECTOR,
    "pick_salesforce_org": DESTINATION_CONNECTOR,
    "pick_sql_connector": DESTINATION_CONNECTOR,
    "pick_workbook": "destination_entity",
    "pick_workbook_ambiguous": "destination_entity",
    "pick_source_workbook": "source_entity",
    "pick_connector_by_label": DESTINATION_CONNECTOR,
    "pick_source_entity": "source_entity",
    "pick_destination_entity": "destination_entity",
    "pick_extract_fields": "extract_fields",
    "create_workbook_name": CREATE_TITLE,
    "pick_create_headers": CREATE_HEADERS,
    "pick_tab": "destination_entity",
}


def clarification_slot(clarification_type: str) -> Optional[str]:
    """Map a clarification type to its requirement slot (for dedupe / prune)."""
    return _CLAR_TYPE_TO_SLOT.get((clarification_type or "").strip())


def is_metadata_clarification_resume(selection_types: Optional[List[str]]) -> bool:
    """True when resume only answered connector/workbook/title clars — scope stays fixed."""
    types = {str(t).strip() for t in (selection_types or []) if str(t).strip()}
    return bool(types) and types <= METADATA_CLARIFICATION_TYPES


def _coerce_migration_intent(migration_intent: Any) -> Any:
    if migration_intent is None:
        return None
    if isinstance(migration_intent, dict):
        try:
            from services.planning.migration_intent_schemas import PromptMigrationIntent

            return PromptMigrationIntent.model_validate(migration_intent)
        except Exception:
            return None
    return migration_intent


def _extract_fields_slot_unsatisfied(
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
) -> bool:
    """True when op_requirements still requires extract field confirmation."""
    from services.planning.pipeline_step_resolver import PlanContext

    cls = dict(classification or {})
    goal = dict(parsed_goal or {})
    ctx = PlanContext.from_classification(cls)
    tokens = requirements.pipeline_tokens or list(requirements.by_step.keys())
    intent = _coerce_migration_intent(migration_intent)

    for slot in requirements.all_slots():
        if slot.slot != EXTRACT_FIELDS:
            continue
        return not _slot_satisfied(
            slot,
            classification=cls,
            plan_contract=plan_contract,
            parsed_goal=goal,
            pipeline_tokens=tokens,
            plan_context=ctx,
            migration_intent=intent,
        )
    return False


def clarification_from_extract_fields_intent(migration_intent: Any) -> Optional[Clarification]:
    """
    Build pick_extract_fields Clarification from finalized migration_intent.

    Ranked slot builder — catalog-backed options, not assess_plan_requirements.
    """
    import uuid

    intent = _coerce_migration_intent(migration_intent)
    if intent is None:
        return None
    if intent.extract_fields_confirmed:
        return None
    if not intent.needs_extract_clarification and not any(
        (a.ambiguity_type or "") == "pick_extract_fields"
        for a in (intent.extract_ambiguities or [])
    ):
        return None

    amb = next(
        (
            a
            for a in (intent.extract_ambiguities or [])
            if (a.ambiguity_type or "") == "pick_extract_fields"
        ),
        None,
    )
    prompt_text = (amb.prompt if amb else "").strip()
    entity_label = intent.driving_entity_label or intent.resolved_driving_key() or "source"
    if not prompt_text:
        prompt_text = (
            f"Which columns or fields should we extract from {entity_label}? "
            "Pick a suggestion below or type column names in chat."
        )

    from services.planning.extract_field_clarification import build_field_clarification_options

    options = build_field_clarification_options(
        intent.suggested_field_hints or [],
        entity_label=entity_label,
    )
    return Clarification(
        id=str(uuid.uuid4()),
        clarification_type="pick_extract_fields",
        prompt=prompt_text,
        options=[
            ClarificationOption(id=o["id"], label=o["label"]) for o in options if o.get("id")
        ],
        required=True,
    )


def build_intent_slot_clarifications(migration_intent: Any) -> List[Clarification]:
    """Materialize extract-role ambiguities and separate-pipelines from migration intent."""
    intent = _coerce_migration_intent(migration_intent)
    if intent is None:
        return []
    from services.planning.migration_intent import (
        clarification_from_separate_pipelines_intent,
        clarifications_from_migration_intent_ambiguities,
    )

    out: List[Clarification] = []
    separate = clarification_from_separate_pipelines_intent(intent)
    if separate:
        out.append(separate)
    out.extend(
        clarifications_from_migration_intent_ambiguities(
            intent, exclude_types=frozenset({"pick_extract_fields"})
        )
    )
    return out


def _entity_slot_unsatisfied(
    slot_id: str,
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
) -> bool:
    from services.planning.pipeline_step_resolver import PlanContext

    cls = dict(classification or {})
    goal = dict(parsed_goal or {})
    ctx = PlanContext.from_classification(cls)
    tokens = requirements.pipeline_tokens or list(requirements.by_step.keys())
    intent = _coerce_migration_intent(migration_intent)

    for slot in requirements.all_slots():
        if slot.slot != slot_id:
            continue
        return not _slot_satisfied(
            slot,
            classification=cls,
            plan_contract=plan_contract,
            parsed_goal=goal,
            pipeline_tokens=tokens,
            plan_context=ctx,
            migration_intent=intent,
        )
    return False


async def build_entity_slot_clarifications(
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    user_id: str,
    prompt: str,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
) -> List[Clarification]:
    """Catalog-backed entity pickers when source/destination entity slots are open."""
    if plan_contract is None:
        from services.planning.binding.guards import plan_contract_from_classification

        plan_contract = plan_contract_from_classification(classification)
    if plan_contract is None:
        return []

    need_source = _entity_slot_unsatisfied(
        SOURCE_ENTITY,
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
    )
    need_dest = _entity_slot_unsatisfied(
        DESTINATION_ENTITY,
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
    )
    if not need_source and not need_dest:
        return []

    roles: List[str] = []
    if need_source:
        roles.append("source")
    if need_dest:
        roles.append("destination")

    from services.planning.entity_resolver import build_entity_clarifications

    intent_ref = migration_intent
    if isinstance(migration_intent, dict):
        intent_ref = migration_intent
    elif migration_intent is not None and hasattr(migration_intent, "model_dump"):
        intent_ref = migration_intent.model_dump(mode="json")

    return await build_entity_clarifications(
        plan_contract,
        classification,
        user_id=str(user_id),
        prompt=prompt or "",
        parsed_goal=parsed_goal,
        roles=tuple(roles),
        plan_migration_intent=intent_ref,
    )


def build_ranked_slot_clarifications(
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
) -> List[Clarification]:
    """Materialize ranked-slot clarifications (options from catalog / intent)."""
    out: List[Clarification] = []
    if _extract_fields_slot_unsatisfied(
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
    ):
        clar = clarification_from_extract_fields_intent(migration_intent)
        if clar:
            out.append(clar)
    out.extend(build_intent_slot_clarifications(migration_intent))
    return out


_GS_WORKBOOK_CLAR_TYPES = frozenset({"pick_workbook", "pick_workbook_ambiguous"})
_GENERIC_DEST_ENTITY_TAB_FALLBACK = frozenset({"pick_destination_entity"})


def _merge_open_clarification_queues(
    queue: List[Clarification],
    ranked: List[Clarification],
    *,
    answered_slots: Set[str],
    answered_types: Set[str],
) -> List[Clarification]:
    """Merge assess + ranked queues; ranked wins for overlapping slots."""
    queue_has_gs_workbook = any(
        (c.clarification_type or "") in _GS_WORKBOOK_CLAR_TYPES for c in queue
    )
    if queue_has_gs_workbook:
        ranked = [
            c
            for c in ranked
            if (c.clarification_type or "") not in _GENERIC_DEST_ENTITY_TAB_FALLBACK
        ]
    ranked_slots = {
        clarification_slot(c.clarification_type or "")
        for c in ranked
        if clarification_slot(c.clarification_type or "")
    }
    filtered_queue = [
        c
        for c in queue
        if clarification_slot(c.clarification_type or "") not in ranked_slots
    ]

    open_queue: List[Clarification] = []
    seen_open_slots: Set[str] = set()
    for clar in filtered_queue + ranked:
        slot = clarification_slot(clar.clarification_type or "")
        if clar.clarification_type in answered_types:
            continue
        if slot and slot in answered_slots:
            continue
        if slot:
            if slot in seen_open_slots:
                continue
            seen_open_slots.add(slot)
        open_queue.append(clar)
    return open_queue


def build_requirement_clarifications(
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    catalog: Any = None,
    prompt: Optional[str] = None,
) -> List[Clarification]:
    """
    Build ordered, slot-deduped clarifications for unsatisfied requirement slots.

    Single source: assess_plan_requirements + destination refinement + header policy.
    Does not emit pick_create_headers for extract-backed create flows.
    """
    from services.planning.op_requirements import (
        _refine_destination_slot_clarifications,
        _refine_source_slot_clarifications,
    )

    _, req_clars = assess_plan_requirements(
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
    )
    catalog_ref = catalog or classification.get("_planning_catalog")
    from services.planning.resource_catalog import coerce_planning_catalog

    catalog_ref = coerce_planning_catalog(catalog_ref, classification)
    req_clars = _refine_destination_slot_clarifications(
        req_clars,
        classification,
        catalog_ref,
        parsed_goal=parsed_goal,
        prompt=prompt,
    )
    req_clars = _refine_source_slot_clarifications(
        req_clars,
        classification,
        catalog_ref,
        parsed_goal=parsed_goal,
        prompt=prompt,
    )

    seen_slots: Set[str] = set()
    out: List[Clarification] = []
    for clar in req_clars:
        slot = clarification_slot(clar.clarification_type or "")
        if slot and slot in seen_slots:
            continue
        if slot:
            seen_slots.add(slot)
        out.append(clar)
    return out


def planner_clarifications_from_requirements(
    classification: Dict[str, Any],
    catalog: Any,
    *,
    prompt: Optional[str] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    plan_contract: Any = None,
) -> List[Clarification]:
    """Slot-deduped clarifications from op_requirements + catalog pickers."""
    from services.planning.op_requirements import read_plan_requirements
    from services.planning.plan_scope import read_plan_scope

    goal = parsed_goal if parsed_goal is not None else read_plan_scope(classification)
    reqs = read_plan_requirements(classification, parsed_goal=goal)
    return build_requirement_clarifications(
        classification,
        reqs,
        plan_contract=plan_contract,
        parsed_goal=goal,
        migration_intent=migration_intent,
        catalog=catalog,
        prompt=prompt,
    )


def prune_plan_clarifications(
    plan: ExecutionPlan,
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
) -> ExecutionPlan:
    """
    Drop open clarifications whose slots are already satisfied; dedupe open clars by slot.

    Preserves answered clarifications for session history.
    """
    from services.planning.pipeline_step_resolver import PlanContext

    if not plan.clarifications:
        return plan

    cls = dict(classification or {})
    goal = dict(parsed_goal or {})
    ctx = PlanContext.from_classification(cls)
    tokens = requirements.pipeline_tokens or list(requirements.by_step.keys())

    if migration_intent is None:
        from services.intent.migration_intent_view import read_migration_intent_dict

        migration_intent = read_migration_intent_dict(cls) or None
    if isinstance(migration_intent, dict):
        try:
            from services.planning.migration_intent_schemas import PromptMigrationIntent

            migration_intent = PromptMigrationIntent.model_validate(migration_intent)
        except Exception:
            pass
    if plan_contract is None:
        from services.planning.binding.guards import plan_contract_from_classification

        plan_contract = plan_contract_from_classification(cls)

    from services.planning.create_metadata import create_headers_from_extraction
    from services.planning.destination_catalog_validate import (
        destination_connector_slot_satisfied,
    )
    from services.planning.resource_catalog import coerce_planning_catalog

    catalog_ref = coerce_planning_catalog(classification=cls)

    slots_by_id: Dict[str, RequirementSlot] = {}
    for s in requirements.all_slots():
        if s.slot not in slots_by_id:
            slots_by_id[s.slot] = s

    def _slot_obj_for_clar(clar: Clarification) -> Optional[RequirementSlot]:
        slot_id = clarification_slot(clar.clarification_type or "")
        if not slot_id:
            return None
        return slots_by_id.get(slot_id)

    def _is_satisfied(clar: Clarification) -> bool:
        slot_id = clarification_slot(clar.clarification_type or "")
        if slot_id == CREATE_HEADERS and create_headers_from_extraction(cls, goal):
            return True
        if slot_id == DESTINATION_CONNECTOR:
            if catalog_ref is not None and destination_connector_slot_satisfied(
                cls, catalog_ref, parsed_goal=goal
            ):
                return True
            if clar.clarification_type in _DESTINATION_CONNECTOR_CLAR_TYPES:
                slot_obj = _slot_obj_for_clar(clar)
                if slot_obj and _slot_satisfied(
                    slot_obj,
                    classification=cls,
                    plan_contract=plan_contract,
                    parsed_goal=goal,
                    pipeline_tokens=tokens,
                    plan_context=ctx,
                    migration_intent=migration_intent,
                ):
                    return True
                return False
        slot_obj = _slot_obj_for_clar(clar)
        if not slot_obj:
            if slot_id == CREATE_HEADERS:
                return create_headers_from_extraction(cls, goal)
            return False
        return _slot_satisfied(
            slot_obj,
            classification=cls,
            plan_contract=plan_contract,
            parsed_goal=goal,
            pipeline_tokens=tokens,
            plan_context=ctx,
            migration_intent=migration_intent,
        )

    answered = [c for c in plan.clarifications if c.selected_option_id]
    answered_dest_connector = any(
        clarification_slot(c.clarification_type or "") == DESTINATION_CONNECTOR
        for c in answered
    )
    dest_slot_satisfied = bool(
        catalog_ref is not None
        and destination_connector_slot_satisfied(cls, catalog_ref, parsed_goal=goal)
    )
    open_clars = [c for c in plan.clarifications if c.required and not c.selected_option_id]

    pruned_open: List[Clarification] = []
    seen_open_slots: Set[str] = set()
    for clar in open_clars:
        slot = clarification_slot(clar.clarification_type or "")
        if slot == DESTINATION_CONNECTOR and (answered_dest_connector or dest_slot_satisfied):
            continue
        if _is_satisfied(clar):
            continue
        if slot:
            if slot in seen_open_slots:
                continue
            seen_open_slots.add(slot)
        pruned_open.append(clar)

    plan = plan.model_copy(deep=True)
    plan.clarifications = answered + pruned_open
    if not plan.blocking_clarifications() and plan.status == "awaiting_clarification":
        plan.status = "awaiting_approval"
    return plan


def merge_plan_clarification_queue(
    plan: ExecutionPlan,
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    catalog: Any = None,
    prompt: Optional[str] = None,
    extra_ranked: Optional[List[Clarification]] = None,
) -> ExecutionPlan:
    """Rebuild open clar queue from requirements; keep answered picks only."""
    if plan.steps:
        return prune_plan_clarifications(
            plan,
            classification,
            requirements,
            plan_contract=plan_contract,
            parsed_goal=parsed_goal,
            migration_intent=migration_intent,
        )

    answered = [c for c in plan.clarifications if c.selected_option_id]
    answered_slots: Set[str] = set()
    answered_types: Set[str] = set()
    for c in answered:
        answered_types.add(c.clarification_type)
        slot = clarification_slot(c.clarification_type or "")
        if slot:
            answered_slots.add(slot)

    queue = build_requirement_clarifications(
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
        catalog=catalog,
        prompt=prompt,
    )

    ranked = build_ranked_slot_clarifications(
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
    )
    if extra_ranked:
        ranked = ranked + list(extra_ranked)

    open_queue = _merge_open_clarification_queues(
        queue,
        ranked,
        answered_slots=answered_slots,
        answered_types=answered_types,
    )

    plan = plan.model_copy(deep=True)
    plan.clarifications = answered + open_queue
    plan = prune_plan_clarifications(
        plan,
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
    )
    if plan.blocking_clarifications():
        plan.status = "awaiting_clarification"
    return plan


async def merge_plan_clarification_queue_async(
    plan: ExecutionPlan,
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    user_id: str,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    catalog: Any = None,
    prompt: Optional[str] = None,
) -> ExecutionPlan:
    """Async merge including catalog-backed entity slot builders."""
    entity_ranked = await build_entity_slot_clarifications(
        classification,
        requirements,
        user_id=str(user_id),
        prompt=prompt or "",
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
    )
    return merge_plan_clarification_queue(
        plan,
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
        catalog=catalog,
        prompt=prompt,
        extra_ranked=entity_ranked,
    )


# Public API used by op_requirements.append_requirement_clarifications_to_plan
__all__ = [
    "METADATA_CLARIFICATION_TYPES",
    "append_requirement_clarifications_to_plan",
    "build_entity_slot_clarifications",
    "build_intent_slot_clarifications",
    "build_ranked_slot_clarifications",
    "build_requirement_clarifications",
    "clarification_from_extract_fields_intent",
    "clarification_slot",
    "is_metadata_clarification_resume",
    "merge_plan_clarification_queue",
    "merge_plan_clarification_queue_async",
    "open_ranked_slot_clarification_types",
    "RANKED_SLOT_CLARIFICATION_TYPES",
    "planner_clarifications_from_requirements",
    "prune_plan_clarifications",
]


def append_requirement_clarifications_to_plan(
    plan: ExecutionPlan,
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    catalog: Any = None,
    prompt: Optional[str] = None,
) -> ExecutionPlan:
    """See op_requirements.append_requirement_clarifications_to_plan."""
    return merge_plan_clarification_queue(
        plan,
        classification,
        requirements,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
        catalog=catalog,
        prompt=prompt,
    )
