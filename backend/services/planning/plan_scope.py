"""PLAN_SCOPE — slim LLM for pipeline scope + migrate→map dependency + binding gates."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PLAN_SCOPE_FIELD = "_plan_scope"


def read_plan_scope(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Read PLAN_SCOPE dict from classification."""
    snap = dict(classification or {})
    raw = snap.get(PLAN_SCOPE_FIELD)
    return dict(raw) if isinstance(raw, dict) else {}


def write_plan_scope(classification: Dict[str, Any], scope: Dict[str, Any]) -> Dict[str, Any]:
    """Persist canonical PLAN_SCOPE field on classification."""
    out = dict(classification or {})
    out[PLAN_SCOPE_FIELD] = dict(scope or {})
    return out


def classification_requests_fresh_extraction(classification: Dict[str, Any]) -> bool:
    """True when S01 / plan_route decided this turn starts a new extract (not prompt keywords)."""
    snap = dict(classification or {})
    session_action = (snap.get("session_action") or "").strip()
    if session_action in ("fresh_extract", "fresh_full_pipeline"):
        return True
    if (snap.get("plan_route") or "") == "fresh_path" and not snap.get("active_job_id"):
        return True
    return False


def classification_retains_active_job_at_execution(classification: Dict[str, Any]) -> bool:
    """
    True when AgentRuntime should reuse active_job_id from classification / history.

    Uses S01 session_action, plan_route, and pipeline tokens — not prompt keywords.
    """
    snap = dict(classification or {})
    session_action = (snap.get("session_action") or "").strip()
    if session_action == "continue_active_job":
        return True
    if (snap.get("plan_route") or "").strip() == "continuation_path":
        return True
    if session_action in ("fresh_extract", "fresh_full_pipeline"):
        return False
    if classification_requests_fresh_extraction(snap):
        return False

    tokens = continuation_pipeline_tokens(snap)
    if tokens and "extract" not in tokens:
        return True

    scope = read_plan_scope(snap)
    ops = [
        str(o).lower()
        for o in (scope.get("operations") or scope.get("pipeline_ops") or [])
    ]
    if "extract" in ops or "extract" in tokens:
        return False
    return bool(snap.get("active_job_id"))


def continuation_pipeline_tokens(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Authoritative ordered pipeline tokens for this turn."""
    snap = dict(classification or {})
    precomputed = snap.get("_continuation_steps") or snap.get("_pipeline_tokens")
    if precomputed:
        return [str(t).lower() for t in precomputed]
    goal = dict(parsed_goal or read_plan_scope(snap) or {})
    return [str(t).lower() for t in resolve_pipeline_tokens_from_goal(snap, goal)]


def tokens_need_map_destination_llm(cont_steps: List[str], job: Any) -> bool:
    """
    True when continuation should run map/migrate destination catalog LLM.

    Create-only turns use op_requirements create slots — not map connector resolution.
    """
    steps = [str(s).lower() for s in (cont_steps or [])]
    if "create" in steps and "map" not in steps and "migrate" not in steps:
        return False
    if "map" in steps:
        return True
    if "migrate" in steps and not bool(getattr(job, "mapping_ready", False)):
        return True
    return False


def continuation_should_skip_extract_steps(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when an active continuation job must not re-run extract steps at execution."""
    snap = dict(classification or {})
    if not snap.get("active_job_id"):
        return False
    if (snap.get("plan_route") or "") == "fresh_path":
        if (snap.get("session_action") or "") not in ("continue_active_job",):
            return False
    tokens = continuation_pipeline_tokens(snap, parsed_goal)
    return "extract" not in tokens


_PIPELINE_TOKENS = frozenset({"extract", "data_ops", "map", "migrate"})
_DOWNSTREAM_PIPELINE_OPS = frozenset({"map", "migrate", "deploy_schema", "join", "create"})


def goal_requests_map_or_migrate(goal: Dict[str, Any]) -> bool:
    """True when PLAN_SCOPE included map/migrate (compound or single downstream)."""
    ops = [str(o).lower() for o in (goal.get("operations") or [])]
    return any(o in _DOWNSTREAM_PIPELINE_OPS for o in ops)


def derive_scope_kind(goal: Dict[str, Any]) -> str:
    """single = one op family; compound = data_ops plus map/migrate/extract pipeline families."""
    has_data_ops = bool(goal.get("data_operations"))
    pipeline = [str(o).lower() for o in (goal.get("operations") or [])]
    has_extract = "extract" in pipeline
    has_downstream = any(o in _DOWNSTREAM_PIPELINE_OPS for o in pipeline)
    families = int(has_data_ops) + int(has_extract) + int(has_downstream)
    if has_data_ops and (has_downstream or has_extract):
        return "compound"
    if has_data_ops and not pipeline:
        return "single"
    if pipeline and not has_data_ops:
        return "single" if len(pipeline) == 1 and not has_extract else "compound"
    return "compound" if families > 1 else "single"


def _sync_create_op_into_pipeline(goal: Dict[str, Any]) -> Dict[str, Any]:
    """When create_destination is set, ensure create is in pipeline ops."""
    from services.planning.pipeline_step_resolver import _order_tokens

    out = dict(goal or {})
    if not out.get("create_destination"):
        return out
    ops = [str(o).lower() for o in (out.get("operations") or []) if str(o).strip()]
    if "create" not in ops:
        if "extract" in ops:
            ops.insert(ops.index("extract") + 1, "create")
        else:
            ops.insert(0, "create")
        out["operations"] = _order_tokens(ops)
    return out


def finalize_plan_scope_goal(goal: Dict[str, Any]) -> Dict[str, Any]:
    """Attach derived scope metadata after PLAN_SCOPE merge."""
    out = _sync_create_op_into_pipeline(dict(goal or {}))
    out["scope_kind"] = derive_scope_kind(out)
    ops = [str(o).lower() for o in (out.get("operations") or [])]
    if goal_requests_map_or_migrate(out) or out.get("create_destination") or "create" in ops:
        out["needs_destination"] = True
    if out.get("needs_source") is None:
        out["needs_source"] = any(
            o in ops for o in ("extract", "join", "deploy_schema", "upsert", "search")
        )
    return out



def merge_plan_scope_preserving_export_ops(
    prior: Dict[str, Any],
    refreshed: Dict[str, Any],
) -> Dict[str, Any]:
    """Keep extract+create export pipeline when PLAN_SCOPE re-enrich drops extract."""
    prior = dict(prior or {})
    refreshed = dict(refreshed or {})
    prior_ops = [str(o).lower() for o in (prior.get("operations") or []) if str(o).strip()]
    new_ops = [str(o).lower() for o in (refreshed.get("operations") or []) if str(o).strip()]
    if (
        "extract" in prior_ops
        and "create" in prior_ops
        and "extract" not in new_ops
        and ("create" in new_ops or refreshed.get("create_destination"))
    ):
        merged_ops = list(prior_ops)
        for op in new_ops:
            if op not in merged_ops:
                merged_ops.append(op)
        refreshed["operations"] = merged_ops
        if prior.get("create_destination") and not refreshed.get("create_destination"):
            refreshed["create_destination"] = prior.get("create_destination")
        for field in (
            "spreadsheet_title",
            "spreadsheet_headers",
            "spreadsheet_header_source",
            "source_phrase",
            "destination_phrase",
        ):
            if prior.get(field) and not refreshed.get(field):
                refreshed[field] = prior[field]
    return finalize_plan_scope_goal(refreshed)


def derive_binding_requirements(
    pipeline_tokens: List[str],
    *,
    plan_route: Optional[str] = None,
    active_job_id: Optional[str] = None,
    classification: Optional[Dict[str, Any]] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """Mechanical binding gates projected from per-step plan requirements."""
    from services.planning.op_requirements import derive_plan_requirements, project_binding_requirements
    from services.planning.pipeline_step_resolver import PlanContext

    cls = dict(classification or {})
    if plan_route:
        cls["plan_route"] = plan_route
    if active_job_id:
        cls["active_job_id"] = active_job_id
    ctx = PlanContext.from_classification(cls)
    reqs = derive_plan_requirements(
        pipeline_tokens,
        classification=cls,
        parsed_goal=parsed_goal,
        plan_context=ctx,
    )
    return project_binding_requirements(reqs)


def _intent_mode_from_scope(
    ops: List[str],
    parsed: Dict[str, Any],
    classification: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Derive intent/mode labels from authoritative PLAN_SCOPE ops (not classifier alone)."""
    pipeline = [str(o).lower() for o in (ops or [])]
    data_ops = list(parsed.get("data_operations") or [])
    create_in_scope = "create" in pipeline or bool(parsed.get("create_destination"))
    if create_in_scope and classification:
        from services.planning.create_metadata import should_materialize_create_step

        if not should_materialize_create_step(classification, parsed):
            pipeline = [o for o in pipeline if o != "create"]
            create_in_scope = False
    if create_in_scope:
        if "migrate" in pipeline:
            return "migrate_data", "migrate"
        return "create", "create"
    if "migrate" in pipeline:
        return "migrate_data", "migrate"
    if "map" in pipeline:
        return "map_data", "map"
    if data_ops and not pipeline:
        return "transform", "data_ops"
    if "extract" in pipeline:
        return "extract", "extract"
    if "deploy_schema" in pipeline or "deploy" in pipeline:
        return "metadata_deploy", "deploy"
    if "upsert" in pipeline:
        return "upsert", "upsert"
    if "update" in pipeline:
        return "update", "update"
    if "search" in pipeline:
        return "extract", "extract"
    if "join" in pipeline:
        return "multi", "extract"
    return None, None


def sync_scope_into_classification(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    pipeline_tokens: List[str],
) -> Dict[str, Any]:
    """Persist scope artifacts on classification for gap_detector and intent steps."""
    out = dict(classification or {})
    parsed = finalize_plan_scope_goal(dict(goal or {}))
    out = write_plan_scope(out, parsed)
    out["_pipeline_tokens"] = list(pipeline_tokens or [])
    from services.planning.op_requirements import derive_plan_requirements
    from services.planning.pipeline_step_resolver import PlanContext

    plan_reqs = derive_plan_requirements(
        pipeline_tokens,
        classification=out,
        parsed_goal=parsed,
        plan_context=PlanContext.from_classification(out),
    )
    out["_plan_requirements"] = plan_reqs.to_dict()
    out["_binding_requirements"] = derive_binding_requirements(
        pipeline_tokens,
        plan_route=out.get("plan_route"),
        active_job_id=out.get("active_job_id"),
        classification=out,
        parsed_goal=parsed,
    )
    if parsed.get("operations"):
        out["operations"] = list(parsed.get("operations") or [])
    elif parsed.get("data_operations") and "data_ops" in (pipeline_tokens or []):
        out["operations"] = list(parsed.get("data_operations") or [])
    if parsed.get("data_operations"):
        out["data_operations"] = list(parsed.get("data_operations") or [])

    from services.planning.create_metadata import apply_create_metadata_to_classification

    out = apply_create_metadata_to_classification(out, parsed)

    intent, mode = _intent_mode_from_scope(
        list(parsed.get("operations") or pipeline_tokens or []),
        parsed,
        out,
    )
    if intent:
        out["intent"] = intent
    if mode:
        out["mode"] = mode
    for field in (
        "source_phrase",
        "destination_phrase",
        "join_key",
        "needs_source",
        "needs_destination",
        "extract_sources",
        "multi_source_extract",
    ):
        if parsed.get(field) is not None:
            out[field] = parsed[field]

    from services.file_extraction.pending_upload import reconcile_plan_scope_with_file_source

    out, parsed = reconcile_plan_scope_with_file_source(out, parsed)
    out = write_plan_scope(out, parsed)
    return out


def ensure_map_for_migrate_goal(
    goal: Dict[str, Any],
    *,
    mapping_ready: bool = False,
) -> Dict[str, Any]:
    """When migrate is in scope, ensure map is present unless mapping is already done."""
    out = dict(goal or {})
    ops = [str(o).lower() for o in (out.get("operations") or [])]
    if "migrate" in ops and "map" not in ops and not mapping_ready:
        idx = ops.index("migrate")
        ops.insert(idx, "map")
        out["operations"] = ops
    return finalize_plan_scope_goal(out)


def ensure_create_for_create_destination_goal(goal: Dict[str, Any]) -> Dict[str, Any]:
    """When create_destination is set, ensure create is in pipeline ops."""
    return finalize_plan_scope_goal(dict(goal or {}))


def arm_create_destination_from_user_choice(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Sync PLAN_SCOPE after user picks create-new workbook (entity-layer clarification)."""
    from services.planning.source_column_headers import HEADER_SOURCE_FROM_EXTRACTION

    out = dict(classification or {})
    goal = dict(read_plan_scope(out))
    goal["create_destination"] = True
    goal["spreadsheet_header_source"] = HEADER_SOURCE_FROM_EXTRACTION
    goal.pop("spreadsheet_headers", None)
    goal = ensure_create_for_create_destination_goal(goal)
    out = write_plan_scope(out, goal)
    tokens = resolve_pipeline_tokens_from_goal(out, goal)
    return sync_scope_into_classification(out, goal, tokens)


def apply_continuation_scope_cap(
    goal: Dict[str, Any],
    *,
    continuation_phase: str,
    mapping_ready: bool = False,
) -> Dict[str, Any]:
    """
    Mechanical continuation scope reconciliation (no S01 phase overrides).

    When migrate is in PLAN_SCOPE ops and mapping is not ready, ensure map is present.
    """
    del continuation_phase
    out = dict(goal or {})
    out = ensure_map_for_migrate_goal(out, mapping_ready=mapping_ready)
    return finalize_plan_scope_goal(out)


def resolve_pipeline_tokens_from_goal(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    *,
    precomputed_steps: Optional[List[str]] = None,
) -> List[str]:
    """Resolve ordered pipeline tokens from goal + classification context."""
    from services.planning.pipeline_step_resolver import PlanContext, resolve_pipeline_steps

    ctx = PlanContext.from_classification(classification)
    return resolve_pipeline_steps(
        goal,
        ctx,
        precomputed_steps=precomputed_steps,
    )


def scope_needs_source_resolution(classification: Dict[str, Any]) -> bool:
    if classification.get("_plan_requirements") or classification.get("_binding_requirements"):
        from services.planning.op_requirements import (
            read_plan_requirements,
            requirements_need_source_bind,
        )

        return requirements_need_source_bind(
            read_plan_requirements(classification), classification
        )
    from services.planning.connector_guards import classification_needs_source_endpoint

    return classification_needs_source_endpoint(classification)


def scope_needs_destination_resolution(classification: Dict[str, Any]) -> bool:
    if classification.get("_plan_requirements") or classification.get("_binding_requirements"):
        from services.planning.op_requirements import (
            read_plan_requirements,
            requirements_need_dest_bind,
        )

        return requirements_need_dest_bind(read_plan_requirements(classification))
    from services.planning.connector_guards import classification_needs_destination_endpoint

    return classification_needs_destination_endpoint(classification)
