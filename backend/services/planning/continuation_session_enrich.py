"""Session-facts enrichment for continuation PLAN_SCOPE (Phase 4)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.planning.continuation_observability import log_continuation_bind


async def enrich_continuation_goal_from_session_facts(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    *,
    user_id: str,
    prompt: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    conversation_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Resolve ambiguous destination_phrase ("that sheet") from chat session facts.

    Mirrors fresh-path orchestrator behavior for continuation before planning kernel.
    """
    from services.planning.session_facts import (
        apply_session_destination_pins,
        build_conversation_session_facts,
        destination_phrase_is_generic,
        enrich_destination_phrase_from_conversation_if_needed,
    )

    if not conversation_history:
        return classification, goal

    cls = dict(classification or {})
    scoped = dict(goal or {})
    phrase_before = (
        scoped.get("destination_phrase") or cls.get("destination_phrase") or ""
    ).strip()

    session_facts = build_conversation_session_facts(conversation_history)
    if not session_facts:
        return cls, scoped

    # Ensure connector guards see map/migrate from PLAN_SCOPE (classification may be sparse).
    ops = [str(o).lower() for o in (scoped.get("operations") or cls.get("operations") or [])]
    if ops:
        cls.setdefault("operations", scoped.get("operations") or cls.get("operations"))
        cls["_plan_scope"] = scoped
        if any(o in ops for o in ("map", "migrate")):
            cls.setdefault("intent", "map_data" if "map" in ops else "migrate_data")
            cls.setdefault("mode", "map" if "map" in ops else "migrate")

    if destination_phrase_is_generic(phrase_before):
        scoped = await enrich_destination_phrase_from_conversation_if_needed(
            scoped,
            cls,
            session_facts,
            user_id=str(user_id),
            prompt=prompt,
            conversation_history=conversation_history,
            conversation_id=conversation_id,
        )
        phrase_after = (scoped.get("destination_phrase") or "").strip()
        if phrase_after and phrase_after != phrase_before:
            log_continuation_bind(
                "session_phrase",
                job_id=job_id,
                extra={"from": phrase_before or None, "to": phrase_after},
            )

    cls, scoped = apply_session_destination_pins(cls, scoped, session_facts)
    cls["_conversation_session_facts"] = session_facts
    if scoped.get("destination_phrase"):
        cls["destination_phrase"] = scoped["destination_phrase"]
    return cls, scoped
