#!/usr/bin/env python3
"""
Cross-connector regression: planning + map-fields transform checks (no full migration).

  cd edison-ai/backend && ./venv/bin/python scripts/cross_connector_regression.py
  ./venv/bin/python scripts/cross_connector_regression.py --scenario sql_split_sf
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
from typing import Any, Dict, List, Optional

import httpx
import jwt
from dotenv import load_dotenv

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

load_dotenv(os.path.join(_BACKEND, ".env"))

BASE = os.getenv("SMOKE_API_BASE", "https://localhost:8000").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET")


SCENARIOS = {
    "sql_combine_sf": {
        "prompt": (
            "extract name and email from users table in Test DB, "
            "combine name into full name, filter valid emails, migrate to Contact in SF 2"
        ),
        "expect_tools": ["extract_sql", "map_fields"],
        "expect_tools_optional": ["run_data_ops_plan"],
        "map_destination": ("salesforce", "SF 2"),
        "map_sample": {"name": "Jane Doe", "email": "jane@example.com"},
        "map_type": "sql",
        "expect_transform": {"programmatic_split", "programmatic_combine", "none_aligned"},
    },
    "gsheets_split_sf": {
        "prompt": (
            "extract Name and Email from google sheets contact list, "
            "split name into first and last, filter valid emails, migrate to Contact in SF 2"
        ),
        "expect_tools": ["extract_google_sheets", "map_fields"],
        "expect_tools_optional": ["run_data_ops_plan"],
        "map_destination": ("salesforce", "SF 2"),
        "map_sample": {"Name": "John Smith", "Email": "john@example.com"},
        "map_type": "gsheets",
        "expect_transform": {"programmatic_split", "none_aligned"},
        "may_clarify": True,
    },
    "sf_combine_sf": {
        "prompt": (
            "extract FirstName LastName Email from Contact in SF 1, "
            "combine into full name, filter valid emails, migrate to Contact in SF 2"
        ),
        "expect_tools": ["extract_salesforce", "map_fields"],
        "expect_tools_optional": ["run_data_ops_plan"],
        "map_destination": ("salesforce", "SF 2"),
        "map_sample": {"FirstName": "Ann", "LastName": "Lee", "Email": "ann@example.com"},
        "map_type": "salesforce",
        "expect_transform": {"programmatic_combine", "none_aligned"},
    },
    "sql_to_gsheets_combine": {
        "prompt": (
            "from SQL database Test DB extract first_name, last_name, email from users table, "
            "combine first and last into name, migrate to Google Sheets customer contact details"
        ),
        "expect_tools": ["extract_sql", "map_fields"],
        "expect_tools_optional": ["run_data_ops_plan"],
        "map_destination": ("google_sheets", "Customer Contact Details"),
        "map_sample": {"first_name": "Bob", "last_name": "Jones", "email": "bob@example.com"},
        "map_type": "sql",
        "expect_transform": {"programmatic_combine"},
        "may_clarify": True,
    },
    "extract_only_sql": {
        "prompt": "extract name and email from users table in Test DB only, do not migrate",
        "expect_tools": ["extract_sql"],
        "forbid_tools": ["start_migration", "map_fields"],
        "plan_only": True,
    },
    "sf_extract_only": {
        "prompt": "extract FirstName and Email from Contact in SF 1 only, no migration",
        "expect_tools": ["extract_salesforce"],
        "forbid_tools": ["start_migration"],
        "plan_only": True,
    },
    "edge_compound_to_lastname": {
        "prompt": None,
        "direct_map": True,
        "map_destination": ("salesforce", "SF 2"),
        "map_sample": {"name": "Maria Garcia", "email": "maria@example.com"},
        "map_type": "sql",
        "mapping_context_summary": "migrate compound name to Contact in SF 2",
        "expect_transform": {"programmatic_split"},
    },
    "edge_firstlast_aligned": {
        "prompt": None,
        "direct_map": True,
        "map_destination": ("salesforce", "SF 2"),
        "map_sample": {"first_name": "Tom", "last_name": "Hanks", "email": "tom@example.com"},
        "map_type": "sql",
        "expect_transform": {"none_aligned"},
    },
}


@dataclass
class ScenarioResult:
    name: str
    ok: bool
    notes: List[str] = field(default_factory=list)
    plan_tools: List[str] = field(default_factory=list)
    transform_resolution: Optional[str] = None
    clarifications: List[str] = field(default_factory=list)


def _parse_sse(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return out


async def _mint_token() -> str:
    from db.connection import database

    await database.connect()
    row = await database.fetch_one(
        """
        SELECT id, email FROM users
        WHERE is_deleted = FALSE AND is_active = TRUE
        ORDER BY created_at ASC LIMIT 1
        """
    )
    if not row or not JWT_SECRET:
        raise RuntimeError("No user or JWT_SECRET")
    token = jwt.encode(
        {
            "email": row["email"],
            "user_id": str(row["id"]),
            "exp": datetime.utcnow() + timedelta(hours=2),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return token


async def _plan_from_stream(
    client: httpx.AsyncClient,
    headers: dict,
    prompt: str,
    *,
    timeout_s: int = 120,
) -> tuple[List[str], List[str], Optional[Dict]]:
    tools: List[str] = []
    clar_types: List[str] = []
    pending: Optional[Dict] = None
    deadline = time.time() + timeout_s
    async with client.stream(
        "POST",
        f"{BASE}/architect/stream",
        headers=headers,
        json={
            "prompt": prompt,
            "conversation_history": [],
            "plan_auto_run": False,
        },
    ) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"stream HTTP {resp.status_code}: {(await resp.aread())[:300]}")
        async for chunk in resp.aiter_text():
            if time.time() > deadline:
                break
            for evt in _parse_sse(chunk):
                meta = evt.get("metadata") or {}
                et = evt.get("event_type") or meta.get("event_type") or ""
                if et == "planner_clarification_required":
                    pending = evt.get("planner_pending") or meta.get("planner_pending") or evt
                    for c in evt.get("clarifications") or meta.get("clarifications") or []:
                        clar_types.append(c.get("clarification_type") or "?")
                if et == "planner_plan_ready" or evt.get("status") == "plan_ready":
                    steps = evt.get("steps") or meta.get("plan_steps_summary") or []
                    for s in steps:
                        tid = (s.get("tool_id") or s.get("id") or "").strip()
                        if tid:
                            tools.append(tid)
                    if not tools:
                        plan = evt.get("execution_plan") or meta.get("execution_plan") or {}
                        for s in (plan.get("steps") or []):
                            p = s.get("parameters") or {}
                            tid = p.get("tool_id")
                            if tid:
                                tools.append(tid)
    return tools, clar_types, pending


async def _map_fields(
    client: httpx.AsyncClient,
    headers: dict,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    dest, dest_name = cfg["map_destination"]
    body = {
        "extracted_schema": cfg["map_sample"],
        "document_type": "Contact",
        "document_description": "Regression sample",
        "destination": dest,
        "destination_name": dest_name or "SF 2",
        "extracted_schema_type": cfg.get("map_type") or "file",
        "mapping_context_summary": cfg.get("mapping_context_summary")
        or cfg.get("prompt")
        or "map fields",
    }
    if cfg.get("map_type") == "sql":
        body["destination_connector_id"] = None
    r = await client.post(f"{BASE}/map-fields", headers=headers, json=body, timeout=180.0)
    if r.status_code != 200:
        raise RuntimeError(f"map-fields HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def _transform_from_map_payload(payload: Dict[str, Any]) -> Optional[str]:
    audit = payload.get("mapping_audit") or {}
    hints = audit.get("hints") or []
    for h in hints:
        if isinstance(h, dict) and "resolution" in str(h.get("code", "")).lower():
            pass
    rules = (payload.get("transformation_rules") or {}).get("fields") or []
    if rules:
        a0 = (rules[0].get("action") or "")
        if a0.startswith("split"):
            return "programmatic_split"
        if a0 == "combine":
            return "programmatic_combine"
    em = payload.get("enhanced_mapping") or {}
    for v in em.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            if v[0].get("is_split_field"):
                return "programmatic_split"
        if isinstance(v, dict) and v.get("is_combined_field"):
            return "programmatic_combine"
    mapping = payload.get("mapping") or {}
    if mapping:
        return "none_aligned"
    return None


async def run_scenario(
    client: httpx.AsyncClient,
    headers: dict,
    name: str,
    cfg: Dict[str, Any],
) -> ScenarioResult:
    res = ScenarioResult(name=name, ok=True)

    if cfg.get("direct_map"):
        try:
            payload = await _map_fields(client, headers, cfg)
            res.transform_resolution = _transform_from_map_payload(payload)
            expected = cfg.get("expect_transform") or set()
            if expected and res.transform_resolution not in expected:
                res.ok = False
                res.notes.append(
                    f"transform {res.transform_resolution!r} not in {expected}"
                )
            if payload.get("destination_object_api_name"):
                res.notes.append(f"target={payload.get('destination_object_api_name')}")
        except Exception as e:
            res.ok = False
            res.notes.append(str(e))
        return res

    prompt = cfg["prompt"]
    assert prompt
    try:
        tools, clar_types, pending = await _plan_from_stream(client, headers, prompt)
        res.plan_tools = tools
        res.clarifications = clar_types

        if cfg.get("may_clarify") and clar_types and not tools:
            res.notes.append("stopped at clarification (manual pick required in UI)")
            return res

        for t in cfg.get("expect_tools") or []:
            if t not in tools:
                res.ok = False
                res.notes.append(f"missing plan tool {t}")
        optional = set(cfg.get("expect_tools_optional") or [])
        if optional and not optional.intersection(tools):
            res.notes.append(f"optional tools absent: {sorted(optional)}")
        for t in cfg.get("forbid_tools") or []:
            if t in tools:
                res.ok = False
                res.notes.append(f"forbidden tool present: {t}")

        if cfg.get("plan_only"):
            return res

        if cfg.get("map_sample") and not clar_types:
            mpayload = await _map_fields(client, headers, {**cfg, "prompt": prompt})
            res.transform_resolution = _transform_from_map_payload(mpayload)
            expected = cfg.get("expect_transform") or set()
            if expected and res.transform_resolution not in expected:
                res.ok = False
                res.notes.append(
                    f"map transform {res.transform_resolution!r} not in {expected}"
                )
    except Exception as e:
        res.ok = False
        res.notes.append(str(e))

    return res


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="append", dest="scenarios")
    args = parser.parse_args()
    names = args.scenarios or list(SCENARIOS.keys())

    token = await _mint_token()
    headers = {"Authorization": f"Bearer {token}"}

    print(f"API: {BASE}\n")
    results: List[ScenarioResult] = []

    try:
        async with httpx.AsyncClient(verify=False, timeout=300.0) as client:
            for name in names:
                cfg = SCENARIOS.get(name)
                if not cfg:
                    print(f"✗ unknown scenario {name}")
                    continue
                print(f"→ {name}...")
                r = await run_scenario(client, headers, name, cfg)
                results.append(r)
                mark = "✓" if r.ok else "✗"
                extra = ", ".join(
                    x
                    for x in [
                        f"tools={r.plan_tools}" if r.plan_tools else "",
                        f"clarify={r.clarifications}" if r.clarifications else "",
                        f"transform={r.transform_resolution}" if r.transform_resolution else "",
                        *r.notes,
                    ]
                    if x
                )
                print(f"  {mark} {name}" + (f" — {extra}" if extra else ""))
    finally:
        from db.connection import database

        await database.disconnect()

    failed = [r for r in results if not r.ok]
    print(f"\n{'='*50}")
    print(f"Passed {len(results) - len(failed)}/{len(results)}")
    if failed:
        for r in failed:
            print(f"  ✗ {r.name}: {'; '.join(r.notes)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
