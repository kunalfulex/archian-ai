"""Runtime step readiness — depends_on graph + session job/artifact facts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set

from agent.plan import PlanStep
from agent.session import AgentSession

_EXTRACT_TOOLS: FrozenSet[str] = frozenset(
    {
        "extract_salesforce",
        "extract_sql",
        "extract_google_sheets",
        "extract_file",
        "file_reextract",
    }
)

_EXTRACT_DEPENDENT_TOOLS: FrozenSet[str] = frozenset(
    {
        "map_fields",
        "start_migration",
    }
)

# Canonical depends_on id used by plan_builder for extract steps (sid_extract="extract").
CANONICAL_EXTRACT_STEP_ID = "extract"
CANONICAL_CREATE_STEP_ID = "create_sheet"
CANONICAL_DATA_OPS_STEP_ID = "data_ops"
CANONICAL_MAP_STEP_ID = "map"

_DEFAULT_STEP_ID_BY_TOOL: Dict[str, str] = {
    "extract_salesforce": CANONICAL_EXTRACT_STEP_ID,
    "extract_sql": CANONICAL_EXTRACT_STEP_ID,
    "extract_google_sheets": CANONICAL_EXTRACT_STEP_ID,
    "extract_file": CANONICAL_EXTRACT_STEP_ID,
    "file_reextract": CANONICAL_EXTRACT_STEP_ID,
    "google_sheets_create_from_prompt": CANONICAL_CREATE_STEP_ID,
    "map_fields": "map",
    "start_migration": "migrate",
    "run_data_ops_plan": CANONICAL_DATA_OPS_STEP_ID,
    "gsheets_execute_operation": "join",
    "metadata_deploy_salesforce": "deploy_schema",
    "schema_gate_assess": "assess_schema",
    "schema_gate_propose": "propose_schema",
}


@dataclass
class PlanExecutionState:
    """Mutable execution progress for one approved-plan run (or resume)."""

    completed_step_ids: Set[str] = field(default_factory=set)
    extraction_complete: bool = False


def resolve_step_id(tool_id: str, args: Dict[str, Any]) -> str:
    """Stable step_id for completion tracking — args first, then tool defaults."""
    sid = (args.get("step_id") or "").strip()
    if sid:
        return sid
    op = (args.get("operation") or "").strip().lower()
    if tool_id == "gsheets_execute_operation" and op:
        return op
    return _DEFAULT_STEP_ID_BY_TOOL.get(tool_id, "")


def _non_empty_mapping_config(value: Any) -> bool:
    return isinstance(value, dict) and bool(value)


def _mapping_artifacts_present(session: AgentSession, snap: Dict[str, Any]) -> bool:
    """True when map_fields produced a usable mapping (id, config, or ready action)."""
    if session.mapping_id or snap.get("mapping_id"):
        return True
    if _non_empty_mapping_config(session.mapping_config):
        return True
    if _non_empty_mapping_config(snap.get("mapping_config")):
        return True
    last = session.last_result if isinstance(session.last_result, dict) else {}
    if (last.get("action") or "").strip() == "map_fields_ready":
        return bool(
            last.get("mapping_id")
            or _non_empty_mapping_config(last.get("mapping_config"))
            or _non_empty_mapping_config(last.get("mapping"))
        )
    return False


def _non_empty_ops_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _meta_indicates_data_ops_complete(meta: Dict[str, Any]) -> bool:
    """True when post-extract data_ops produced a versioned job or applied ops."""
    if _non_empty_ops_list(meta.get("operations_applied")):
        return True
    version = meta.get("extraction_version")
    try:
        return version is not None and int(version) > 1
    except (TypeError, ValueError):
        return False


def _data_ops_artifacts_present(session: AgentSession, snap: Dict[str, Any]) -> bool:
    """True when run_data_ops_plan finished (ops applied or extraction version bumped)."""
    last = session.last_result if isinstance(session.last_result, dict) else {}
    if _meta_indicates_data_ops_complete(last):
        return True
    if _meta_indicates_data_ops_complete(snap):
        return True
    return False


def _meta_indicates_map_complete(meta: Dict[str, Any]) -> bool:
    if (meta.get("action") or "").strip() != "map_fields_ready":
        return bool(meta.get("mapping_id") or _non_empty_mapping_config(meta.get("mapping_config")))
    return bool(
        meta.get("mapping_id")
        or _non_empty_mapping_config(meta.get("mapping_config"))
        or _non_empty_mapping_config(meta.get("mapping"))
    )


def _tool_artifacts_indicate_complete(
    tool_id: str,
    session: AgentSession,
    snap: Dict[str, Any],
) -> bool:
    """True when session/classification artifacts show this tool's step finished."""
    if tool_id in _EXTRACT_TOOLS:
        return bool(session.active_job_id or snap.get("active_job_id"))
    if tool_id == "google_sheets_create_from_prompt":
        return bool(snap.get("destination_spreadsheet_id"))
    if tool_id == "run_data_ops_plan":
        return _data_ops_artifacts_present(session, snap)
    if tool_id == "map_fields":
        return _mapping_artifacts_present(session, snap)
    if tool_id == "metadata_deploy_salesforce":
        return bool(snap.get("schema_deploy_approved"))
    if tool_id == "schema_gate_propose":
        missing = snap.get("schema_gate_missing_fields")
        return missing is not None and len(missing) == 0
    return False


def _infer_completed_step_ids_from_plan(
    session: AgentSession,
    *,
    plan_steps: Optional[List[PlanStep]] = None,
) -> Set[str]:
    """Infer satisfied step_ids by matching plan steps to live session artifacts."""
    snap = session.classification_snapshot or {}
    inferred: Set[str] = set()
    for step in plan_steps or []:
        args = step.arguments or {}
        sid = resolve_step_id(step.tool_id, args)
        if not sid:
            continue
        if _tool_artifacts_indicate_complete(step.tool_id, session, snap):
            inferred.add(sid)
    return inferred


def effective_completed_step_ids(
    session: AgentSession,
    state: PlanExecutionState,
    *,
    plan_steps: Optional[List[PlanStep]] = None,
) -> Set[str]:
    """
    Union of recorded completions and live artifact inference for depends_on checks.

    Used at each gate so a step that finished (e.g. create wrote spreadsheet_id)
    satisfies dependents even if record_step_completion did not run yet.
    """
    completed = set(state.completed_step_ids)
    completed.update(_infer_completed_step_ids_from_plan(session, plan_steps=plan_steps))
    if state.extraction_complete or session.active_job_id or (session.classification_snapshot or {}).get(
        "active_job_id"
    ):
        for step in plan_steps or []:
            if step.tool_id in _EXTRACT_TOOLS:
                sid = resolve_step_id(step.tool_id, step.arguments or {})
                if sid:
                    completed.add(sid)
        completed.add(CANONICAL_EXTRACT_STEP_ID)
    return completed


def sync_execution_state_from_session(
    session: AgentSession,
    state: PlanExecutionState,
    *,
    plan_steps: Optional[List[PlanStep]] = None,
) -> None:
    """Refresh in-memory completion from session artifacts before gating the next step."""
    state.completed_step_ids.update(
        effective_completed_step_ids(session, state, plan_steps=plan_steps)
    )
    snap = session.classification_snapshot or {}
    if session.active_job_id or snap.get("active_job_id"):
        state.extraction_complete = True


def bootstrap_execution_state(
    session: AgentSession,
    *,
    plan_steps: Optional[List[PlanStep]] = None,
) -> PlanExecutionState:
    """Restore execution progress from classification snapshot and session facts."""
    snap = session.classification_snapshot or {}
    completed: Set[str] = {
        str(x).strip() for x in (snap.get("_plan_completed_step_ids") or []) if str(x).strip()
    }
    extraction_complete = bool(session.active_job_id or snap.get("active_job_id"))
    state = PlanExecutionState(
        completed_step_ids=completed,
        extraction_complete=extraction_complete,
    )
    sync_execution_state_from_session(session, state, plan_steps=plan_steps)
    return state


def record_step_skipped(
    tool_id: str,
    args: Dict[str, Any],
    *,
    state: PlanExecutionState,
) -> None:
    """Mark a skipped plan step as satisfied for depends_on gating."""
    sid = resolve_step_id(tool_id, args)
    if sid:
        state.completed_step_ids.add(sid)


def snapshot_classification_for_pause(
    session: AgentSession,
    state: PlanExecutionState,
) -> Dict[str, Any]:
    """Persist execution progress and return classification for planner_pending payloads."""
    persist_execution_state(session, state)
    return dict(session.classification_snapshot or {})


def _find_plan_step_for_dep(
    dep: str,
    plan_steps: Optional[List[PlanStep]],
) -> Optional[PlanStep]:
    for step in plan_steps or []:
        sid = resolve_step_id(step.tool_id, step.arguments or {})
        if sid == dep:
            return step
    return None


def dependency_satisfied(
    dep: str,
    *,
    session: AgentSession,
    state: PlanExecutionState,
    plan_steps: Optional[List[PlanStep]] = None,
) -> bool:
    """True when a depends_on step_id is recorded or inferable from session artifacts."""
    effective = effective_completed_step_ids(session, state, plan_steps=plan_steps)
    if dep in effective:
        return True
    matched = _find_plan_step_for_dep(dep, plan_steps)
    if matched:
        snap = session.classification_snapshot or {}
        return _tool_artifacts_indicate_complete(matched.tool_id, session, snap)
    if dep == CANONICAL_EXTRACT_STEP_ID or dep in _EXTRACT_TOOLS:
        return state.extraction_complete or bool(
            session.active_job_id or (session.classification_snapshot or {}).get("active_job_id")
        )
    return False


def plan_step_blocked_reason(
    tool_id: str,
    args: Dict[str, Any],
    *,
    state: PlanExecutionState,
    session: AgentSession,
    label: Optional[str] = None,
    plan_steps: Optional[List[PlanStep]] = None,
) -> Optional[str]:
    """Return a user-visible block reason when a plan step must not run yet."""
    depends_on = [str(d).strip() for d in (args.get("depends_on") or []) if str(d).strip()]
    for dep in depends_on:
        if dependency_satisfied(dep, session=session, state=state, plan_steps=plan_steps):
            continue
        step_label = label or tool_id
        return f"Step “{step_label}” is waiting for “{dep}” to finish first."

    if tool_id in _EXTRACT_DEPENDENT_TOOLS:
        if not dependency_satisfied(
            CANONICAL_EXTRACT_STEP_ID,
            session=session,
            state=state,
            plan_steps=plan_steps,
        ):
            return (
                "Extraction must finish before mapping or migrating. "
                "Complete the extract step or answer any open clarification first."
            )

    if tool_id == "google_sheets_create_from_prompt":
        if args.get("use_extraction_headers") and not dependency_satisfied(
            CANONICAL_EXTRACT_STEP_ID,
            session=session,
            state=state,
            plan_steps=plan_steps,
        ):
            return (
                "Spreadsheet creation needs extraction headers, but extraction has not completed yet."
            )

    return None


def record_step_completion(
    tool_id: str,
    args: Dict[str, Any],
    *,
    state: PlanExecutionState,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Record step completion and update extraction_complete when job finishes."""
    sid = resolve_step_id(tool_id, args)
    if sid:
        state.completed_step_ids.add(sid)

    if tool_id in _EXTRACT_TOOLS:
        job_done = bool((meta or {}).get("_extraction_complete"))
        if job_done:
            state.extraction_complete = True
            state.completed_step_ids.add(CANONICAL_EXTRACT_STEP_ID)
            if sid and sid != CANONICAL_EXTRACT_STEP_ID:
                state.completed_step_ids.add(sid)

    if tool_id == "google_sheets_create_from_prompt":
        sheet_id = (meta or {}).get("spreadsheet_id")
        if sheet_id:
            state.completed_step_ids.add(CANONICAL_CREATE_STEP_ID)
            if sid:
                state.completed_step_ids.add(sid)

    if tool_id == "run_data_ops_plan" and meta and _meta_indicates_data_ops_complete(meta):
        state.completed_step_ids.add(CANONICAL_DATA_OPS_STEP_ID)
        if sid:
            state.completed_step_ids.add(sid)

    if tool_id == "map_fields" and meta and _meta_indicates_map_complete(meta):
        state.completed_step_ids.add(CANONICAL_MAP_STEP_ID)
        if sid:
            state.completed_step_ids.add(sid)


def persist_execution_state(session: AgentSession, state: PlanExecutionState) -> None:
    """Mirror execution progress onto classification_snapshot for resume within/across turns."""
    snap = dict(session.classification_snapshot or {})
    snap["_plan_completed_step_ids"] = sorted(state.completed_step_ids)
    if state.extraction_complete and session.active_job_id:
        snap["active_job_id"] = session.active_job_id
    if session.mapping_id:
        snap["mapping_id"] = session.mapping_id
    if session.mapping_config and isinstance(session.mapping_config, dict):
        snap["mapping_config"] = session.mapping_config
    last = session.last_result if isinstance(session.last_result, dict) else {}
    if _non_empty_ops_list(last.get("operations_applied")):
        snap["operations_applied"] = list(last["operations_applied"])
    if last.get("extraction_version") is not None:
        try:
            snap["extraction_version"] = int(last["extraction_version"])
        except (TypeError, ValueError):
            snap["extraction_version"] = last["extraction_version"]
    session.classification_snapshot = snap


def merge_execution_state_into_classification(
    classification: Dict[str, Any],
    *,
    conversation_history: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Restore plan execution progress from chat history for a new HTTP request.

    Merges ``_plan_completed_step_ids``, ``plan_resume_step_index``, and ``active_job_id``
    from the latest unresolved plan-execution pending or plan step checkpoints.
    """
    out = dict(classification or {})
    if not conversation_history:
        return out

    from services.planning.plan_context import latest_plan_execution_state_from_history

    inherited = latest_plan_execution_state_from_history(conversation_history)
    if not inherited:
        return out

    for key in (
        "_plan_completed_step_ids",
        "plan_resume_step_index",
        "active_job_id",
        "mapping_id",
        "mapping_config",
        "operations_applied",
        "extraction_version",
    ):
        if key not in out or out.get(key) in (None, "", []):
            val = inherited.get(key)
            if val not in (None, "", []):
                out[key] = val
    if inherited.get("destination_spreadsheet_id") and not out.get("destination_spreadsheet_id"):
        out["destination_spreadsheet_id"] = inherited["destination_spreadsheet_id"]
    return out


# Backward-compatible wrappers for tests and gradual migration.
def mark_plan_step_complete(
    step: PlanStep,
    *,
    completed_step_ids: Set[str],
    extraction_complete: bool,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    state = PlanExecutionState(
        completed_step_ids=set(completed_step_ids),
        extraction_complete=extraction_complete,
    )
    record_step_completion(
        step.tool_id,
        step.arguments or {},
        state=state,
        meta=meta,
    )
    completed_step_ids.clear()
    completed_step_ids.update(state.completed_step_ids)
    return state.extraction_complete
