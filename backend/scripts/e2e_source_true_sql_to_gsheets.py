#!/usr/bin/env python3
"""
E2E smoke: SQL Test DB users → Google Sheets Customer Contact Details.
Validates source-true extract + map source_fields / combine rules via API.

  cd edison-ai/backend && ./venv/bin/python scripts/e2e_source_true_sql_to_gsheets.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
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

PROMPT = (
    "extract customer data from Test DB from users table and migrate them to "
    "customer contact details sheet"
)


def _parse_sse_chunk(text: str) -> List[Dict[str, Any]]:
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
        "exp": datetime.utcnow() + timedelta(hours=2),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return token, user_id


def _keys_from_records(records: List[Dict]) -> set:
    keys = set()
    for r in records[:5]:
        if isinstance(r, dict):
            keys.update(k for k in r.keys() if isinstance(k, str))
    return keys


async def _poll_sql_job(client: httpx.AsyncClient, job_id: str, headers: dict, timeout_s: int = 180) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = await client.get(f"{BASE}/job-status/{job_id}", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"task-status HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        status = (data.get("status") or "").lower()
        if status in ("completed", "success"):
            return data
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"SQL job {status}: {data.get('error') or data}")
        await asyncio.sleep(2)
    raise TimeoutError(f"SQL job {job_id} not completed in {timeout_s}s")


async def _run() -> int:
    from db.connection import database

    await database.connect()
    try:
        token, user_id = await _mint_token()
    finally:
        pass

    headers = {"Authorization": f"Bearer {token}"}
    failures: List[str] = []

    print(f"User: {user_id}")
    print(f"Prompt: {PROMPT}\n")

    job_id: Optional[str] = None
    extraction_records: List[Dict] = []
    map_payload: Optional[Dict] = None

    async with httpx.AsyncClient(verify=False, timeout=300.0) as client:
        print("→ POST /architect/stream (plan + extract, truncated read)...")
        body = {
            "prompt": PROMPT,
            "conversation_history": [],
            "conversation_id": None,
            "planner_resume": None,
            "plan_auto_run": True,
        }
        buffer = ""
        async with client.stream(
            "POST",
            f"{BASE}/architect/stream",
            headers=headers,
            json=body,
        ) as resp:
            if resp.status_code != 200:
                print(f"✗ architect/stream HTTP {resp.status_code}: {(await resp.aread())[:500]}")
                return 1
            async for chunk in resp.aiter_text():
                buffer += chunk
                for evt in _parse_sse_chunk(chunk):
                    et = evt.get("event_type") or evt.get("status")
                    if evt.get("job_id"):
                        job_id = evt["job_id"]
                    if et == "extraction_completed" or evt.get("event_type") == "extraction_completed":
                        data = evt.get("data") or evt.get("extracted_data") or []
                        if isinstance(data, list):
                            extraction_records = data
                    meta = evt.get("metadata") or {}
                    if meta.get("job_id"):
                        job_id = meta["job_id"]
                    if meta.get("event_type") == "extraction_completed":
                        d = meta.get("extracted_data") or meta.get("data")
                        if isinstance(d, list):
                            extraction_records = d
                    if evt.get("action") == "map_fields_ready":
                        map_payload = evt
                    if meta.get("event_type") == "map_fields_ready":
                        map_payload = meta

        if job_id and not extraction_records:
            print(f"→ Poll SQL job {job_id}...")
            try:
                task = await _poll_sql_job(client, job_id, headers)
                result = task.get("result") or {}
                extraction_records = result.get("data") or result.get("extracted_data") or []
            except Exception as exc:
                failures.append(f"SQL job poll: {exc}")

        if job_id and not map_payload:
            print(f"→ POST /map-fields for job {job_id}...")
            r = await client.post(
                f"{BASE}/map-fields",
                headers=headers,
                json={"job_id": job_id, "destination": "google_sheets"},
                timeout=120.0,
            )
            if r.status_code == 200:
                map_payload = r.json()
            else:
                failures.append(f"map-fields HTTP {r.status_code}: {r.text[:400]}")

    await database.disconnect()

    print("\n=== Extraction schema ===")
    keys = _keys_from_records(extraction_records)
    print(f"  columns ({len(keys)}): {sorted(keys)[:20]}")
    if keys & {"first_name", "last_name"}:
        print("  ✓ source-true: first_name / last_name present")
    elif keys & {"Name"} and "first_name" not in keys:
        failures.append("Extraction looks destination-shaped (Name without first_name/last_name)")

    if extraction_records:
        name_vals = [
            str(r.get("Name") or r.get("name") or "")
            for r in extraction_records[:5]
            if isinstance(r, dict)
        ]
        if name_vals and all(len(v.split()) <= 1 and v for v in name_vals if v):
            failures.append(f"Name column may be last-name-only sample: {name_vals[:3]}")

    print("\n=== Map payload ===")
    if not map_payload:
        failures.append("No map_fields_ready / map-fields response")
    else:
        src_fields = map_payload.get("source_fields") or []
        mapping = map_payload.get("mapping") or {}
        mt = map_payload.get("mapping_transformation") or map_payload.get("transformation_rules") or {}
        print(f"  source_fields: {src_fields[:15]}")
        print(f"  mapping keys: {list(mapping.keys())[:15]}")
        combine_rules = [
            f for f in (mt.get("fields") or [])
            if isinstance(f, dict) and f.get("action") == "combine"
        ]
        if combine_rules:
            print(f"  ✓ combine rules: {combine_rules}")
        elif "first_name" in mapping and "last_name" in mapping:
            m1 = mapping.get("first_name") or {}
            m2 = mapping.get("last_name") or {}
            if isinstance(m1, dict) and isinstance(m2, dict):
                c1 = m1.get("column") or m1.get("field")
                c2 = m2.get("column") or m2.get("field")
                if c1 and c1 == c2:
                    print(f"  ✓ mapping combine via same column: {c1}")
                else:
                    failures.append("first_name/last_name not combined to same dest column")

    if failures:
        print("\n✗ E2E FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\n✓ E2E API smoke passed")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
