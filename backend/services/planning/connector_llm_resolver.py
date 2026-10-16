"""LLM-first connector resolution — maps PLAN_SCOPE phrases to catalog instances."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from services.planning.resource_catalog import ResourceCatalog

logger = logging.getLogger(__name__)

_MIN_CONNECTOR_CONFIDENCE = 0.55


@dataclass(frozen=True)
class LlmConnectorPick:
    connector_type: str
    display_name: str
    connector_id: Optional[str]
    confidence: float
    reasoning_short: Optional[str] = None


def _profile_payload(profile: Any) -> Dict[str, Any]:
    if not profile:
        return {}
    return {
        "synopsis": (getattr(profile, "synopsis", None) or "")[:400] or None,
        "synonyms": list(getattr(profile, "synonyms", None) or [])[:12],
        "business_domains": list(getattr(profile, "business_domains", None) or [])[:12],
        "system_roles": list(getattr(profile, "system_roles", None) or [])[:8],
    }


def build_connector_catalog_for_llm(catalog: ResourceCatalog) -> List[Dict[str, Any]]:
    """Semantic catalog snapshot for LLM — no scoring or keyword matching."""
    options: List[Dict[str, Any]] = []
    for conn in catalog.sql_connections:
        if not conn.name:
            continue
        options.append(
            {
                "connector_type": "sql",
                "display_name": conn.name,
                "connector_id": conn.connector_id,
                **_profile_payload(conn.profile),
            }
        )
    for conn in catalog.salesforce_connections:
        if not conn.name:
            continue
        options.append(
            {
                "connector_type": "salesforce",
                "display_name": conn.name,
                "connector_id": conn.token_id,
                **_profile_payload(conn.profile),
            }
        )
    for conn in catalog.google_sheets_connections:
        if not conn.name:
            continue
        options.append(
            {
                "connector_type": "google_sheets",
                "display_name": conn.name,
                "connector_id": conn.connection_id,
                **_profile_payload(conn.profile),
            }
        )
    return options


def build_workbook_catalog_for_llm(catalog: ResourceCatalog) -> List[Dict[str, Any]]:
    """Spreadsheet workbooks/tabs for info-path connector LLM (names only)."""
    options: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in catalog.google_sheet_rows or []:
        sn = (row.spreadsheet_name or "").strip()
        key = sn.lower()
        if not sn or key in seen:
            continue
        seen.add(key)
        tabs = sorted(
            {
                (r.sheet_name or "Sheet1").strip()
                for r in catalog.google_sheet_rows
                if (r.spreadsheet_name or "").strip().lower() == key
            }
        )
        options.append(
            {
                "workbook_name": sn,
                "sheet_tabs": tabs[:20],
            }
        )
    return options


def _match_pick_to_catalog(
    data: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Optional[LlmConnectorPick]:
    cid = (data.get("connector_id") or "").strip()
    ctype = (data.get("connector_type") or "").strip().lower()
    dname = (data.get("display_name") or "").strip()
    confidence = float(data.get("confidence") or 0)
    if confidence < _MIN_CONNECTOR_CONFIDENCE:
        return None

    for entry in build_connector_catalog_for_llm(catalog):
        if cid and entry.get("connector_id") == cid:
            return LlmConnectorPick(
                connector_type=str(entry["connector_type"]),
                display_name=str(entry["display_name"]),
                connector_id=entry.get("connector_id"),
                confidence=confidence,
                reasoning_short=(data.get("reasoning_short") or "").strip() or None,
            )
        if (
            ctype
            and dname
            and entry.get("connector_type") == ctype
            and str(entry.get("display_name") or "").strip().lower() == dname.lower()
        ):
            return LlmConnectorPick(
                connector_type=ctype,
                display_name=str(entry["display_name"]),
                connector_id=entry.get("connector_id"),
                confidence=confidence,
                reasoning_short=(data.get("reasoning_short") or "").strip() or None,
            )
    return None


def apply_destination_connector_pick(
    classification: Dict[str, Any],
    pick: LlmConnectorPick,
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    if pick.connector_type == "google_sheets" and catalog is not None:
        from services.planning.google_sheets_destination import apply_google_sheets_account_pick
        from services.planning.destination_catalog_validate import (
            ensure_google_sheets_connection_pin,
            mark_destination_validated_from_llm,
            strip_unvalidated_destination_confirm_flags,
            validate_destination_connector_pins,
        )

        out = apply_google_sheets_account_pick(classification, pick, catalog)
        out = ensure_google_sheets_connection_pin(out, catalog)
        if validate_destination_connector_pins(out, catalog):
            return mark_destination_validated_from_llm(out, catalog=catalog)
        return strip_unvalidated_destination_confirm_flags(out)

    out = dict(classification or {})
    out["destination"] = pick.connector_type
    out["destination_name"] = pick.display_name
    if pick.connector_id:
        out["destination_connector_id"] = pick.connector_id
    if pick.reasoning_short:
        out["_destination_connector_reasoning"] = pick.reasoning_short
    if catalog is not None:
        from services.planning.destination_catalog_validate import (
            ensure_google_sheets_connection_pin,
            mark_destination_validated_from_llm,
            strip_unvalidated_destination_confirm_flags,
            validate_destination_connector_pins,
        )

        out = ensure_google_sheets_connection_pin(out, catalog)
        if validate_destination_connector_pins(out, catalog):
            return mark_destination_validated_from_llm(out, catalog=catalog)
        return strip_unvalidated_destination_confirm_flags(out)
    out["_destination_connector_resolution"] = "llm"
    return out


def apply_source_connector_pick(
    classification: Dict[str, Any],
    pick: LlmConnectorPick,
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    """Apply catalog source connector pick to classification pins."""
    if pick.connector_type == "google_sheets" and catalog is not None:
        from services.planning.google_sheets_source import apply_google_sheets_source_account_pick
        from services.planning.source_catalog_validate import (
            ensure_google_sheets_source_connection_pin,
            mark_source_validated_from_llm,
            strip_unvalidated_source_confirm_flags,
            validate_source_connector_pins,
        )

        out = apply_google_sheets_source_account_pick(classification, pick, catalog)
        out = ensure_google_sheets_source_connection_pin(out, catalog)
        if validate_source_connector_pins(out, catalog):
            return mark_source_validated_from_llm(out, catalog=catalog)
        return strip_unvalidated_source_confirm_flags(out)

    out = dict(classification or {})
    out["source"] = pick.connector_type
    out["source_name"] = pick.display_name
    if pick.connector_id:
        out["source_connector_id"] = pick.connector_id
    if pick.reasoning_short:
        out["_source_connector_reasoning"] = pick.reasoning_short
    if catalog is not None:
        from services.planning.source_catalog_validate import (
            mark_source_validated_from_llm,
            strip_unvalidated_source_confirm_flags,
            validate_source_connector_pins,
        )

        if validate_source_connector_pins(out, catalog):
            return mark_source_validated_from_llm(out, catalog=catalog)
        return strip_unvalidated_source_confirm_flags(out)
    return out


async def llm_extract_destination_intent(
    *,
    user_id: str,
    prompt: str,
    job_summary: Dict[str, Any],
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    When PLAN_SCOPE omitted destination_phrase, ask LLM what migrate/map target the user named.
    Returns dict with optional destination_phrase / destination_same_as_source.
    """
    if not (prompt or "").strip():
        return {}

    scope_prompt = f"""Extract the user's map/migrate DESTINATION from their message.

Active extraction job (source of data — destination may be same or different):
{json.dumps(job_summary, indent=2)}

User message: "{prompt}"

Return JSON only:
{{
  "destination_phrase": "<human label: org name, SQL connection name, spreadsheet name, or null>",
  "destination_same_as_source": false,
  "confidence": 0.0-1.0
}}

Rules:
- destination_phrase: the named TARGET system the user wants (e.g. "Test DB", "SF 1", "Production org").
  Extract the label even if it equals the job source_name.
- destination_same_as_source: true ONLY when user explicitly means the same system as job source
  (e.g. "same database", "same org", "back to source") — not when they merely name a label that happens to match source.
- Do not return catalog UUIDs — human labels only.
- confidence < 0.5 → set destination_phrase null."""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks
        from utils.openai_helpers import parse_openai_json_response

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_SCOPE,
            messages=[{"role": "user", "content": scope_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=200,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content or "{}"
        data = parse_openai_json_response(raw)
        if not isinstance(data, dict):
            return {}
        if float(data.get("confidence") or 0) < _MIN_CONNECTOR_CONFIDENCE:
            return {}
        out: Dict[str, Any] = {}
        phrase = (data.get("destination_phrase") or "").strip()
        if phrase and phrase.lower() not in ("null", "unknown", "none"):
            out["destination_phrase"] = phrase
        if data.get("destination_same_as_source"):
            out["destination_same_as_source"] = True
        return out
    except Exception as exc:
        logger.debug("llm_extract_destination_intent skipped: %s", exc)
        return {}


async def llm_resolve_destination_connector(
    *,
    user_id: str,
    prompt: str,
    destination_phrase: Optional[str],
    catalog: ResourceCatalog,
    job_summary: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> Optional[LlmConnectorPick]:
    """
    Pick destination connector from the user's catalog using LLM + semantic profiles.
    No label scoring or keyword matching.
    """
    options = build_connector_catalog_for_llm(catalog)
    if not options:
        return None

    phrase_line = (
        f'"{destination_phrase}"'
        if (destination_phrase or "").strip()
        else "(infer the target system from the user message)"
    )
    pick_prompt = f"""Pick which connected system the user wants as the DESTINATION for map/migrate.

Use the user message, destination phrase, job context, and connector business semantics below.
Do not use keyword rules — reason about what the user means.

User message: "{prompt}"
Destination phrase: {phrase_line}

Active job (data was extracted from here — target may be same or different):
{json.dumps(job_summary or {}, indent=2)}

Available connectors in the user's catalog:
{json.dumps(options, indent=2)}

Return JSON only:
{{
  "connector_id": "<uuid from catalog or null>",
  "connector_type": "salesforce|sql|google_sheets",
  "display_name": "<exact display_name from catalog>",
  "confidence": 0.0-1.0,
  "reasoning_short": "one sentence"
}}

Rules:
- display_name and connector_id MUST match one catalog entry exactly.
- Pick the system the user wants data mapped/migrated INTO.
- If they name "Test DB" and a sql connector is named "Test DB", pick that connector.
- Do not default to Salesforce or the extract source unless the user clearly wants that target.
- confidence < 0.5 or no fit → connector_id null."""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks
        from utils.openai_helpers import parse_openai_json_response

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_SCOPE,
            messages=[{"role": "user", "content": pick_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=350,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content or "{}"
        data = parse_openai_json_response(raw)
        if not isinstance(data, dict):
            return None
        pick = _match_pick_to_catalog(data, catalog)
        if pick:
            logger.info(
                "[connector_llm] destination pick type=%s name=%s conf=%.2f",
                pick.connector_type,
                pick.display_name,
                pick.confidence,
            )
        return pick
    except Exception as exc:
        logger.debug("llm_resolve_destination_connector skipped: %s", exc)
        return None


async def llm_resolve_source_connector(
    *,
    user_id: str,
    prompt: str,
    source_phrase: Optional[str],
    catalog: ResourceCatalog,
    conversation_id: Optional[str] = None,
) -> Optional[LlmConnectorPick]:
    """LLM pick for source connector (same pattern as destination)."""
    options = build_connector_catalog_for_llm(catalog)
    if not options:
        return None
    if not (prompt or "").strip() and not (source_phrase or "").strip():
        return None

    phrase_line = (
        f'"{source_phrase}"'
        if (source_phrase or "").strip()
        else "(infer the source system from the user message)"
    )
    pick_prompt = f"""Pick which connected system the user means as the SOURCE.

User message: "{prompt}"
Source phrase: {phrase_line}

Available connectors:
{json.dumps(options, indent=2)}

Return JSON only:
{{"connector_id": "...", "connector_type": "salesforce|sql|google_sheets", "display_name": "...", "confidence": 0.0-1.0}}

display_name and connector_id MUST match a catalog entry. confidence < 0.5 → connector_id null."""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks
        from utils.openai_helpers import parse_openai_json_response

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_SCOPE,
            messages=[{"role": "user", "content": pick_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=300,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content or "{}"
        data = parse_openai_json_response(raw)
        if not isinstance(data, dict):
            return None
        return _match_pick_to_catalog(data, catalog)
    except Exception as exc:
        logger.debug("llm_resolve_source_connector skipped: %s", exc)
        return None


@dataclass(frozen=True)
class LlmInfoPick:
    """Info-path connector or workbook target from catalog LLM binding."""

    connector_type: str
    display_name: str
    connector_id: Optional[str]
    confidence: float
    workbook_name: Optional[str] = None
    sheet_name: Optional[str] = None
    reasoning_short: Optional[str] = None


def _match_info_pick_to_catalog(
    data: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Optional[LlmInfoPick]:
    confidence = float(data.get("confidence") or 0)
    if confidence < _MIN_CONNECTOR_CONFIDENCE:
        return None

    workbook_name = (data.get("workbook_name") or "").strip()
    sheet_name = (data.get("sheet_name") or "").strip() or None
    valid_workbooks = {
        w["workbook_name"].lower()
        for w in build_workbook_catalog_for_llm(catalog)
    }
    if workbook_name and workbook_name.lower() not in valid_workbooks:
        workbook_name = ""

    connector_pick = _match_pick_to_catalog(data, catalog)
    if workbook_name:
        ctype = "google_sheets"
        display_name = connector_pick.display_name if connector_pick else ""
        connector_id = connector_pick.connector_id if connector_pick else None
        if connector_pick and connector_pick.connector_type != "google_sheets":
            display_name = ""
            connector_id = None
        return LlmInfoPick(
            connector_type=ctype,
            display_name=display_name or workbook_name,
            connector_id=connector_id,
            confidence=confidence,
            workbook_name=workbook_name,
            sheet_name=sheet_name,
            reasoning_short=(data.get("reasoning_short") or "").strip() or None,
        )

    if not connector_pick:
        return None

    return LlmInfoPick(
        connector_type=connector_pick.connector_type,
        display_name=connector_pick.display_name,
        connector_id=connector_pick.connector_id,
        confidence=confidence,
        reasoning_short=(data.get("reasoning_short") or "").strip() or None,
    )


async def llm_resolve_info_connector(
    *,
    user_id: str,
    prompt: str,
    catalog: ResourceCatalog,
    hint_connector: Optional[str] = None,
    hint_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Optional[LlmInfoPick]:
    """
    Pick which connector or Google workbook the user is asking about (info/schema path).
    Uses catalog semantics only — no keyword phrase extraction.
    """
    connectors = build_connector_catalog_for_llm(catalog)
    workbooks = build_workbook_catalog_for_llm(catalog)
    if not connectors and not workbooks:
        return None
    if not (prompt or "").strip():
        return None

    hint_lines = []
    if (hint_connector or "").strip():
        hint_lines.append(f"Classifier connector hint: {hint_connector}")
    if (hint_name or "").strip():
        hint_lines.append(f"Classifier name hint: {hint_name}")

    pick_prompt = f"""Resolve what connected system or Google workbook the user is asking about.

This is a schema/info question (list objects, describe tables, row counts, columns) — NOT extract/migrate.

User message: "{prompt}"
{chr(10).join(hint_lines) if hint_lines else ""}

Connected systems (accounts):
{json.dumps(connectors, indent=2)}

Google workbooks (spreadsheet names):
{json.dumps(workbooks, indent=2)}

Return JSON only:
{{
  "connector_id": "<uuid from catalog or null>",
  "connector_type": "salesforce|sql|google_sheets",
  "display_name": "<exact connector display_name from catalog, or null>",
  "workbook_name": "<exact workbook_name from catalog when question is about a spreadsheet, or null>",
  "sheet_name": "<tab name or null>",
  "confidence": 0.0-1.0,
  "reasoning_short": "one sentence"
}}

Rules:
- For Salesforce/SQL info, set connector_type + display_name from the connectors list.
- For Google Sheets workbook questions, set connector_type google_sheets and workbook_name from the workbooks list.
- display_name, connector_id, and workbook_name MUST match catalog entries exactly.
- confidence < 0.5 or no fit → connector_id null and workbook_name null."""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks
        from utils.openai_helpers import parse_openai_json_response

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_SCOPE,
            messages=[{"role": "user", "content": pick_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=400,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content or "{}"
        data = parse_openai_json_response(raw)
        if not isinstance(data, dict):
            return None
        pick = _match_info_pick_to_catalog(data, catalog)
        if pick:
            logger.info(
                "[connector_llm] info pick type=%s workbook=%s conf=%.2f",
                pick.connector_type,
                pick.workbook_name or pick.display_name,
                pick.confidence,
            )
        return pick
    except Exception as exc:
        logger.debug("llm_resolve_info_connector skipped: %s", exc)
        return None


def apply_info_connector_pick(
    classification: Dict[str, Any],
    pick: LlmInfoPick,
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    out = dict(classification or {})
    out["source"] = pick.connector_type
    out["intent"] = "info"
    out["mode"] = "general"
    if pick.workbook_name:
        out["source_name"] = pick.workbook_name
        if pick.sheet_name:
            out["source_sheet_name"] = pick.sheet_name
    elif pick.display_name:
        out["source_name"] = pick.display_name
    if pick.connector_id:
        out["source_connector_id"] = pick.connector_id
    if pick.reasoning_short:
        out["_info_connector_reasoning"] = pick.reasoning_short
    if catalog is not None and pick.connector_type == "google_sheets" and pick.connector_id:
        from services.planning.destination_catalog_validate import ensure_google_sheets_connection_pin

        out = ensure_google_sheets_connection_pin(out, catalog)
    out["_info_connector_resolution"] = "llm"
    return out
