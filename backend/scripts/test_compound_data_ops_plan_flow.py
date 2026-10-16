#!/usr/bin/env python3
"""Verify compound prompt builds extract + data_ops + map + migrate plan."""
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

PROMPT = (
    "extract data from contact list google sheet and migrate to same sheet, "
    "remove duplicate records and invalid email id records"
)


def _parse_sse_objects(chunk: str) -> list[dict]:
    out: list[dict] = []
    for line in chunk.split("\n"):
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


async def _mint_token() -> tuple[str, str]:
    from db.connection import database

    row = await database.fetch_one(
        """
        SELECT id, email FROM users
        WHERE is_deleted = FALSE AND is_active = TRUE
        ORDER BY created_at ASC LIMIT 1
        """
    )
    user_id = str(row["id"])
    token = jwt.encode(
        {
            "email": row["email"],
            "user_id": user_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return token, user_id


async def main() -> int:
    from db.connection import database

    await database.connect()
    try:
        token, user_id = await _mint_token()
        body = {"prompt": PROMPT, "conversation_history": [], "plan_auto_run": False}
        print(f"→ POST {BASE}/architect/stream user={user_id[:8]}…")

        events: list[dict] = []
        classification = None

        async with httpx.AsyncClient(verify=False, timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{BASE}/architect/stream",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            ) as resp:
                if resp.status_code != 200:
                    print(f"✗ HTTP {resp.status_code}")
                    return 1
                async for chunk in resp.aiter_text():
                    for obj in _parse_sse_objects(chunk):
                        events.append(obj)
                        if obj.get("event_type") == "classification_completed":
                            classification = obj
                    if classification:
                        break

        assert classification is not None
        data_ops = classification.get("data_operations") or []
        print(f"  data_operations={data_ops}")

        plan_steps = classification.get("plan_steps_summary")
        if not plan_steps and isinstance(classification.get("execution_plan"), dict):
            plan_steps = classification["execution_plan"].get("steps")

        tool_ids = []
        labels = []
        for s in plan_steps or []:
            if not isinstance(s, dict):
                continue
            tid = s.get("tool_id") or (s.get("parameters") or {}).get("tool_id")
            if tid:
                tool_ids.append(tid)
            if s.get("label"):
                labels.append(s["label"])

        print(f"  plan tool_ids={tool_ids}")
        print(f"  plan labels={labels}")

        failures = []
        if "dedupe" not in data_ops or "filter" not in data_ops:
            failures.append("classification missing dedupe/filter in data_operations")
        if "run_data_ops_plan" not in tool_ids:
            failures.append("plan missing run_data_ops_plan")
        expected = ["extract_google_sheets", "run_data_ops_plan", "map_fields", "start_migration"]
        if tool_ids != expected:
            failures.append(f"expected tools {expected}, got {tool_ids}")

        if failures:
            print("✗ FAIL:")
            for f in failures:
                print(f"  - {f}")
            return 1

        print("✓ PASS: compound plan includes data ops between extract and map/migrate")
        return 0
    finally:
        await database.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
