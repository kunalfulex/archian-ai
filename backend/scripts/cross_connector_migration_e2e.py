#!/usr/bin/env python3
"""
Full cross-connector E2E: plan → clarify → approve → extract → data ops → map → migrate.

  cd edison-ai/backend && ./venv/bin/python scripts/cross_connector_migration_e2e.py
  ./venv/bin/python scripts/cross_connector_migration_e2e.py --scenario sql_combine_sf
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
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


SCENARIOS: Dict[str, Dict[str, Any]] = {
    "sql_combine_sf": {
        "prompt": (
            "From SQL Test DB extract name and email from users table (limit 5 rows), "
            "combine name into full name, keep only valid emails, migrate to Contact in SF 2"
        ),
        "workbook_hints": [],
    },
    "gsheets_split_sf": {
        "prompt": (
            "From google sheets contact list extract Name and Email (first 15 rows only), "
            "split name into first and last, filter valid emails, migrate to Contact in SF 2"
        ),
        "workbook_hints": ["contact list", "contact"],
    },
    "sf_combine_sf": {
        "prompt": (
            "From SF 1 extract FirstName LastName Email from Contact (limit 8 records), "
            "combine into full name, filter valid emails, migrate to Contact in SF 2"
        ),
        "workbook_hints": [],
    },
    "sql_to_gsheets_combine": {
        "prompt": (
            "From SQL database Test DB extract first_name, last_name, email from users "
            "(limit 5 rows), combine into name, migrate to Google Sheets Customer Contact Details"
        ),
        "workbook_hints": ["customer contact"],
    },
    "sf_to_gsheets_export": {
        "prompt": (
            "From SF 1 extract FirstName LastName Email from Contact (limit 6 records), "
            "combine into full name, migrate to Google Sheets Customer Contact Details sheet"
        ),
        "workbook_hints": ["customer contact"],
    },
}


@dataclass
class RunResult:
    name: str
    ok: bool
    notes: List[str] = field(default_factory=list)
    extracted: Optional[int] = None
    migrated_ok: Optional[int] = None
    migrated_fail: Optional[int] = None


def _parse_sse_buffer(buffer: str) -> Tuple[List[Dict[str, Any]], str]:
    out: List[Dict[str, Any]] = []
    while "\n\n" in buffer:
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
    return str(evt.get("event_type") or meta.get("event_type") or evt.get("status") or "").lower()


async def _mint_token() -> Tuple[str, str]:
    from db.connection import database

    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET not set")
    row = await database.fetch_one(
        """
        SELECT id, email FROM users
        WHERE is_deleted = FALSE AND is_active = TRUE
        ORDER BY created_at ASC LIMIT 1
        """
    )
    if not row:
        raise RuntimeError("No active user")
    user_id = str(row["id"])
    token = jwt.encode(
        {"email": row["email"], "user_id": user_id, "exp": datetime.utcnow() + timedelta(hours=3)},
        JWT_SECRET,
        algorithm="HS256",
    )
    return token, user_id


async def _create_conversation(client: httpx.AsyncClient, headers: dict) -> str:
    r = await client.post(
        f"{BASE}/chat/conversations",
        headers=headers,
        json={"title": f"E2E migrate {int(time.time())}"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"create conversation HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    conv = data.get("conversation") or data
    return str(conv.get("id") or conv.get("conversation_id"))


async def _stream_architect(
    client: httpx.AsyncClient,
    headers: dict,
    *,
    prompt: str,
    conversation_id: str,
    planner_resume: Optional[Dict[str, Any]],
    timeout_s: int,
) -> List[Dict[str, Any]]:
    body: Dict[str, Any] = {
        "prompt": prompt,
        "conversation_history": [],
        "conversation_id": conversation_id,
        "planner_resume": planner_resume,
        "plan_auto_run": False,
    }
    events: List[Dict[str, Any]] = []
    deadline = time.time() + timeout_s
    buf = ""
    async with client.stream(
        "POST",
        f"{BASE}/architect/stream",
        headers=headers,
        json=body,
        timeout=timeout_s + 60,
    ) as resp:
        if resp.status_code != 200:
            text = (await resp.aread())[:1500]
            raise RuntimeError(f"architect/stream HTTP {resp.status_code}: {text}")
        async for chunk in resp.aiter_text():
            if time.time() > deadline:
                events.append({"_truncated": True, "reason": "timeout"})
                break
            buf += chunk
            parsed, buf = _parse_sse_buffer(buf)
            events.extend(parsed)
    parsed, _ = _parse_sse_buffer(buf + "\n\n")
    events.extend(parsed)
    return events


def _classification_from_events(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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
            "destination_spreadsheet_id": evt.get("destination_spreadsheet_id"),
        }
    return None


def _planner_pending(events: List[Dict[str, Any]], original_prompt: str) -> Optional[Dict[str, Any]]:
    for evt in reversed(events):
        if _evt_type(evt) in ("planner_clarification_required", "planner_plan_ready"):
            pending = evt.get("planner_pending") or (evt.get("metadata") or {}).get("planner_pending")
            if pending:
                return pending
    cls = _classification_from_events(events)
    if cls and (cls.get("needs_clarification") or cls.get("execution_plan")):
        return {
            "original_prompt": original_prompt,
            "classification": cls,
            "execution_plan": cls.get("execution_plan") or {},
            "clarifications": cls.get("clarifications") or [],
            "plan_id": cls.get("plan_id"),
        }
    return None


def _plan_ready(events: List[Dict[str, Any]]) -> bool:
    for evt in events:
        if _evt_type(evt) == "planner_plan_ready" or evt.get("status") == "plan_ready":
            return True
    cls = _classification_from_events(events)
    return bool(cls and cls.get("execution_plan") and not cls.get("needs_clarification"))


def _auto_selection(
    pending: Dict[str, Any], hints: List[str]
) -> Optional[Tuple[str, str]]:
    clarifications = pending.get("clarifications") or (pending.get("classification") or {}).get(
        "clarifications"
    ) or []
    for clar in clarifications:
        ctype = clar.get("clarification_type") or ""
        options = clar.get("options") or []
        if ctype in ("pick_workbook", "pick_workbook_ambiguous", "pick_tab"):
            for opt in options:
                if opt.get("create_new"):
                    continue
                label = (opt.get("label") or "").lower()
                if any(h.lower() in label for h in hints):
                    return clar["id"], opt["id"]
            for opt in options:
                if not opt.get("create_new"):
                    return clar["id"], opt["id"]
        if ctype == "pick_salesforce_org":
            for opt in options:
                label = (opt.get("label") or "").lower()
                if "sf 2" in label or (hints and any(h in label for h in hints)):
                    return clar["id"], opt["id"]
        if ctype == "pick_sql_connector":
            for opt in options:
                if "test" in (opt.get("label") or "").lower():
                    return clar["id"], opt["id"]
    return None


def _scan_pipeline_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "extraction_completed": False,
        "extract_count": None,
        "map_ready": False,
        "migration_started": False,
        "migration_job_id": None,
        "migration_completed": False,
        "migration_error": None,
        "errors": [],
    }
    for evt in events:
        et = _evt_type(evt)
        meta = evt.get("metadata") or {}
        if et == "extraction_completed" or evt.get("action") == "extraction_completed":
            out["extraction_completed"] = True
            cnt = evt.get("record_count") or meta.get("record_count") or evt.get("total_records")
            if cnt is not None:
                out["extract_count"] = int(cnt)
        if et == "map_fields_ready" or evt.get("action") == "map_fields_ready":
            out["map_ready"] = True
        if et == "migration_started" or evt.get("action") == "migration_started":
            out["migration_started"] = True
            jid = evt.get("job_id") or meta.get("job_id")
            if jid:
                out["migration_job_id"] = str(jid)
        if et == "migration_completed" or evt.get("status") == "migration_completed":
            out["migration_completed"] = True
            res = evt.get("result") or meta.get("result") or {}
            if isinstance(res, dict):
                summ = res.get("summary") or res
                out["migrated_ok"] = summ.get("successful") or res.get("records_migrated")
                out["migrated_fail"] = summ.get("failed") or res.get("failed_records")
        if et == "agent_step_completed" and evt.get("tool_id") == "start_migration":
            vis = evt.get("visible") or evt
            if isinstance(vis, dict) and vis.get("job_id"):
                out["migration_job_id"] = str(vis["job_id"])
                out["migration_started"] = True
        if et == "error" or evt.get("status") == "error":
            msg = evt.get("message") or meta.get("message") or str(evt)[:200]
            out["errors"].append(msg)
    return out


async def _poll_migration_job(
    client: httpx.AsyncClient, headers: dict, job_id: str, timeout_s: int = 300
) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = await client.get(f"{BASE}/job-status/{job_id}", headers=headers)
        if r.status_code != 200:
            await asyncio.sleep(2)
            continue
        data = r.json()
        status = (data.get("status") or "").lower()
        if status in ("completed", "success"):
            result = data.get("result") or {}
            summ = result.get("summary") if isinstance(result, dict) else {}
            if not isinstance(summ, dict):
                summ = {}
            return {
                "status": status,
                "successful": summ.get("successful")
                or result.get("records_migrated")
                or result.get("successful_records")
                or data.get("successful_records"),
                "failed": summ.get("failed")
                or result.get("failed_records")
                or data.get("failed_records"),
                "total": data.get("total_records")
                or result.get("total_records")
                or result.get("processed_records"),
            }
        if status in ("failed", "error", "cancelled"):
            return {"status": status, "error": data.get("error") or data}
        await asyncio.sleep(3)
    raise TimeoutError(f"migration job {job_id} not terminal in {timeout_s}s")


async def run_scenario(
    client: httpx.AsyncClient,
    headers: dict,
    name: str,
    cfg: Dict[str, Any],
) -> RunResult:
    res = RunResult(name=name, ok=True)
    prompt = cfg["prompt"]
    hints = cfg.get("workbook_hints") or []
    print(f"\n{'='*60}\n→ {name}\n   {prompt[:90]}...")

    conv_id = await _create_conversation(client, headers)
    all_events: List[Dict[str, Any]] = []

    events = await _stream_architect(
        client, headers, prompt=prompt, conversation_id=conv_id, planner_resume=None, timeout_s=120
    )
    all_events.extend(events)

    for round_i in range(6):
        pending = _planner_pending(all_events, prompt)
        if not pending:
            break
        cls = pending.get("classification") or {}
        if cls.get("needs_clarification"):
            sel = _auto_selection(pending, hints)
            if not sel:
                res.ok = False
                res.notes.append("could not auto-select clarification")
                return res
            cid, oid = sel
            print(f"  → clarification round {round_i + 1}: {oid}")
            resume = {
                "planner_pending": pending,
                "selections": [{"clarification_id": cid, "option_id": oid}],
                "approve_plan": False,
            }
            ev2 = await _stream_architect(
                client,
                headers,
                prompt="",
                conversation_id=conv_id,
                planner_resume=resume,
                timeout_s=120,
            )
            all_events.extend(ev2)
            continue
        if _plan_ready(all_events):
            break

    pending = _planner_pending(all_events, prompt)
    if not pending:
        cls = _classification_from_events(all_events)
        if cls and cls.get("execution_plan"):
            pending = {
                "original_prompt": prompt,
                "classification": cls,
                "execution_plan": cls.get("execution_plan"),
            }
    if not pending:
        res.ok = False
        res.notes.append("no planner_pending for approve")
        return res

    if not _plan_ready(all_events) and (pending.get("classification") or {}).get("needs_clarification"):
        res.ok = False
        res.notes.append("plan not ready after clarifications")
        return res

    print("  → approve_plan (full pipeline)")
    approve = {"planner_pending": pending, "approve_plan": True, "selections": []}
    ev3 = await _stream_architect(
        client,
        headers,
        prompt="",
        conversation_id=conv_id,
        planner_resume=approve,
        timeout_s=480,
    )
    all_events.extend(ev3)

    scan = _scan_pipeline_events(all_events)
    res.extracted = scan.get("extract_count")
    if scan.get("errors"):
        res.notes.extend(scan["errors"][:3])

    if not scan["extraction_completed"]:
        for evt in all_events:
            if _evt_type(evt) == "agent_step_completed" and (evt.get("tool_id") or "").startswith(
                "extract_"
            ):
                scan["extraction_completed"] = True
                vis = evt.get("visible") or evt
                if isinstance(vis, dict):
                    cnt = vis.get("record_count") or vis.get("total_records")
                    if cnt is not None:
                        res.extracted = int(cnt)
                break
        if not scan["extraction_completed"]:
            res.notes.append("extraction_completed not in SSE (checking migration)")

    if scan["migration_completed"]:
        res.migrated_ok = scan.get("migrated_ok")
        res.migrated_fail = scan.get("migrated_fail")
    elif scan.get("migration_job_id"):
        print(f"  → poll migration job {scan['migration_job_id']}")
        try:
            polled = await _poll_migration_job(client, headers, scan["migration_job_id"])
            if polled.get("status") in ("completed", "success"):
                res.migrated_ok = polled.get("successful")
                res.migrated_fail = polled.get("failed")
            else:
                res.ok = False
                res.notes.append(f"migration {polled.get('status')}: {polled.get('error')}")
        except Exception as exc:
            res.ok = False
            res.notes.append(str(exc))
    elif scan["migration_started"]:
        res.notes.append("migration started but no job_id to poll")
        res.ok = False
    else:
        res.ok = False
        res.notes.append("migration never started")

    ok_count = int(res.migrated_ok or 0)
    fail_count = int(res.migrated_fail or 0)
    if ok_count > 0:
        res.ok = True
    elif fail_count > 0 and ok_count == 0:
        res.ok = False
        res.notes.append(f"all {fail_count} records failed migration")
    elif res.migrated_ok is not None and ok_count == 0 and fail_count == 0:
        res.ok = False
        res.notes.append("0 records migrated")

    return res


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="append", dest="scenarios")
    args = parser.parse_args()
    names = args.scenarios or list(SCENARIOS.keys())

    from db.connection import database

    await database.connect()
    results: List[RunResult] = []
    try:
        token, user_id = await _mint_token()
        headers = {"Authorization": f"Bearer {token}"}
        print(f"API: {BASE}\nUser: {user_id}")

        async with httpx.AsyncClient(verify=False, timeout=600.0) as client:
            for name in names:
                cfg = SCENARIOS.get(name)
                if not cfg:
                    print(f"Unknown scenario: {name}")
                    continue
                try:
                    r = await run_scenario(client, headers, name, cfg)
                except Exception as exc:
                    r = RunResult(name=name, ok=False, notes=[str(exc)])
                results.append(r)
                mark = "✓" if r.ok else "✗"
                extra = f"extracted={r.extracted}, migrated={r.migrated_ok}"
                if r.migrated_fail:
                    extra += f", failed={r.migrated_fail}"
                if r.notes:
                    extra += f" — {'; '.join(r.notes)}"
                print(f"  {mark} {name}: {extra}")
    finally:
        await database.disconnect()

    failed = [r for r in results if not r.ok]
    print(f"\n{'='*60}\nPassed {len(results) - len(failed)}/{len(results)}")
    for r in failed:
        print(f"  ✗ {r.name}: {'; '.join(r.notes)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
