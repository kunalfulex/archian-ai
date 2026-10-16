"""Ordered gap detection for intent pipeline S10."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from services.intent.state import IntentState
from services.intent.types import DestinationNeed
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import Clarification

_COMPOSE = "<compose>"


@dataclass(frozen=True)
class GapRule:
    clarification_type: str
    message: str
    applies: Callable[[IntentState, Dict[str, Any]], bool]


def _needs_source_connector(state: IntentState, cls: Dict[str, Any]) -> bool:
    if cls.get("_endpoints_resolved") or state.source.resolved:
        return False
    src = (state.source_type or cls.get("source") or "").lower()
    if src == "file" or cls.get("_file_upload_pinned"):
        return False
    return bool(src and src != "unknown" and not state.source.resolved)


def _needs_destination_connector(state: IntentState, cls: Dict[str, Any]) -> bool:
    need = state.needs_destination or DestinationNeed.NONE.value
    return need == DestinationNeed.REQUIRED.value and not state.destination.resolved


def _needs_source_entity(state: IntentState, cls: Dict[str, Any]) -> bool:
    from services.file_extraction.pending_upload import is_file_source
    from services.planning.op_requirements import SOURCE_ENTITY, assess_plan_requirements, read_plan_requirements

    if is_file_source(cls):
        return False

    reqs = read_plan_requirements(cls)
    if reqs.pipeline_tokens or cls.get("_plan_requirements"):
        if SOURCE_ENTITY not in {s.slot for s in reqs.all_slots()}:
            return False
        gaps, _ = assess_plan_requirements(cls, reqs)
        return any(g.get("slot") == SOURCE_ENTITY for g in gaps)

    if cls.get("active_job_id"):
        return False
    if cls.get("planner_source_entity_confirmed"):
        return False
    if (cls.get("source_table_name") or cls.get("source_object_api_name") or "").strip():
        return False
    if (state.source_entity.entity_key or "").strip():
        return False
    turn = (state.turn_type or cls.get("intent") or "").lower()
    if turn in ("general", "info"):
        return False
    ops = state.operations or cls.get("operations") or []
    if "extract" not in [str(o).lower() for o in ops] and turn not in (
        "extract",
        "migrate",
        "migrate_data",
        "map_data",
    ):
        return False
    return not (state.source_entity.entity_key or "").strip()


def _needs_extract_fields(state: IntentState, cls: Dict[str, Any]) -> bool:
    if state.fields.confirmed or cls.get("extract_fields_confirmed"):
        return False
    if not (state.source_entity.entity_key or cls.get("source_table_name")):
        return False
    scope = state.fields.scope or "unspecified"
    if scope in ("explicit", "all_descriptive") and state.fields.hints_strict:
        return False
    if scope == "explicit" and state.fields.hints and state.fields.scope_confidence >= 0.7:
        return False
    if cls.get("needs_clarification"):
        for clar in cls.get("clarifications") or []:
            if isinstance(clar, dict) and clar.get("clarification_type") == "pick_extract_fields":
                return True
    return scope in ("vague", "unspecified") and bool(state.source_entity.entity_key)


GAP_RULES: List[GapRule] = [
    GapRule("pick_source_connector", _COMPOSE, _needs_source_connector),
    GapRule("pick_destination_connector", _COMPOSE, _needs_destination_connector),
    GapRule("pick_source_entity", _COMPOSE, _needs_source_entity),
    GapRule("pick_extract_fields", _COMPOSE, _needs_extract_fields),
]


def _gaps_from_clarifications(clarifications: List[Any]) -> List[Dict[str, str]]:
    gaps: List[Dict[str, str]] = []
    for clar in clarifications:
        if isinstance(clar, Clarification):
            ctype = clar.clarification_type
            message = clar.prompt or ""
        elif isinstance(clar, dict):
            ctype = clar.get("clarification_type")
            message = clar.get("prompt") or ""
        else:
            continue
        if ctype:
            gaps.append(
                {
                    "clarification_type": str(ctype),
                    "message": str(message)[:200],
                }
            )
    return gaps


def _clarification_key(clar: Clarification) -> str:
    return (clar.clarification_type or "").strip().lower()


def _clarification_from_plan(classification: Dict[str, Any]) -> List[Clarification]:
    """
    Prefer execution_plan clarifications; merge top-level only when type is new.

    Avoids duplicate pick_source_connector blocks when S10 post-plan merge copies
    the same clarification onto classification and execution_plan.
    """
    out: List[Clarification] = []
    seen: set[str] = set()

    ep = classification.get("execution_plan") or {}
    ep_clars = ep.get("clarifications") if isinstance(ep, dict) else None
    if isinstance(ep_clars, list) and ep_clars:
        for clar in ep_clars:
            if isinstance(clar, dict) and clar.get("clarification_type"):
                try:
                    model = Clarification.model_validate(clar)
                except Exception:
                    continue
                key = _clarification_key(model)
                if key and key not in seen:
                    seen.add(key)
                    out.append(model)
        if out:
            return out

    for clar in classification.get("clarifications") or []:
        if isinstance(clar, Clarification):
            key = _clarification_key(clar)
            if key and key not in seen:
                seen.add(key)
                out.append(clar)
        elif isinstance(clar, dict) and clar.get("clarification_type"):
            try:
                model = Clarification.model_validate(clar)
            except Exception:
                continue
            key = _clarification_key(model)
            if key and key not in seen:
                seen.add(key)
                out.append(model)
    return out


def _uses_plan_scope_path(classification: Dict[str, Any]) -> bool:
    from services.planning.plan_scope import read_plan_scope
    from services.planning.planner_path import classification_has_plan_scope

    return bool(classification_has_plan_scope(classification) or read_plan_scope(classification))


def _requirement_queue_clarifications(
    cls: Dict[str, Any],
    *,
    prompt: str,
    catalog: ResourceCatalog,
) -> List[Clarification]:
    """Slot queue via op_requirements + catalog pickers (LLM compose fills copy)."""
    from services.planning.clarification_queue import planner_clarifications_from_requirements
    from services.planning.gap_detector import resolve_destination_connector_clarification
    from services.planning.plan_scope import read_plan_scope

    parsed_goal = read_plan_scope(cls) or None
    queue = planner_clarifications_from_requirements(
        cls,
        catalog,
        prompt=prompt,
        parsed_goal=parsed_goal,
    )
    if queue:
        return queue
    specific = resolve_destination_connector_clarification(cls, catalog, prompt)
    return [specific] if specific else []


async def _catalog_clarifications(
    cls: Dict[str, Any],
    *,
    prompt: str,
    catalog: ResourceCatalog,
    user_id: Optional[str] = None,
) -> List[Clarification]:
    """Requirement queue first; legacy connector/flow supplement when queue is empty."""
    queue = _requirement_queue_clarifications(cls, prompt=prompt, catalog=catalog)
    if queue:
        return queue
    from services.planning.gap_detector import detect_clarifications

    return await detect_clarifications(cls, prompt, catalog, user_id=user_id)


async def detect_planner_gaps(
    state: IntentState,
    classification: Dict[str, Any],
    *,
    prompt: str,
    catalog: Optional[ResourceCatalog] = None,
    user_id: Optional[str] = None,
) -> tuple[List[Dict[str, str]], List[Clarification]]:
    """
  Read blocking gaps from execution_plan / classification after planning.

  Does not materialize new clarifications — planning kernel owns slot merge.
    """
    del state, prompt, catalog, user_id
    cls = dict(classification or {})
    clarifications: List[Clarification] = []

    ep = cls.get("execution_plan") or {}
    if isinstance(ep, dict) and (ep.get("steps") or []):
        return [], []

    plan_clars = _clarification_from_plan(cls)
    if plan_clars:
        return _gaps_from_clarifications(plan_clars), plan_clars

    if cls.get("needs_clarification"):
        for clar in cls.get("clarifications") or []:
            if isinstance(clar, dict) and clar.get("clarification_type"):
                try:
                    clarifications.append(Clarification.model_validate(clar))
                except Exception:
                    continue
        if clarifications:
            return _gaps_from_clarifications(clarifications), clarifications

    return [], []


def collect_gaps(state: IntentState, classification: Dict[str, Any]) -> List[Dict[str, str]]:
    """Sync gap list from state or intent rules (no async catalog)."""
    if state.gaps:
        return list(state.gaps)
    gaps: List[Dict[str, str]] = []
    for rule in GAP_RULES:
        if rule.applies(state, classification):
            gaps.append(
                {
                    "clarification_type": rule.clarification_type,
                    "message": rule.message,
                }
            )
    return gaps


def resolve_next_gap(
    state: IntentState,
    classification: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """First blocking gap in pipeline order."""
    gaps = state.gaps or collect_gaps(state, classification or {})
    return gaps[0] if gaps else None


async def resolve_next_gap_clarification(
    state: IntentState,
    classification: Dict[str, Any],
    *,
    prompt: str,
    catalog: Optional[ResourceCatalog] = None,
    user_id: Optional[str] = None,
) -> Optional[Clarification]:
    """First blocking Clarification (with options when from catalog)."""
    gaps, clarifications = await detect_planner_gaps(
        state,
        classification,
        prompt=prompt,
        catalog=catalog,
        user_id=user_id,
    )
    state.gaps = gaps
    state.active_gap = gaps[0]["clarification_type"] if gaps else None
    if gaps:
        state.readiness = "need_clarification"
    return clarifications[0] if clarifications else None
