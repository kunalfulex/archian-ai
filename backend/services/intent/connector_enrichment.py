"""Pre-planner connector hooks — same-endpoint policy and plan-thread context."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def apply_pre_planner_connector_hooks(
    classification: Dict[str, Any],
    *,
    prompt: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    google_sheet_rows: Optional[List[Dict[str, Any]]] = None,
    intent_state_for_classifier: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Normalize connector-specific classification after intent pre-pipeline (S01–S06).

    Connector names and sheet scope come from classifier + PLAN_SCOPE LLM — no prompt regex.
    """
    del google_sheet_rows
    from services.planning.agent_runtime_enrichment import enrich_classification_for_agent_runtime
    from services.planning.plan_context import apply_active_plan_thread_context

    out = dict(classification or {})

    if (out.get("session_action") or "").strip() == "continue_active_job":
        out = apply_active_plan_thread_context(out, prompt, conversation_history)
        return out
    if (out.get("plan_route") or "").strip() == "continuation_path":
        out = apply_active_plan_thread_context(out, prompt, conversation_history)
        return out

    out = enrich_classification_for_agent_runtime(
        out,
        intent_state_for_classifier,
        prompt=prompt,
        conversation_history=conversation_history,
    )
    return out
