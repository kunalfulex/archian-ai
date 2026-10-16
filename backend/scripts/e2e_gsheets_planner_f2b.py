#!/usr/bin/env python3
"""
Frontend→backend E2E: Google Sheets planner scenarios via /architect/stream.

Uses the same payloads as the React app (planner_resume selections, approve_plan).

  cd edison-ai/backend && ./venv/bin/python scripts/e2e_gsheets_planner_f2b.py
  ./venv/bin/python scripts/e2e_gsheets_planner_f2b.py --scenario pick_existing
  ./venv/bin/python scripts/e2e_gsheets_planner_f2b.py --execute  # also run approved plan (slow)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
import jwt
from dotenv import load_dotenv

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

load_dotenv(os.path.join(_BACKEND, ".env"))

BASE = os.getenv("SMOKE_API_BASE", "https://localhost:8000").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET")

PROMPT_EXPORT = "extract contact data from SF 1 org and deploy them to google sheet"
PROMPT_CREATE_NAMED = (
    "extract contacts from SF 1 and deploy to google sheets. "
    "create new google sheet and name it E2E Sales Contact List"
)


def _parse_sse_buffer(buffer: str) -> tuple[List[Dict[str, Any]], str]:
    """Parse complete SSE data: lines from buffer; return (events, remainder)."""
    out: List[Dict[str, Any]] = []
    while "\n\n" in buffer or buffer.strip().endswith("}"):
        if "\n\n" not in buffer:
            break
        block, buffer = buffer.split("\n\n", 1)
        payload = ""
        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                payload += line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            buffer = block + "\n\n" + buffer
            break
    return out, buffer


def _evt_type(evt: Dict[str, Any]) -> str:
    meta = evt.get("metadata") or {}
    return str(
        evt.get("event_type")
        or meta.get("event_type")
        or ""
    ).lower()


def _plan_tools_from_classification(cls: Dict[str, Any]) -> List[str]:
    plan = cls.get("execution_plan") or {}
    return [
        (s.get("parameters") or {}).get("tool_id")
        for s in (plan.get("steps") or [])
        if isinstance(s, dict)
    ]


def _plan_has_existing_workbook(cls: Dict[str, Any]) -> bool:
    plan = cls.get("execution_plan") or {}
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        params = step.get("parameters") or {}
        dest = step.get("destination") or {}
        if isinstance(dest, dict) and dest.get("spreadsheet_id"):
            return True
        if params.get("spreadsheet_id"):
            return True
    return False


async def _mint_token() -> Tuple[str, str]:
    from db.connection import database

    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET not set in .env")
    row = await database.fetch_one(
        """
        SELECT id, email FROM users
        WHERE is_deleted = FALSE AND is_active = TRUE
        ORDER BY created_at ASC
        LIMIT 1
        """
    )
    if not row:
        raise RuntimeError("No active user")
    user_id = str(row["id"])
    token = jwt.encode(
        {
            "email": row["email"],
            "user_id": user_id,
            "exp": datetime.utcnow() + timedelta(hours=2),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return token, user_id


async def _create_conversation(client: httpx.AsyncClient, headers: dict) -> str:
    r = await client.post(
        f"{BASE}/chat/conversations",
        headers=headers,
        json={"title": "E2E GSheets planner"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"create conversation HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    conv = data.get("conversation") or data
    cid = conv.get("id") or conv.get("conversation_id")
    if not cid:
        raise RuntimeError(f"no conversation id in response: {data}")
    return str(cid)


async def _stream_architect(
    client: httpx.AsyncClient,
    headers: dict,
    *,
    prompt: str,
    conversation_id: Optional[str],
    planner_resume: Optional[Dict[str, Any]],
    timeout_s: int = 120,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    body: Dict[str, Any] = {
        "prompt": prompt,
        "conversation_history": [],
        "conversation_id": conversation_id,
        "planner_resume": planner_resume,
        "plan_auto_run": False,
    }
    events: List[Dict[str, Any]] = []
    conv_id = conversation_id
    deadline = time.time() + timeout_s
    buf = ""
    async with client.stream(
        "POST",
        f"{BASE}/architect/stream",
        headers=headers,
        json=body,
        timeout=timeout_s + 30,
    ) as resp:
        if resp.status_code != 200:
            text = (await resp.aread())[:1200]
            raise RuntimeError(f"architect/stream HTTP {resp.status_code}: {text}")
        async for chunk in resp.aiter_text():
            if time.time() > deadline:
                break
            buf += chunk
            parsed, buf = _parse_sse_buffer(buf)
            for evt in parsed:
                events.append(evt)
                meta = evt.get("metadata") or {}
                if meta.get("conversation_id"):
                    conv_id = str(meta["conversation_id"])
                if evt.get("conversation_id"):
                    conv_id = str(evt["conversation_id"])
    parsed, _ = _parse_sse_buffer(buf + "\n\n")
    events.extend(parsed)
    return events, conv_id


def _classification_from_events(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build classification dict from classification_completed SSE (matches frontend)."""
    for evt in reversed(events):
        if _evt_type(evt) != "classification_completed":
            continue
        return {
            "source": evt.get("source"),
            "destination": evt.get("destination"),
            "intent": evt.get("intent"),
            "mode": evt.get("mode"),
            "source_name": evt.get("source_name"),
            "destination_name": evt.get("destination_name"),
            "needs_clarification": evt.get("needs_clarification"),
            "plan_status": evt.get("plan_status"),
            "clarifications": evt.get("clarifications"),
            "execution_plan": evt.get("execution_plan"),
            "plan_id": evt.get("plan_id"),
            "planner_create_spreadsheet": evt.get("planner_create_spreadsheet"),
            "planner_new_spreadsheet_name": evt.get("planner_new_spreadsheet_name"),
            "destination_spreadsheet_id": evt.get("destination_spreadsheet_id"),
            "planner_destination_confirmed": evt.get("planner_destination_confirmed"),
        }
    return None


def _last_classification(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return _classification_from_events(events)


def _planner_pending(events: List[Dict[str, Any]], *, original_prompt: str) -> Optional[Dict[str, Any]]:
    for evt in reversed(events):
        if _evt_type(evt) in (
            "planner_clarification_required",
            "planner_plan_ready",
        ):
            pending = evt.get("planner_pending") or (evt.get("metadata") or {}).get(
                "planner_pending"
            )
            if pending:
                return pending
    cls = _classification_from_events(events)
    if cls and cls.get("needs_clarification"):
        return {
            "original_prompt": original_prompt,
            "classification": cls,
            "execution_plan": cls.get("execution_plan") or {},
            "clarifications": cls.get("clarifications") or [],
            "plan_id": cls.get("plan_id"),
        }
    if cls and cls.get("execution_plan"):
        return {
            "original_prompt": original_prompt,
            "classification": cls,
            "execution_plan": cls.get("execution_plan"),
            "plan_id": cls.get("plan_id"),
        }
    return None


def _find_workbook_option_id(pending: Dict[str, Any], name_substr: str) -> Optional[str]:
    for clar in pending.get("clarifications") or []:
        if clar.get("clarification_type") != "pick_workbook":
            continue
        for opt in clar.get("options") or []:
            label = (opt.get("label") or "").lower()
            if name_substr.lower() in label and not opt.get("create_new"):
                return opt.get("id")
    return None


def _assert(condition: bool, msg: str, failures: List[str]) -> None:
    if condition:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
        failures.append(msg)


async def scenario_ambiguous(headers: dict, failures: List[str]) -> Optional[Dict[str, Any]]:
    print("\n[1] Export to Google Sheets (ambiguous) → workbook picker")
    async with httpx.AsyncClient(verify=False, timeout=180.0) as client:
        conv_id = await _create_conversation(client, headers)
        events, conv_id = await _stream_architect(
            client,
            headers,
            prompt=PROMPT_EXPORT,
            conversation_id=conv_id,
            planner_resume=None,
        )
    pending = _planner_pending(events, original_prompt=PROMPT_EXPORT)
    cls = _last_classification(events)
    _assert(
        pending is not None or (cls and cls.get("needs_clarification")),
        "planner clarification or needs_clarification",
        failures,
    )
    if pending:
        clarifications = pending.get("clarifications") or (cls or {}).get("clarifications") or []
        types = [c.get("clarification_type") for c in clarifications]
        _assert("pick_workbook" in types, "pick_workbook clarification present", failures)
    return pending


async def scenario_pick_existing(
    headers: dict, failures: List[str], *, execute: bool
) -> None:
    print("\n[2] Pick existing workbook → plan without create step")
    async with httpx.AsyncClient(verify=False, timeout=600.0 if execute else 180.0) as client:
        conv_id = await _create_conversation(client, headers)
        events, conv_id = await _stream_architect(
            client,
            headers,
            prompt=PROMPT_EXPORT,
            conversation_id=conv_id,
            planner_resume=None,
        )
        pending = _planner_pending(events, original_prompt=PROMPT_EXPORT)
        if not pending:
            failures.append("no pending after initial prompt")
            return
        opt_id = _find_workbook_option_id(pending, "contact list")
        if not opt_id:
            failures.append("contact list option not found in picker")
            return
        print(f"  → resume option {opt_id}")
        resume_body = {
            "planner_pending": pending,
            "selections": [{"clarification_id": pending["clarifications"][0]["id"], "option_id": opt_id}],
            "reply_text": None,
            "approve_plan": False,
        }
        events2, conv_id = await _stream_architect(
            client,
            headers,
            prompt="",
            conversation_id=conv_id,
            planner_resume=resume_body,
        )
        cls = _last_classification(events2)
        if not cls:
            failures.append("no classification after workbook pick")
            return
        tools = _plan_tools_from_classification(cls)
        _assert(
            "google_sheets_create_from_prompt" not in tools,
            "plan excludes google_sheets_create_from_prompt",
            failures,
        )
        _assert("extract_salesforce" in tools, "plan includes extract_salesforce", failures)
        pending2 = _planner_pending(
            events2, original_prompt=pending.get("original_prompt") or PROMPT_EXPORT
        ) or {
            "original_prompt": pending.get("original_prompt") or PROMPT_EXPORT,
            "classification": cls,
            "execution_plan": cls.get("execution_plan"),
        }
        resume_cls = pending2.get("classification") or cls
        _assert(
            bool(resume_cls.get("destination_spreadsheet_id"))
            or _plan_has_existing_workbook(resume_cls),
            "destination_spreadsheet_id set after workbook pick",
            failures,
        )
        if execute:
            print("  → approve_plan (full pipeline — slow)")
            approve_body = {
                "planner_pending": pending2,
                "approve_plan": True,
                "selections": [],
            }
            events3, _ = await _stream_architect(
                client,
                headers,
                prompt="",
                conversation_id=conv_id,
                planner_resume=approve_body,
                timeout_s=480,
            )
            created = any(
                _evt_type(e) == "spreadsheet_created"
                or (e.get("metadata") or {}).get("event_type") == "spreadsheet_created"
                for e in events3
            )
            _assert(not created, "must not emit spreadsheet_created for existing pick", failures)


async def scenario_create_named(headers: dict, failures: List[str]) -> None:
    print("\n[3] Create new sheet named in prompt → plan includes create step")
    async with httpx.AsyncClient(verify=False, timeout=180.0) as client:
        conv_id = await _create_conversation(client, headers)
        events, _ = await _stream_architect(
            client,
            headers,
            prompt=PROMPT_CREATE_NAMED,
            conversation_id=conv_id,
            planner_resume=None,
        )
    cls = _last_classification(events)
    pending = _planner_pending(events, original_prompt=PROMPT_CREATE_NAMED)
    state = (pending or {}).get("classification") or cls or {}
    if not state:
        failures.append("no classification for create-named prompt")
        return
    tools = _plan_tools_from_classification(state)
    if state.get("needs_clarification"):
        types = [c.get("clarification_type") for c in (state.get("clarifications") or [])]
        _assert(
            "create_workbook_name" in types or state.get("planner_create_spreadsheet"),
            "create flow needs name or has create flag",
            failures,
        )
    else:
        _assert(
            "google_sheets_create_from_prompt" in tools,
            "plan includes create step",
            failures,
        )
    _assert(
        state.get("planner_create_spreadsheet") is True
        or (state.get("planner_new_spreadsheet_name") or "").lower().find("e2e sales") >= 0
        or (state.get("destination_name") or "").lower().find("e2e sales") >= 0,
        "spreadsheet title contains E2E Sales Contact List",
        failures,
    )


async def scenario_pick_create_new(headers: dict, failures: List[str]) -> None:
    print("\n[4] Pick Create new spreadsheet → create flag / step")
    async with httpx.AsyncClient(verify=False, timeout=180.0) as client:
        conv_id = await _create_conversation(client, headers)
        events, conv_id = await _stream_architect(
            client,
            headers,
            prompt=PROMPT_EXPORT,
            conversation_id=conv_id,
            planner_resume=None,
        )
        pending = _planner_pending(events, original_prompt=PROMPT_EXPORT)
        if not pending:
            failures.append("no pending for create-new panel test")
            return
        clar = next(
            c for c in (pending.get("clarifications") or []) if c.get("clarification_type") == "pick_workbook"
        )
        resume_body = {
            "planner_pending": pending,
            "selections": [{"clarification_id": clar["id"], "option_id": "gs_create_new"}],
        }
        events2, _ = await _stream_architect(
            client,
            headers,
            prompt="",
            conversation_id=conv_id,
            planner_resume=resume_body,
        )
    cls = _last_classification(events2)
    if not cls:
        failures.append("no classification after gs_create_new")
        return
    _assert(cls.get("planner_create_spreadsheet") is True, "planner_create_spreadsheet true", failures)
    if not cls.get("needs_clarification"):
        tools = _plan_tools_from_classification(cls)
        _assert(
            "google_sheets_create_from_prompt" in tools,
            "plan includes create after gs_create_new",
            failures,
        )


async def _run() -> int:
    from db.connection import database

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        choices=["all", "ambiguous", "pick_existing", "create_named", "pick_create_new"],
        default="all",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run full approved plan for pick_existing (SF extract + migrate; slow)",
    )
    args = parser.parse_args()

    await database.connect()
    failures: List[str] = []
    try:
        token, user_id = await _mint_token()
        print(f"API: {BASE}")
        print(f"User: {user_id}")
        headers = {"Authorization": f"Bearer {token}"}

        if args.scenario in ("all", "ambiguous"):
            await scenario_ambiguous(headers, failures)
        if args.scenario in ("all", "pick_existing"):
            await scenario_pick_existing(headers, failures, execute=args.execute)
        if args.scenario in ("all", "create_named"):
            await scenario_create_named(headers, failures)
        if args.scenario in ("all", "pick_create_new"):
            await scenario_pick_create_new(headers, failures)
    finally:
        await database.disconnect()

    print("\n" + ("=" * 50))
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All GSheets planner F2B scenarios passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
