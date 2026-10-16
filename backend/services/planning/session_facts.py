"""Structured session facts from chat metadata for LLM routing and endpoint pins."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MIN_DEST_PHRASE_CONFIDENCE = 0.55
_GENERIC_DEST_PHRASES = frozenset(
    {
        "google sheets",
        "google_sheets",
        "google sheet",
        "gsheets",
        "spreadsheet",
        "sheet",
        "workbook",
        "the sheet",
        "that sheet",
        "above sheet",
        "created sheet",
    }
)


def _message_metadata(msg: Dict[str, Any]) -> Dict[str, Any]:
    meta = msg.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _normalize_label(value: Optional[str]) -> str:
    return " ".join(str(value or "").strip().lower().split())


def latest_spreadsheet_creation_metadata(
    conversation_history: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Most recent spreadsheet_creation_completed event in chat history."""
    for msg in reversed(conversation_history or []):
        meta = _message_metadata(msg)
        event = str(meta.get("event_type") or "").strip()
        if event != "spreadsheet_creation_completed":
            continue
        name = (
            meta.get("spreadsheet_name")
            or meta.get("destination_name")
            or ""
        ).strip()
        sid = (meta.get("spreadsheet_id") or "").strip()
        if name or sid:
            return dict(meta)
    return None


def build_conversation_session_facts(
    conversation_history: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Authoritative facts from chat data events (not LLM-generated)."""
    from services.planning.conversation_session import latest_extraction_completed_metadata

    facts: Dict[str, Any] = {}
    sheet_meta = latest_spreadsheet_creation_metadata(conversation_history)
    if sheet_meta:
        name = (
            sheet_meta.get("spreadsheet_name")
            or sheet_meta.get("destination_name")
            or ""
        ).strip()
        facts["last_created_spreadsheet"] = {
            "spreadsheet_name": name or None,
            "spreadsheet_id": (sheet_meta.get("spreadsheet_id") or "").strip() or None,
            "spreadsheet_url": (sheet_meta.get("spreadsheet_url") or "").strip() or None,
            "destination": "google_sheets",
        }
    ext_meta = latest_extraction_completed_metadata(conversation_history)
    if ext_meta:
        facts["last_extraction"] = {
            "job_id": ext_meta.get("job_id") or ext_meta.get("active_job_id"),
            "source": ext_meta.get("source_type") or ext_meta.get("source"),
            "source_name": ext_meta.get("source_name"),
            "entity": ext_meta.get("table_name")
            or ext_meta.get("object_api_name")
            or ext_meta.get("source_entity_key"),
            "record_count": ext_meta.get("record_count"),
            "destination": ext_meta.get("destination"),
            "destination_name": ext_meta.get("destination_name"),
        }
    return facts


def _destination_phrase_is_generic(phrase: Optional[str]) -> bool:
    norm = _normalize_label(phrase)
    if not norm:
        return True
    return norm in _GENERIC_DEST_PHRASES


def destination_phrase_is_generic(phrase: Optional[str]) -> bool:
    """Public helper — True for empty or deictic phrases like 'that sheet'."""
    return _destination_phrase_is_generic(phrase)


def _phrase_matches_spreadsheet(phrase: str, spreadsheet_name: str) -> bool:
    p = _normalize_label(phrase)
    n = _normalize_label(spreadsheet_name)
    if not p or not n:
        return False
    return p == n or p in n or n in p


def apply_session_destination_pins(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]],
    session_facts: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Pin google_sheets destination workbook from session facts when PLAN_SCOPE phrase matches.

    Deterministic companion to PLAN_SCOPE / bind LLMs — no prompt keyword routing.
    """
    from services.planning.connector_guards import classification_destination_endpoint_resolved
    from services.planning.schemas import normalize_connector_type

    out = dict(classification or {})
    goal = dict(parsed_goal or {})
    if classification_destination_endpoint_resolved(out):
        return out, goal

    last = (session_facts or {}).get("last_created_spreadsheet") or {}
    sheet_name = (last.get("spreadsheet_name") or "").strip()
    sheet_id = (last.get("spreadsheet_id") or "").strip()
    if not sheet_name and not sheet_id:
        return out, goal

    dest_type = normalize_connector_type(out.get("destination") or "")
    if dest_type != "google_sheets":
        return out, goal

    phrase = (
        (goal.get("destination_phrase") or "").strip()
        or (out.get("destination_phrase") or "").strip()
    )
    if not phrase or _destination_phrase_is_generic(phrase):
        return out, goal
    if sheet_name and not _phrase_matches_spreadsheet(phrase, sheet_name):
        return out, goal

    if sheet_name:
        out["destination_name"] = sheet_name
        out["destination_object_api_name"] = sheet_name
    if sheet_id:
        out["destination_spreadsheet_id"] = sheet_id
    out["destination"] = "google_sheets"
    if phrase and sheet_name:
        goal["destination_phrase"] = sheet_name
        out["destination_phrase"] = sheet_name
    return out, goal


async def enrich_destination_phrase_from_conversation_if_needed(
    parsed_goal: Dict[str, Any],
    classification: Dict[str, Any],
    session_facts: Dict[str, Any],
    *,
    user_id: str,
    prompt: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    When migrate/map needs a destination and phrase is missing or generic, ask LLM using
    session facts + dialogue (not keyword rules on the user prompt).
    """
    from services.planning.connector_guards import classification_destination_endpoint_resolved
    from services.planning.plan_scope import read_plan_scope

    goal = dict(parsed_goal or {})
    if classification_destination_endpoint_resolved(classification, parsed_goal=goal):
        return goal

    scope = read_plan_scope(classification) or goal
    ops = [str(o).lower() for o in (scope.get("operations") or goal.get("operations") or [])]
    if not any(o in ops for o in ("map", "migrate")):
        return goal

    phrase = (goal.get("destination_phrase") or classification.get("destination_phrase") or "").strip()
    if phrase and not _destination_phrase_is_generic(phrase):
        return goal

    last = session_facts.get("last_created_spreadsheet")
    if not last and not session_facts.get("last_extraction"):
        return goal

    from services.intent.routing_context import build_routing_dialogue_context

    dialogue = build_routing_dialogue_context(
        conversation_history,
        current_prompt=prompt or "",
        max_entries=10,
    )
    llm_prompt = f"""Extract the map/migrate DESTINATION the user means in this turn.

**Session facts (authoritative recent outcomes):**
{json.dumps(session_facts, indent=2, default=str)}

**Recent conversation:**
{dialogue}

**User message:**
\"\"\"{(prompt or '').strip()}\"\"\"

Return ONLY valid JSON:
{{
  "destination_phrase": "<spreadsheet name, org name, SQL connection label, or null>",
  "references_recent_spreadsheet": true|false,
  "confidence": 0.0-1.0
}}

Rules:
- When the user refers to a spreadsheet created earlier in this chat (e.g. "above sheet", "that sheet we created", "the sheet you just made"), set destination_phrase to that spreadsheet's name from session facts.
- Do NOT return connector type labels like "google_sheets" or "google sheets" — use the human workbook name when referring to a created sheet.
- confidence < 0.5 → destination_phrase null.
"""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks
        from utils.openai_helpers import parse_openai_json_response

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_SCOPE,
            messages=[{"role": "user", "content": llm_prompt}],
            max_tokens=180,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content or "{}"
        data = parse_openai_json_response(raw)
        if not isinstance(data, dict):
            return goal
        if float(data.get("confidence") or 0) < _MIN_DEST_PHRASE_CONFIDENCE:
            return goal
        resolved = (data.get("destination_phrase") or "").strip()
        if not resolved or _destination_phrase_is_generic(resolved):
            return goal
        goal["destination_phrase"] = resolved
        return goal
    except Exception as exc:
        logger.debug("enrich_destination_phrase_from_conversation skipped: %s", exc)
        return goal
