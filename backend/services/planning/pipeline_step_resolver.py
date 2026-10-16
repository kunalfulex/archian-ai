"""Goal-driven pipeline step resolution (PLAN_SCOPE ops → ordered execution tokens)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

PipelineStepToken = str  # extract | data_ops | map | migrate | create | join | deploy_schema | ...

# Canonical ordering when PLAN_SCOPE lists ops out of dependency order.
_TOKEN_ORDER = (
    "extract",
    "data_ops",
    "create",
    "join",
    "upsert",
    "search",
    "update",
    "formula",
    "map",
    "migrate",
    "deploy_schema",
    "deploy",
)


@dataclass
class PlanContext:
    """Mechanical facts for dependency expansion — no prompt keyword matching."""

    active_job_id: Optional[str] = None
    mapping_ready: bool = False
    continuation_phase: str = "none"
    is_continuation: bool = False
    plan_route: Optional[str] = None

    @classmethod
    def from_classification(cls, classification: Dict[str, Any]) -> PlanContext:
        progress = classification.get("_session_progress") or {}
        last = (classification.get("last_completed_step") or progress.get("last_completed_step") or "").lower()
        active_job = (classification.get("active_job_id") or "").strip()
        mapping_ready = bool(
            progress.get("mapping_ready")
            or classification.get("mapping_ready")
        )
        if not mapping_ready and not active_job:
            mapping_ready = last == "map"
        intent_l = (classification.get("intent") or "").strip().lower()
        mode_l = (classification.get("mode") or "").strip().lower()
        job_continuation_intent = intent_l in (
            "map_data",
            "migrate_data",
            "migrate",
        ) or mode_l in ("map", "migrate", "data_ops")
        is_cont = bool(
            classification.get("_continuation_steps")
            or classification.get("plan_route") == "continuation_path"
            or (
                classification.get("active_job_id")
                and (classification.get("session_action") or "") == "continue_active_job"
            )
            or (classification.get("active_job_id") and job_continuation_intent)
        )
        return cls(
            active_job_id=classification.get("active_job_id"),
            mapping_ready=mapping_ready,
            continuation_phase=str(classification.get("continuation_phase") or "none"),
            is_continuation=is_cont,
            plan_route=classification.get("plan_route"),
        )


def _continuation_phase_fallback(ctx: PlanContext) -> List[str]:
    """S01 phase hint when PLAN_SCOPE returned no ops (legacy continuation only)."""
    phase = (ctx.continuation_phase or "none").lower()
    if phase == "data_ops":
        return ["data_ops"]
    if phase == "map":
        return ["map"]
    if phase == "migrate":
        if ctx.mapping_ready:
            return ["migrate"]
        return ["map", "migrate"]
    return []


def _order_tokens(tokens: List[str]) -> List[str]:
    order = {t: i for i, t in enumerate(_TOKEN_ORDER)}
    return sorted(tokens, key=lambda t: order.get(t, 99))


def _tokens_from_goal(goal: Dict[str, Any], ctx: PlanContext) -> List[str]:
    """Materialize step tokens from authoritative PLAN_SCOPE ops only (no silent injection)."""
    from services.planning.goal_parser import split_pipeline_and_data_operations
    from services.planning.plan_scope import ensure_map_for_migrate_goal

    goal = ensure_map_for_migrate_goal(dict(goal or {}), mapping_ready=ctx.mapping_ready)

    all_ops = [str(o).lower() for o in (goal.get("operations") or [])]
    pipeline, embedded_data = split_pipeline_and_data_operations(all_ops)
    data_ops = list(goal.get("data_operations") or [])
    if not data_ops and embedded_data:
        data_ops = embedded_data

    steps: List[str] = []
    for token in _TOKEN_ORDER:
        if token == "data_ops":
            if data_ops:
                steps.append("data_ops")
            continue
        if token == "deploy":
            if "deploy" in pipeline and "deploy_schema" not in pipeline:
                steps.append("deploy_schema")
            continue
        if token in pipeline:
            steps.append("deploy_schema" if token == "deploy" else token)

    if steps:
        return steps

    if ctx.is_continuation:
        return _continuation_phase_fallback(ctx)

    return []


def resolve_pipeline_steps(
    goal: Dict[str, Any],
    ctx: PlanContext,
    *,
    precomputed_steps: Optional[List[str]] = None,
) -> List[str]:
    """
    Expand PLAN_SCOPE operations into ordered execution tokens.

    PLAN_SCOPE is authoritative — this function orders tokens only (no silent map
    injection). Map before migrate is ensured in plan_scope / scope_validator when
    migrate is in PLAN_SCOPE pipeline_ops.
    """
    if precomputed_steps is not None:
        return _order_tokens(list(precomputed_steps))

    return _tokens_from_goal(goal, ctx)


def derive_pipeline_label(tokens: List[str]) -> str:
    return " → ".join(tokens) if tokens else "empty"
