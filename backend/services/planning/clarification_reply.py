"""Parse natural-language replies to planner clarification prompts."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def _wants_create_new(text: str, options: List[Dict[str, Any]]) -> bool:
    """Detect create-new intent by matching presented option labels only."""
    create_opts = [o for o in (options or []) if o.get("create_new")]
    if create_opts and _match_option_label(text, create_opts):
        return True
    t = _normalize(text)
    return t in ("gs_create_new", "create new spreadsheet", "create new sheet")


def _extract_new_workbook_title(text: str) -> Optional[str]:
    """Bare title answer for create_workbook_name (no regex parsing)."""
    raw = (text or "").strip()
    if not raw:
        return None
    if len(raw) <= 120 and not raw.endswith("?"):
        return raw.strip().strip('"').strip("'")
    return None


def _match_workbook_option(text: str, options: List[Dict[str, Any]]) -> Optional[str]:
    t = _normalize(text)
    if not t:
        return None
    for opt in options or []:
        if opt.get("create_new"):
            continue
        label = (opt.get("label") or "").strip()
        oid = opt.get("id") or ""
        ref = opt.get("resource_ref") or {}
        name = (ref.get("display_name") if isinstance(ref, dict) else getattr(ref, "display_name", None)) or ""
        candidates = [label.lower(), name.lower(), oid.lower()]
        for c in candidates:
            if not c:
                continue
            if t == c or c in t or t in c:
                return oid
        if label and "(" in label:
            wb = label.split("(", 1)[0].strip().lower()
            if wb and (t == wb or wb in t):
                return oid
    return None


def parse_clarification_reply(
    reply_text: str,
    clarifications: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Turn a chat reply into planner selection payloads.
    Returns [] if the message cannot be mapped to a required clarification.
    """
    text = (reply_text or "").strip()
    if not text or not clarifications:
        return []

    blocking = [c for c in clarifications if c.get("required", True)]
    if len(blocking) != 1:
        if len(blocking) > 1:
            return []
        target = blocking[0] if blocking else clarifications[0]
    else:
        target = blocking[0]

    cid = target.get("id") or ""
    ctype = target.get("clarification_type") or ""
    options = target.get("options") or []

    if ctype == "create_workbook_name":
        title = _extract_new_workbook_title(text)
        if title:
            return [
                {
                    "clarification_id": cid,
                    "option_id": f"text:{title}",
                    "text_value": title,
                }
            ]
        return []

    if ctype in ("pick_workbook", "pick_source_workbook", "pick_workbook_ambiguous"):
        if ctype in ("pick_workbook", "pick_source_workbook") and _wants_create_new(text, options):
            title = _extract_new_workbook_title(text)
            sel: Dict[str, Any] = {"clarification_id": cid, "option_id": "gs_create_new"}
            if title:
                sel["new_workbook_title"] = title
            return [sel]
        opt_id = _match_workbook_option(text, options)
        if opt_id:
            return [{"clarification_id": cid, "option_id": opt_id}]
        return []

    if ctype == "pick_flow":
        opt_id = _match_option_label(text, options)
        if opt_id:
            return [{"clarification_id": cid, "option_id": opt_id}]
        return []

    if ctype in ("pick_google_account", "pick_salesforce_org", "pick_sql_connector", "pick_tab"):
        opt_id = _match_option_label(text, options)
        if opt_id:
            return [{"clarification_id": cid, "option_id": opt_id}]
        return []

    if ctype in ("pick_extract_fields", "pick_create_headers"):
        from services.planning.extract_field_clarification import parse_field_clarification_reply

        parsed = parse_field_clarification_reply(text, options)
        if parsed:
            sel_payload: Dict[str, Any] = {
                "clarification_id": cid,
                "option_id": parsed["option_id"],
            }
            if parsed.get("text_value"):
                sel_payload["text_value"] = parsed["text_value"]
            return [sel_payload]
        return []

    if ctype in ("extract_entity_role", "extract_related_vs_secondary"):
        opt_id = _match_option_label(text, options)
        if opt_id:
            return [{"clarification_id": cid, "option_id": opt_id}]
        return []

    if ctype in ("pick_source_entity", "pick_destination_entity"):
        opt_id = _match_option_label(text, options)
        if opt_id:
            return [{"clarification_id": cid, "option_id": opt_id}]
        return []

    return []


def _match_option_label(text: str, options: List[Dict[str, Any]]) -> Optional[str]:
    t = _normalize(text)
    if not t:
        return None
    for opt in options or []:
        label = (opt.get("label") or "").strip().lower()
        oid = (opt.get("id") or "").lower()
        ref = opt.get("resource_ref") or {}
        name = ""
        if isinstance(ref, dict):
            name = (ref.get("display_name") or "").lower()
        if label and (t == label or label in t or t in label):
            return opt.get("id")
        if name and (t == name or name in t):
            return opt.get("id")
        if oid and oid in t:
            return opt.get("id")
    return None

