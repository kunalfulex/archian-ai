#!/usr/bin/env python3
"""
Frontend→backend integration smoke: new prompt after completed migration must NOT
resume stale extract clarification (no "Additional detail:" merge).

Run from backend/:
  ./venv/bin/python scripts/test_new_request_session_flow.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

import httpx
import jwt
from dotenv import load_dotenv

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

load_dotenv(os.path.join(_BACKEND, ".env"))

BASE = os.getenv("SMOKE_API_BASE", "https://localhost:8000").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET")

ORIGINAL_PROMPT = "extarct customer data from Test Db and migrate to same database"
NEW_PROMPT = "extract data from contact list google sheet and migrate them to same sheet"


def _parse_sse_objects(buffer: str) -> list[dict]:
    out: list[dict] = []
    for line in buffer.split("\n"):
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


def _build_stale_history() -> list[dict]:
    """Simulates chat after Test DB migration + unresolved clarification metadata."""
    return [
        {
            "type": "user",
            "text": ORIGINAL_PROMPT,
        },
        {
            "type": "assistant",
            "text": "Execution plan ready",
            "metadata": {
                "event_type": "planner_plan_ready",
                "plan_status": "approved",
                "planner_pending": {
                    "original_prompt": ORIGINAL_PROMPT,
                    "classification": {
                        "source": "sql",
                        "destination": "sql",
                        "source_name": "Test DB",
                        "destination_name": "Test DB",
                        "plan_status": "approved",
                    },
                },
            },
            "plannerResolved": True,
        },
        {
            "type": "assistant",
            "metadata": {
                "event_type": "planner_plan_execution_clarification",
                "planner_pending": {
                    "extract_step_clarification": True,
                    "plan_execution_pending": True,
                    "original_prompt": ORIGINAL_PROMPT,
                    "classification": {
                        "source": "sql",
                        "destination": "sql",
                        "source_name": "Test DB",
                        "destination_name": "Test DB",
                        "plan_status": "approved",
                    },
                },
            },
            "plannerResolved": True,
        },
        {
            "type": "assistant",
            "metadata": {
                "event_type": "migration_completed",
                "job_id": "sql_test-job",
            },
        },
        {
            "type": "user",
            "text": NEW_PROMPT,
        },
    ]


async def _mint_token() -> tuple[str, str]:
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
        raise RuntimeError("No active user in database")
    user_id = str(row["id"])
    payload = {
        "email": row["email"],
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=1),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return token, user_id


async def main() -> int:
    from db.connection import database

    await database.connect()
    try:
        return await _run_test()
    finally:
        await database.disconnect()


async def _run_test() -> int:
    token, user_id = await _mint_token()
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "prompt": NEW_PROMPT,
        "conversation_history": _build_stale_history(),
        "conversation_id": None,
        "planner_resume": None,
        "plan_auto_run": False,
    }

    print(f"→ POST {BASE}/architect/stream")
    print(f"  user_id={user_id}")
    print(f"  prompt={NEW_PROMPT!r}")

    events: list[dict] = []
    classification: dict | None = None
    merged_detected = False

    async with httpx.AsyncClient(verify=False, timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{BASE}/architect/stream",
            headers=headers,
            json=body,
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                print(f"✗ HTTP {resp.status_code}: {text[:500]}")
                return 1

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                for obj in _parse_sse_objects(chunk):
                    events.append(obj)
                    et = obj.get("event_type")
                    if et == "classification_completed":
                        classification = obj
                    excerpt = json.dumps(obj)
                    if "Additional detail:" in excerpt and ORIGINAL_PROMPT in excerpt:
                        merged_detected = True
                if classification:
                    break

    print(f"\n→ Received {len(events)} SSE event(s) (truncated early after classify)")

    if merged_detected:
        print("✗ FAIL: SSE still shows merged prompt (Additional detail:)")
        return 1

    if not classification:
        print("✗ FAIL: No classification_completed event received")
        for e in events[:8]:
            print(f"  sample event: {json.dumps(e)[:200]}")
        return 1

    plan_steps = classification.get("plan_steps_summary")
    if not plan_steps and isinstance(classification.get("execution_plan"), dict):
        plan_steps = classification["execution_plan"].get("steps")
    if not plan_steps:
        for e in events:
            if e.get("event_type") == "planner_plan_ready":
                plan_steps = e.get("steps")
                break

    if plan_steps:
        tool_ids = []
        for s in plan_steps:
            if not isinstance(s, dict):
                continue
            tid = s.get("tool_id") or (s.get("parameters") or {}).get("tool_id")
            if tid:
                tool_ids.append(tid)
        print(f"\n  plan tool_ids={tool_ids}")

    source = (classification.get("source") or "").lower()
    dest = (classification.get("destination") or "").lower()
    plan_status = classification.get("plan_status")
    plan_thread = classification.get("plan_thread_active")

    print("\nClassification result:")
    print(f"  source={source!r} destination={dest!r}")
    print(f"  plan_status={plan_status!r} plan_thread_active={plan_thread!r}")

    failures: list[str] = []
    if "Additional detail:" in NEW_PROMPT:
        failures.append("prompt itself corrupted")
    if plan_thread:
        failures.append("plan_thread_active should be false for fresh request")
    if source == "sql" and "google" in NEW_PROMPT.lower():
        failures.append(
            f"expected google_sheets routing for sheet prompt, got source={source!r}"
        )

    if failures:
        print("\n✗ FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\n✓ PASS: New prompt classified fresh (no stale plan merge)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
