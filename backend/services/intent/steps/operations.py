"""S09 — ordered operations from PLAN_SCOPE / pipeline tokens."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.state import IntentState
from services.intent.types import IntentStepId, StepOutcome, StepResult


async def run_step_operations(
    state: IntentState,
    *,
    classification: Optional[Dict[str, Any]] = None,
) -> StepResult:
    cls = classification or {}
    snap = state.classifier_snapshot or cls

    pipeline_tokens = cls.get("_pipeline_tokens") or snap.get("_pipeline_tokens")
    if isinstance(pipeline_tokens, list):
        ops = [str(t).lower() for t in pipeline_tokens if str(t).strip()]
    else:
        from services.planning.plan_scope import read_plan_scope

        goal = read_plan_scope(cls)
        ops = [str(o).lower() for o in (goal.get("operations") or []) if str(o).strip()]
        if not ops and state.operations:
            ops = list(state.operations)

    ops = ["deploy_schema" if o == "metadata_deploy" else o for o in ops]

    if bool(snap.get("multi_source_extract")) and "extract" not in ops:
        ops.insert(0, "extract")
    if str(snap.get("intent") or "").lower() in ("metadata_deploy", "deploy") and "deploy_schema" not in ops:
        ops.append("deploy_schema")
    if str(snap.get("intent") or "").lower() == "join" and "join" not in ops:
        ops.append("join")

    state.operations = ops
    stored_goal = cls.get("_plan_scope") or snap.get("_plan_scope")
    if isinstance(stored_goal, dict) and stored_goal.get("mode"):
        state.mode = str(stored_goal.get("mode"))
    elif cls.get("mode"):
        state.mode = str(cls.get("mode"))
    state.mark_step(IntentStepId.S09_OPERATIONS.value)
    return StepResult(step_id=IntentStepId.S09_OPERATIONS, outcome=StepOutcome.COMPLETE)
