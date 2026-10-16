"""Shared dialogue context for Gate 0, S01, classifier, and PLAN_SCOPE routing prompts."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def recent_user_turns(
    conversation_history: Optional[List[Dict[str, Any]]],
    *,
    current_prompt: str,
    max_turns: int = 5,
) -> List[str]:
    lines: List[str] = []
    current_norm = (current_prompt or "").strip()
    for msg in conversation_history or []:
        role = (msg.get("type") or msg.get("role") or "").lower()
        if role != "user":
            continue
        text = (msg.get("text") or msg.get("content") or "").strip()
        if not text or text == current_norm:
            continue
        lines.append(text)
    return lines[-max_turns:]


def _summarize_message(msg: Dict[str, Any]) -> Optional[str]:
    meta = msg.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    event = str(meta.get("event_type") or "").strip()
    if event == "extraction_completed":
        rc = meta.get("record_count")
        src = meta.get("source_name") or meta.get("source_type") or "source"
        ent = meta.get("table_name") or meta.get("object_api_name")
        line = f"Extraction completed"
        if rc is not None:
            line += f" ({rc} records)"
        line += f" from {src}"
        if ent:
            line += f" — {ent}"
        return line
    if event == "migration_completed":
        dest = meta.get("destination_name") or meta.get("destination") or "destination"
        return f"Migration completed to {dest}"
    if event == "spreadsheet_creation_completed":
        name = (
            meta.get("spreadsheet_name")
            or meta.get("destination_name")
            or "Spreadsheet"
        )
        sid = (meta.get("spreadsheet_id") or "").strip()
        line = f"Created spreadsheet '{name}'"
        if sid:
            line += f" (id={sid[:12]}…)" if len(sid) > 12 else f" (id={sid})"
        return line
    if event == "planner_clarification_required":
        q = (meta.get("question") or meta.get("clarification_question") or "").strip()
        if not q:
            q = (msg.get("text") or msg.get("content") or "").strip()
        pending = meta.get("planner_pending") if isinstance(meta.get("planner_pending"), dict) else {}
        if not q and pending:
            clars = pending.get("clarifications") or []
            if clars and isinstance(clars[0], dict):
                q = (clars[0].get("prompt") or "").strip()
        return f"Asked for clarification: {q[:120]}" if q else "Asked user for clarification"
    if event in ("planner_plan_ready", "plan_ready"):
        ops = meta.get("operations") or meta.get("step_types")
        if ops:
            return f"Presented execution plan: {ops}"
        return "Presented execution plan"
    if event == "plan_dismissed" or meta.get("plan_dismissed"):
        return "User dismissed the presented plan draft"
    if event == "classification_completed":
        intent = meta.get("intent") or meta.get("mode")
        if intent:
            return f"Classified intent as {intent}"
    text = (msg.get("text") or msg.get("content") or "").strip()
    if text:
        return text[:220] + ("…" if len(text) > 220 else "")
    return None


def build_routing_dialogue_context(
    conversation_history: Optional[List[Dict[str, Any]]],
    *,
    current_prompt: str = "",
    max_entries: int = 10,
) -> str:
    """
    Recent dialogue for routing LLMs: user turns plus short assistant/session summaries.

    Includes extraction_completed and plan events from metadata — not only raw user text.
    """
    lines: List[str] = []
    current_norm = (current_prompt or "").strip()
    for msg in conversation_history or []:
        role = (msg.get("type") or msg.get("role") or "").lower()
        if role == "user":
            text = (msg.get("text") or msg.get("content") or "").strip()
            if not text:
                continue
            if text == current_norm:
                continue
            lines.append(f"User: {text}")
            continue
        if role in ("assistant", "system", "ai"):
            summary = _summarize_message(msg)
            if summary:
                prefix = "System" if role == "system" else "Assistant"
                lines.append(f"{prefix}: {summary}")
    if not lines:
        return "None"
    return "\n".join(lines[-max_entries:])


def _session_field(session_decision: Any, field: str, default: Any = None) -> Any:
    if session_decision is None:
        return default
    if isinstance(session_decision, dict):
        return session_decision.get(field, default)
    return getattr(session_decision, field, default)


def session_action_is_continue(session_decision: Any) -> bool:
    return (
        _session_field(session_decision, "session_action") == "continue_active_job"
        and bool(_session_field(session_decision, "reuse_job_id"))
    )


def _session_decision_dict(session_decision: Any) -> Dict[str, Any]:
    if session_decision is None:
        return {}
    if isinstance(session_decision, dict):
        return dict(session_decision)
    if hasattr(session_decision, "model_dump"):
        return session_decision.model_dump(mode="json")
    return {}


def build_session_routing_block(
    conversation_history: Optional[List[Dict[str, Any]]],
    *,
    current_prompt: str = "",
    session_decision: Any = None,
    job_summary: Optional[Dict[str, Any]] = None,
    progress_summary: Optional[Dict[str, Any]] = None,
    max_dialogue_entries: int = 10,
) -> str:
    """
    Curated dialogue + authoritative S01 session routing for classifier and PLAN_SCOPE.

    LLM layers read this block; no keyword routing is applied here.
    """
    dialogue = build_routing_dialogue_context(
        conversation_history,
        current_prompt=current_prompt,
        max_entries=max_dialogue_entries,
    )
    sd = _session_decision_dict(session_decision)
    session_action = (sd.get("session_action") or "no_prior_job").strip()

    lines = [
        "**Recent conversation (oldest first):**",
        dialogue,
        "",
        "**Session routing (S01 — authoritative for fresh vs continue):**",
        json.dumps(
            {
                "session_action": session_action,
                "continuation_phase": sd.get("continuation_phase") or "none",
                "reuse_job_id": bool(sd.get("reuse_job_id")),
                "confidence": sd.get("confidence"),
                "reasoning_short": sd.get("reasoning_short"),
            },
            indent=2,
        ),
    ]

    if session_action == "continue_active_job" and sd.get("reuse_job_id"):
        job_blob: Dict[str, Any] = {}
        if job_summary:
            job_blob.update(job_summary)
        if progress_summary:
            job_blob["progress"] = progress_summary
        if job_blob:
            lines.extend(
                [
                    "",
                    "**Active job (continue only — inherit source when user does not name a new one):**",
                    json.dumps(job_blob, indent=2, default=str),
                ]
            )
    elif session_action in ("no_prior_job", "fresh_extract", "fresh_full_pipeline"):
        from services.planning.session_facts import build_conversation_session_facts

        facts = build_conversation_session_facts(conversation_history)
        if facts:
            lines.extend(
                [
                    "",
                    "**Session facts (recent outcomes in this chat — use for referential phrases):**",
                    json.dumps(facts, indent=2, default=str),
                ]
            )
        lines.extend(
            [
                "",
                "**Routing principle:** New pipeline task — classify and scope from the "
                "current message and conversation above. Prior extraction in history is "
                "background only unless the user explicitly references that data in this turn.",
            ]
        )

    return "\n".join(lines)
