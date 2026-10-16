"""Declarative per-step planning requirements — slot specs, assessment, and build gates."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from services.planning.pipeline_step_resolver import PlanContext
from services.planning.schemas import Clarification, ExecutionPlan, PlanContract

logger = logging.getLogger(__name__)

# Canonical requirement slot IDs
SOURCE_CONNECTOR = "source_connector"
DESTINATION_CONNECTOR = "destination_connector"
SOURCE_ENTITY = "source_entity"
DESTINATION_ENTITY = "destination_entity"
EXTRACT_FIELDS = "extract_fields"
ACTIVE_JOB = "active_job"
MAPPING_READY = "mapping_ready"
CREATE_TITLE = "create_title"
CREATE_HEADERS = "create_headers"
JOIN_SPEC = "join_spec"
DEPLOY_ENDPOINTS = "deploy_endpoints"
TARGET_ENTITY = "target_entity"

_SLOT_CLARIFICATIONS: Dict[str, Tuple[str, str]] = {
    SOURCE_CONNECTOR: ("pick_source_connector", "<compose>"),
    DESTINATION_CONNECTOR: ("pick_destination_connector", "<compose>"),
    SOURCE_ENTITY: ("pick_source_entity", "<compose>"),
    DESTINATION_ENTITY: ("pick_destination_entity", "<compose>"),
    EXTRACT_FIELDS: ("pick_extract_fields", "<compose>"),
    CREATE_TITLE: ("create_workbook_name", "<compose>"),
    CREATE_HEADERS: ("pick_create_headers", "<compose>"),
    JOIN_SPEC: ("pick_tab", "<compose>"),
    DEPLOY_ENDPOINTS: ("pick_salesforce_org", "<compose>"),
    TARGET_ENTITY: ("pick_destination_entity", "<compose>"),
}


@dataclass(frozen=True)
class RequirementSlot:
    slot: str
    clarification_type: str
    message: str
    step: str = ""

    @classmethod
    def for_slot(cls, slot_id: str, *, step: str = "") -> RequirementSlot:
        ctype, message = _SLOT_CLARIFICATIONS.get(
            slot_id,
            ("pick_connector_by_label", "<compose>"),
        )
        return cls(slot=slot_id, clarification_type=ctype, message=message, step=step)


@dataclass
class PlanRequirements:
    """Per-step required slots derived from resolved pipeline tokens."""

    by_step: Dict[str, List[RequirementSlot]] = field(default_factory=dict)
    pipeline_tokens: List[str] = field(default_factory=list)

    def all_slots(self) -> List[RequirementSlot]:
        ordered: List[RequirementSlot] = []
        seen: set[str] = set()
        for token in self.pipeline_tokens:
            for slot in self.by_step.get(token, []):
                key = f"{token}:{slot.slot}"
                if key not in seen:
                    seen.add(key)
                    ordered.append(slot)
        return ordered

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_tokens": list(self.pipeline_tokens),
            "by_step": {
                step: [
                    {
                        "slot": s.slot,
                        "clarification_type": s.clarification_type,
                        "message": s.message,
                        "step": s.step,
                    }
                    for s in slots
                ]
                for step, slots in self.by_step.items()
            },
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> PlanRequirements:
        by_step: Dict[str, List[RequirementSlot]] = {}
        steps = raw.get("by_step") if isinstance(raw.get("by_step"), dict) else {}
        for step, slots in steps.items():
            parsed: List[RequirementSlot] = []
            if isinstance(slots, list):
                for item in slots:
                    if isinstance(item, RequirementSlot):
                        parsed.append(item)
                    elif isinstance(item, dict) and item.get("slot"):
                        parsed.append(
                            RequirementSlot(
                                slot=str(item["slot"]),
                                clarification_type=str(
                                    item.get("clarification_type")
                                    or _SLOT_CLARIFICATIONS.get(str(item["slot"]), ("", ""))[0]
                                ),
                                message=str(item.get("message") or ""),
                                step=str(item.get("step") or step),
                            )
                        )
            by_step[str(step)] = parsed
        tokens = list(raw.get("pipeline_tokens") or [])
        if not tokens:
            tokens = list(by_step.keys())
        return cls(by_step=by_step, pipeline_tokens=tokens)


def _prior_steps(pipeline_tokens: List[str], step: str) -> List[str]:
    out: List[str] = []
    for token in pipeline_tokens:
        if token == step:
            break
        out.append(token)
    return out


def _slots_for_extract(*, step: str) -> List[RequirementSlot]:
    return [
        RequirementSlot.for_slot(SOURCE_CONNECTOR, step=step),
        RequirementSlot.for_slot(SOURCE_ENTITY, step=step),
        RequirementSlot.for_slot(EXTRACT_FIELDS, step=step),
    ]


def _slots_for_create(
    classification: Dict[str, Any],
    pipeline_tokens: List[str],
    *,
    step: str,
) -> List[RequirementSlot]:
    from services.planning.create_metadata import create_headers_from_extraction

    slots = [RequirementSlot.for_slot(DESTINATION_CONNECTOR, step=step)]
    cls = dict(classification or {})
    if not create_headers_from_extraction(cls, None, pipeline_tokens=pipeline_tokens):
        slots.append(RequirementSlot.for_slot(CREATE_HEADERS, step=step))
    slots.append(RequirementSlot.for_slot(CREATE_TITLE, step=step))
    return slots


def _slots_for_data_ops(
    pipeline_tokens: List[str],
    *,
    step: str,
    ctx: PlanContext,
) -> List[RequirementSlot]:
    prior = _prior_steps(pipeline_tokens, step)
    if ctx.active_job_id or "extract" in prior:
        return []
    return [RequirementSlot.for_slot(ACTIVE_JOB, step=step)]


def _destination_entity_slot_applies(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when map/migrate should require a destination entity bind (not round-trip to source)."""
    if classification.get("destination_same_as_source"):
        return False
    if classification.get("planner_create_spreadsheet"):
        return False
    from services.planning.plan_scope import read_plan_scope
    from services.planning.schemas import normalize_connector_type

    dest = normalize_connector_type(classification.get("destination") or "")
    if dest == "google_sheets":
        pass  # workbook/tab resolved at entity layer when connector account is known

    goal = parsed_goal or read_plan_scope(classification) or {}
    if goal.get("needs_destination") is False:
        return False
    if goal.get("create_destination"):
        return False
    tokens = classification.get("_pipeline_tokens") or goal.get("operations") or []
    if "create" in tokens:
        return False
    return True


def _slots_for_map(
    classification: Dict[str, Any],
    pipeline_tokens: List[str],
    *,
    step: str,
    ctx: PlanContext,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> List[RequirementSlot]:
    prior = _prior_steps(pipeline_tokens, step)
    slots: List[RequirementSlot] = []
    if not ctx.active_job_id and "extract" not in prior:
        slots.append(RequirementSlot.for_slot(ACTIVE_JOB, step=step))
    slots.append(RequirementSlot.for_slot(DESTINATION_CONNECTOR, step=step))
    if _destination_entity_slot_applies(classification, parsed_goal):
        slots.append(RequirementSlot.for_slot(DESTINATION_ENTITY, step=step))
    if "extract" in prior:
        slots.append(RequirementSlot.for_slot(EXTRACT_FIELDS, step=step))
    return slots


def _slots_for_migrate(
    classification: Dict[str, Any],
    pipeline_tokens: List[str],
    *,
    step: str,
    ctx: PlanContext,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> List[RequirementSlot]:
    prior = _prior_steps(pipeline_tokens, step)
    slots: List[RequirementSlot] = []
    if not ctx.mapping_ready and "map" not in prior:
        slots.append(RequirementSlot.for_slot(MAPPING_READY, step=step))
    slots.append(RequirementSlot.for_slot(DESTINATION_CONNECTOR, step=step))
    if _destination_entity_slot_applies(classification, parsed_goal):
        slots.append(RequirementSlot.for_slot(DESTINATION_ENTITY, step=step))
    # Fresh extract in same plan still needs source entity/fields; continuation uses job pin.
    if "extract" in pipeline_tokens and not ctx.active_job_id:
        if SOURCE_ENTITY not in {s.slot for s in slots}:
            pass  # extract step owns source slots
    return slots


def _slots_for_join(*, step: str) -> List[RequirementSlot]:
    return [
        RequirementSlot.for_slot(SOURCE_CONNECTOR, step=step),
        RequirementSlot.for_slot(JOIN_SPEC, step=step),
    ]


def _slots_for_deploy(*, step: str) -> List[RequirementSlot]:
    return [
        RequirementSlot.for_slot(SOURCE_CONNECTOR, step=step),
        RequirementSlot.for_slot(DESTINATION_CONNECTOR, step=step),
        RequirementSlot.for_slot(DEPLOY_ENDPOINTS, step=step),
    ]


def _slots_for_target_op(*, step: str) -> List[RequirementSlot]:
    return [
        RequirementSlot.for_slot(DESTINATION_CONNECTOR, step=step),
        RequirementSlot.for_slot(TARGET_ENTITY, step=step),
    ]


_STEP_SLOT_BUILDERS = {
    "extract": lambda classification, tokens, step, ctx, goal: _slots_for_extract(step=step),
    "create": lambda classification, tokens, step, ctx, goal: _slots_for_create(
        classification, tokens, step=step
    ),
    "data_ops": lambda classification, tokens, step, ctx, goal: _slots_for_data_ops(
        tokens, step=step, ctx=ctx
    ),
    "map": lambda classification, tokens, step, ctx, goal: _slots_for_map(
        classification, tokens, step=step, ctx=ctx, parsed_goal=goal
    ),
    "migrate": lambda classification, tokens, step, ctx, goal: _slots_for_migrate(
        classification, tokens, step=step, ctx=ctx, parsed_goal=goal
    ),
    "join": lambda classification, tokens, step, ctx, goal: _slots_for_join(step=step),
    "deploy_schema": lambda classification, tokens, step, ctx, goal: _slots_for_deploy(step=step),
    "deploy": lambda classification, tokens, step, ctx, goal: _slots_for_deploy(step=step),
    "upsert": lambda classification, tokens, step, ctx, goal: _slots_for_target_op(step=step),
    "search": lambda classification, tokens, step, ctx, goal: _slots_for_target_op(step=step),
    "update": lambda classification, tokens, step, ctx, goal: _slots_for_target_op(step=step),
}


def derive_plan_requirements(
    pipeline_tokens: List[str],
    *,
    classification: Optional[Dict[str, Any]] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    plan_context: Optional[PlanContext] = None,
) -> PlanRequirements:
    """Per-step required slots from resolved pipeline tokens + plan context."""
    cls = dict(classification or {})
    goal = dict(parsed_goal or {})
    ctx = plan_context or PlanContext.from_classification(cls)
    tokens = [str(t).lower() for t in (pipeline_tokens or []) if str(t).strip()]

    by_step: Dict[str, List[RequirementSlot]] = {}
    for step in tokens:
        builder = _STEP_SLOT_BUILDERS.get(step)
        if builder:
            by_step[step] = builder(cls, tokens, step, ctx, goal)
        else:
            by_step[step] = []

    return PlanRequirements(by_step=by_step, pipeline_tokens=tokens)


def _plan_requirements_cache_stale(
    cached: PlanRequirements,
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    pipeline_tokens: List[str],
) -> bool:
    """True when persisted requirements no longer match PLAN_SCOPE / header provenance."""
    from services.planning.create_metadata import create_headers_from_extraction

    goal_ops = [str(o).lower() for o in (goal.get("operations") or []) if str(o).strip()]
    if "extract" in goal_ops and "extract" not in pipeline_tokens:
        return True
    if create_headers_from_extraction(classification, goal):
        if any(s.slot == CREATE_HEADERS for s in cached.all_slots()):
            return True
    return False


def read_plan_requirements(
    classification: Dict[str, Any],
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> PlanRequirements:
    """Load persisted requirements or derive from pipeline tokens on classification."""
    from services.planning.plan_scope import read_plan_scope, resolve_pipeline_tokens_from_goal

    goal: Dict[str, Any] = dict(parsed_goal or read_plan_scope(classification) or {})
    goal_ops = [str(o).lower() for o in (goal.get("operations") or []) if str(o).strip()]

    tokens: List[str] = []
    if goal.get("operations"):
        tokens = resolve_pipeline_tokens_from_goal(classification, goal)
    else:
        raw_tokens = classification.get("_pipeline_tokens")
        if isinstance(raw_tokens, list) and raw_tokens:
            tokens = [str(t).lower() for t in raw_tokens if str(t).strip()]

    if not tokens:
        raw_tokens = classification.get("_pipeline_tokens")
        if isinstance(raw_tokens, list):
            tokens = [str(t).lower() for t in raw_tokens if str(t).strip()]

    raw = classification.get("_plan_requirements")
    if isinstance(raw, dict) and raw.get("by_step"):
        cached_tokens = [str(t).lower() for t in (raw.get("pipeline_tokens") or []) if str(t).strip()]
        current_tokens = [str(t).lower() for t in (tokens or []) if str(t).strip()]
        if current_tokens and cached_tokens == current_tokens:
            cached_reqs = PlanRequirements.from_dict(raw)
            if not _plan_requirements_cache_stale(cached_reqs, classification, goal, current_tokens):
                logger.debug(
                    "[plan_requirements] cache_hit tokens=%s",
                    current_tokens,
                )
                return cached_reqs

    derived = derive_plan_requirements(
        tokens,
        classification=classification,
        parsed_goal=goal or None,
    )
    logger.debug(
        "[plan_requirements] tokens=%s goal_ops=%s slots=%s",
        list(derived.pipeline_tokens or []),
        goal_ops,
        [s.slot for s in derived.all_slots()],
    )
    return derived


def requirements_need_source_connector(requirements: PlanRequirements) -> bool:
    """True when a resolved source connector instance is required."""
    return any(s.slot == SOURCE_CONNECTOR for s in requirements.all_slots())


def requirements_need_destination_connector(requirements: PlanRequirements) -> bool:
    """True when a resolved destination connector instance is required."""
    return any(s.slot == DESTINATION_CONNECTOR for s in requirements.all_slots())


def requirements_need_source_bind(
    requirements: PlanRequirements,
    classification: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when LLM/catalog source entity binding should run."""
    if classification is not None:
        from services.file_extraction.pending_upload import is_file_source

        if is_file_source(classification):
            return False
    return any(
        s.slot in (SOURCE_ENTITY, SOURCE_CONNECTOR)
        for s in requirements.all_slots()
    )


def requirements_need_dest_bind(requirements: PlanRequirements) -> bool:
    """True when destination entity binding LLM should run (map/migrate cross-system)."""
    return any(s.slot == DESTINATION_ENTITY for s in requirements.all_slots())


def contract_builder_for_slot(slot_id: str) -> Optional[str]:
    """Module path for the contract builder tied to a requirement slot (documentation helper)."""
    return {
        CREATE_TITLE: "services.planning.create_metadata.apply_create_metadata_to_classification",
        CREATE_HEADERS: "services.planning.create_metadata.apply_create_metadata_to_classification",
        SOURCE_ENTITY: "services.planning.extract_contract_from_classification",
        EXTRACT_FIELDS: "services.planning.binding.validate",
        JOIN_SPEC: "services.planning.join_contract",
        DEPLOY_ENDPOINTS: "services.planning.deploy_contract",
        TARGET_ENTITY: "services.planning.upsert_contract",
    }.get(slot_id)


def runtime_preconditions_for_slot(slot_id: str) -> List[str]:
    """TOOL_PRECONDITIONS rules associated with a planning slot (documentation helper)."""
    return {
        ACTIVE_JOB: ["requires_active_job_id"],
        MAPPING_READY: ["requires_mapping"],
        DESTINATION_CONNECTOR: ["requires_destination"],
        DEPLOY_ENDPOINTS: ["requires_schema_deploy_when_pending"],
    }.get(slot_id, [])


def _slot_satisfied(
    slot: RequirementSlot,
    *,
    classification: Dict[str, Any],
    plan_contract: Optional[PlanContract],
    parsed_goal: Dict[str, Any],
    pipeline_tokens: List[str],
    plan_context: PlanContext,
    migration_intent: Any = None,
) -> bool:
    from services.planning.connector_guards import (
        classification_destination_endpoint_resolved,
        classification_source_endpoint_resolved,
    )
    from services.planning.create_metadata import should_materialize_create_step
    from services.planning.plan_extract_gates import (
        extract_fields_locked,
        source_extract_entity_resolved,
    )
    from services.planning.plan_scope import read_plan_scope

    goal = parsed_goal or read_plan_scope(classification)
    slot_id = slot.slot

    if slot_id == SOURCE_CONNECTOR:
        return classification_source_endpoint_resolved(classification)

    if slot_id == DESTINATION_CONNECTOR:
        from services.planning.destination_catalog_validate import (
            destination_connector_authoritatively_confirmed,
            destination_connector_slot_satisfied,
            read_needs_destination,
        )
        from services.planning.schemas import normalize_connector_type

        if not read_needs_destination(classification, goal):
            return True
        from services.planning.resource_catalog import coerce_planning_catalog

        catalog_ref = coerce_planning_catalog(classification=classification)
        if catalog_ref is not None:
            return destination_connector_slot_satisfied(
                classification, catalog_ref, parsed_goal=goal
            )
        dest = normalize_connector_type(classification.get("destination") or "")
        if dest in ("unknown", ""):
            return False
        from services.planning.connector_guards import (
            destination_connector_structurally_pinned,
        )
        from services.planning.destination_catalog_validate import (
            destination_connector_authoritatively_confirmed,
        )

        if destination_connector_structurally_pinned(classification):
            return True
        return destination_connector_authoritatively_confirmed(classification)

    if slot_id == SOURCE_ENTITY:
        from services.file_extraction.pending_upload import is_file_source
        from services.planning.schemas import normalize_connector_type

        if is_file_source(classification):
            return True
        src = normalize_connector_type(classification.get("source") or "")
        if src in ("google_sheets", "gsheets"):
            from services.planning.google_sheets_source import gsheets_workbook_source_resolved
            from services.planning.resource_catalog import coerce_planning_catalog

            cat = coerce_planning_catalog(classification=classification)
            if cat is not None and gsheets_workbook_source_resolved(classification, cat):
                if (classification.get("source_sheet_name") or "").strip():
                    return True
                if classification.get("planner_source_entity_confirmed"):
                    return True
        if (classification.get("source_table_name") or classification.get("source_object_api_name") or "").strip():
            return True
        if (classification.get("source_sheet_name") or "").strip():
            return True
        if classification.get("planner_source_entity_confirmed"):
            return True
        if plan_context.active_job_id and "extract" not in pipeline_tokens:
            return True
        return source_extract_entity_resolved(plan_contract)

    if slot_id == DESTINATION_ENTITY:
        from services.file_extraction.pending_upload import file_source_awaiting_extract
        from services.planning.create_metadata import (
            classification_requests_new_spreadsheet,
            spreadsheet_title_slot_satisfied,
        )
        from services.planning.entity_pins import pinned_entity_key
        from services.planning.schemas import normalize_connector_type

        if file_source_awaiting_extract(classification):
            return classification_destination_endpoint_resolved(classification)
        if classification_requests_new_spreadsheet(classification, goal):
            if spreadsheet_title_slot_satisfied(classification, goal):
                return True
        dest_ct = normalize_connector_type(classification.get("destination") or "")
        if dest_ct in ("google_sheets", "gsheets"):
            from services.planning.google_sheets_destination import (
                gsheets_workbook_destination_resolved,
            )
            from services.planning.resource_catalog import coerce_planning_catalog

            cat = coerce_planning_catalog(classification=classification)
            if cat is not None and gsheets_workbook_destination_resolved(classification, cat):
                if (classification.get("destination_sheet_name") or "").strip():
                    return True
                if classification.get("planner_destination_entity_confirmed"):
                    return True
        if classification.get("planner_destination_entity_confirmed"):
            if pinned_entity_key(classification, role="destination"):
                return True
            if classification_requests_new_spreadsheet(classification, goal):
                return spreadsheet_title_slot_satisfied(classification, goal)
            return False
        pinned = pinned_entity_key(classification, role="destination")
        if pinned:
            return True
        dest = plan_contract.destination if plan_contract else None
        return bool(dest and dest.is_resolved() and float(dest.confidence or 0) >= 0.72)

    if slot_id == EXTRACT_FIELDS:
        from services.file_extraction.pending_upload import file_source_awaiting_extract

        if file_source_awaiting_extract(classification):
            return True
        if extract_fields_locked(classification, migration_intent):
            return True
        if migration_intent is not None:
            if getattr(migration_intent, "extract_fields_confirmed", False):
                return True
            if isinstance(migration_intent, dict):
                raw = migration_intent
            elif hasattr(migration_intent, "model_dump"):
                raw = migration_intent.model_dump(mode="json")
            else:
                raw = {}
            if isinstance(raw, dict) and raw.get("extract_fields_confirmed"):
                return True
            scope = (raw.get("extract_field_scope") if isinstance(raw, dict) else None) or ""
            if (
                scope == "explicit"
                and raw.get("field_hints")
                and raw.get("field_hints_strict")
                and raw.get("extract_fields_confirmed")
            ):
                return True
            from services.planning.extract_field_clarification import (
                needs_extract_field_clarification,
            )

            prompt = (classification.get("original_prompt") or "").strip()
            if not needs_extract_field_clarification(migration_intent, prompt):
                return True
        return False

    if slot_id == ACTIVE_JOB:
        prior = _prior_steps(pipeline_tokens, slot.step or "")
        return bool(plan_context.active_job_id or "extract" in prior)

    if slot_id == MAPPING_READY:
        prior = _prior_steps(pipeline_tokens, slot.step or "")
        if plan_context.mapping_ready or "map" in prior:
            return True
        if plan_context.active_job_id and "migrate" in (slot.step or ""):
            return bool(classification.get("mapping_ready") or plan_context.mapping_ready)
        return False

    if slot_id == CREATE_TITLE:
        from services.planning.create_metadata import (
            _goal_has_create,
            spreadsheet_title_slot_satisfied,
        )

        goal_dict = dict(goal or {})
        if not (_goal_has_create(goal_dict) or classification.get("planner_create_spreadsheet")):
            return True
        return spreadsheet_title_slot_satisfied(classification, goal)

    if slot_id == CREATE_HEADERS:
        from services.planning.create_metadata import (
            _goal_has_create,
            resolve_create_pipeline_tokens,
        )
        from services.planning.source_column_headers import create_headers_slot_satisfied

        goal_dict = dict(goal or {})
        header_tokens = resolve_create_pipeline_tokens(
            classification, goal_dict, pipeline_tokens=pipeline_tokens
        )
        if not (_goal_has_create(goal_dict) or classification.get("planner_create_spreadsheet")):
            return True
        return create_headers_slot_satisfied(
            classification, goal_dict, pipeline_tokens=header_tokens
        )

    if slot_id == JOIN_SPEC:
        ops = [str(o).lower() for o in (goal.get("operations") or [])]
        return bool(
            "join" in ops
            or classification.get("planner_additional_sources")
            or classification.get("multi_source_pipeline")
            or classification.get("join_key")
        )

    if slot_id == DEPLOY_ENDPOINTS:
        return (
            classification_source_endpoint_resolved(classification)
            and classification_destination_endpoint_resolved(classification)
        )

    if slot_id == TARGET_ENTITY:
        from services.planning.entity_pins import pinned_entity_key

        pinned = pinned_entity_key(classification, role="destination")
        if pinned:
            return True
        dest = plan_contract.destination if plan_contract else None
        return bool(dest and dest.is_resolved())

    return True


def assess_plan_requirements(
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Optional[PlanContract] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    plan_context: Optional[PlanContext] = None,
) -> Tuple[List[Dict[str, str]], List[Clarification]]:
    """Return (gap summaries, Clarification objects) for unsatisfied slots in pipeline order."""
    cls = dict(classification or {})
    goal = dict(parsed_goal or {})
    ctx = plan_context or PlanContext.from_classification(cls)
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

    gaps: List[Dict[str, str]] = []
    clarifications: List[Clarification] = []
    seen_types: set[str] = set()
    _ENTITY_RANKED_CLAR_TYPES = frozenset(
        {
            "pick_source_entity",
            "pick_destination_entity",
            "pick_target_entity",
            "pick_extract_fields",
        }
    )

    for slot in requirements.all_slots():
        if _slot_satisfied(
            slot,
            classification=cls,
            plan_contract=plan_contract,
            parsed_goal=goal,
            pipeline_tokens=tokens,
            plan_context=ctx,
            migration_intent=migration_intent,
        ):
            continue
        ctype = (slot.clarification_type or "").strip().lower()
        if ctype in seen_types:
            continue
        seen_types.add(ctype)
        gaps.append(
            {
                "clarification_type": slot.clarification_type,
                "message": slot.message,
                "slot": slot.slot,
                "step": slot.step,
            }
        )
        if (slot.clarification_type or "").strip() not in _ENTITY_RANKED_CLAR_TYPES:
            slot_prompt = slot.message or ""
            if slot_prompt == "<compose>":
                slot_prompt = ""
            clarifications.append(
                Clarification(
                    id=str(uuid.uuid4()),
                    clarification_type=slot.clarification_type,
                    prompt=slot_prompt,
                    options=[],
                    required=True,
                )
            )

    from services.planning.create_metadata import (
        create_headers_from_extraction,
        filter_create_header_clarifications,
        resolve_create_pipeline_tokens,
    )

    header_tokens = resolve_create_pipeline_tokens(cls, goal)
    clarifications = filter_create_header_clarifications(clarifications, cls, goal)
    if create_headers_from_extraction(cls, goal, pipeline_tokens=header_tokens):
        gaps = [g for g in gaps if g.get("slot") != CREATE_HEADERS]

    return gaps, clarifications


def _refine_destination_slot_clarifications(
    clarifications: List[Clarification],
    classification: Dict[str, Any],
    catalog: Any,
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
    prompt: Optional[str] = None,
) -> List[Clarification]:
    """Map generic pick_destination_connector to catalog-specific pickers (e.g. Google account)."""
    if catalog is None:
        return list(clarifications or [])

    from services.planning.connector_guards import is_known_connector
    from services.planning.destination_catalog_validate import (
        needs_google_sheets_account_pick,
    )
    from services.planning.gap_detector import (
        _build_pick_google_account_clarification,
        resolve_destination_connector_clarification,
    )
    from services.planning.schemas import normalize_connector_type

    dest = normalize_connector_type(classification.get("destination") or "")
    if not is_known_connector(dest):
        return list(clarifications or [])

    out = [
        c
        for c in (clarifications or [])
        if c.clarification_type != "pick_destination_connector"
    ]
    if needs_google_sheets_account_pick(classification, catalog):
        specific = _build_pick_google_account_clarification(
            classification, catalog, prompt=prompt
        )
        if specific:
            out.insert(0, specific)
    elif not out and any(
        c.clarification_type == "pick_destination_connector" for c in (clarifications or [])
    ):
        specific = resolve_destination_connector_clarification(
            classification, catalog, prompt
        )
        if specific:
            out.append(specific)
    out = _append_gs_workbook_entity_clarifications(
        out,
        classification,
        catalog,
        prompt=prompt,
        parsed_goal=parsed_goal,
        role="destination",
    )
    return out


def _append_gs_workbook_entity_clarifications(
    clarifications: List[Clarification],
    classification: Dict[str, Any],
    catalog: Any,
    *,
    prompt: Optional[str] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    role: str,
) -> List[Clarification]:
    """Entity-layer GS workbook picker when connector account is pinned but workbook is not."""
    from services.planning.create_metadata import _goal_has_create
    from services.planning.google_sheets_destination import gsheets_workbook_destination_resolved
    from services.planning.google_sheets_source import gsheets_workbook_source_resolved
    from services.planning.gap_detector import (
        _build_pick_source_workbook_clarification,
        _build_pick_workbook_clarification,
    )
    from services.planning.plan_scope import read_plan_scope
    from services.planning.schemas import normalize_connector_type
    from services.planning.destination_catalog_validate import google_sheets_account_slot_satisfied
    from services.planning.source_catalog_validate import google_sheets_source_account_slot_satisfied

    if catalog is None:
        return clarifications

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    out = list(clarifications or [])
    seen_types = {c.clarification_type for c in out}

    if role == "destination":
        dest = normalize_connector_type(classification.get("destination") or "")
        if dest not in ("google_sheets", "gsheets"):
            return out
        if _goal_has_create(goal) or classification.get("planner_create_spreadsheet"):
            return out
        if not google_sheets_account_slot_satisfied(classification, catalog):
            return out
        if gsheets_workbook_destination_resolved(classification, catalog):
            return out
        if "pick_workbook" in seen_types:
            return out
        wb = _build_pick_workbook_clarification(classification, prompt, catalog)
        if wb:
            out = [
                c
                for c in out
                if c.clarification_type
                not in ("pick_destination_entity", "pick_destination_connector")
            ]
            out.append(wb)
        return out

    src = normalize_connector_type(classification.get("source") or "")
    if src not in ("google_sheets", "gsheets"):
        return out
    if not google_sheets_source_account_slot_satisfied(classification, catalog):
        return out
    if gsheets_workbook_source_resolved(classification, catalog):
        return out
    if "pick_source_workbook" in seen_types:
        return out
    wb = _build_pick_source_workbook_clarification(classification, prompt, catalog)
    if wb:
        out = [c for c in out if c.clarification_type not in ("pick_source_entity",)]
        out.append(wb)
    return out


def _refine_source_slot_clarifications(
    clarifications: List[Clarification],
    classification: Dict[str, Any],
    catalog: Any,
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
    prompt: Optional[str] = None,
) -> List[Clarification]:
    from services.planning.connector_guards import is_known_connector
    from services.planning.gap_detector import resolve_source_connector_clarification
    from services.planning.schemas import normalize_connector_type
    from services.planning.source_catalog_validate import needs_google_sheets_account_pick_source

    if catalog is None:
        return list(clarifications or [])

    src = normalize_connector_type(classification.get("source") or "")
    if not is_known_connector(src):
        return list(clarifications or [])

    out = [
        c
        for c in (clarifications or [])
        if c.clarification_type != "pick_source_connector"
    ]
    if needs_google_sheets_account_pick_source(classification, catalog):
        specific = resolve_source_connector_clarification(classification, catalog, prompt)
        if specific:
            out.insert(0, specific)
    elif not out and any(
        c.clarification_type == "pick_source_connector" for c in (clarifications or [])
    ):
        specific = resolve_source_connector_clarification(classification, catalog, prompt)
        if specific:
            out.append(specific)
    return _append_gs_workbook_entity_clarifications(
        out,
        classification,
        catalog,
        prompt=prompt,
        parsed_goal=parsed_goal,
        role="source",
    )


def append_requirement_clarifications_to_plan(
    plan: ExecutionPlan,
    classification: Dict[str, Any],
    requirements: PlanRequirements,
    *,
    plan_contract: Optional[PlanContract] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    catalog: Any = None,
    prompt: Optional[str] = None,
) -> ExecutionPlan:
    """Attach unsatisfied requirement slots to the execution plan (slot-deduped queue)."""
    from services.planning.clarification_queue import merge_plan_clarification_queue

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


def ready_to_build_plan(
    classification: Dict[str, Any],
    *,
    plan_contract: Optional[PlanContract] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    requirements: Optional[PlanRequirements] = None,
) -> bool:
    """True when all required slots across resolved steps are satisfied."""
    reqs = requirements or read_plan_requirements(classification)
    gaps, _ = assess_plan_requirements(
        classification,
        reqs,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
    )
    return not gaps


def project_binding_requirements(requirements: PlanRequirements) -> Dict[str, bool]:
    """Backward-compatible `_binding_requirements` projection."""
    slots = {s.slot for s in requirements.all_slots()}
    return {
        "source": SOURCE_ENTITY in slots or SOURCE_CONNECTOR in slots,
        "destination": DESTINATION_ENTITY in slots or DESTINATION_CONNECTOR in slots,
        "fields": EXTRACT_FIELDS in slots,
    }
