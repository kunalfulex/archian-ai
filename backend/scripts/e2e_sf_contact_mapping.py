#!/usr/bin/env python3
"""
E2E smoke: SF1 Contact extract → map to SF2.
Validates mapping audit, key alignment, and no invalid AccountName/AccountPhone splits.

  cd edison-ai/backend && ./venv/bin/python scripts/e2e_sf_contact_mapping.py
"""
from __future__ import annotations

import asyncio
import json
import os
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
    "extract first name, last name, email, account name, account phone "
    "from contact object of SF 1 and migrate them to SF 2"
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


def _collect_map_event(evt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if evt.get("action") == "map_fields_ready":
        return evt
    meta = evt.get("metadata") or {}
    if meta.get("event_type") == "map_fields_ready" or meta.get("action") == "map_fields_ready":
        return {**meta, **{k: v for k, v in evt.items() if k != "metadata"}}
    uv = evt.get("user_visible") or {}
    if isinstance(uv, dict) and uv.get("action") == "map_fields_ready":
        return uv
    return None


def _mapping_entry_is_invalid_split(value: Any) -> bool:
    if isinstance(value, list) and len(value) == 1:
        return True
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and (item.get("field") or "").lower() == "accountid":
                if item.get("object") == "Contact":
                    return True
    return False


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


async def _wait_extraction_row(
    client: httpx.AsyncClient, job_id: str, headers: dict, timeout_s: int = 300
) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = await client.get(f"{BASE}/extraction-results/{job_id}", headers=headers)
        if r.status_code == 200:
            body = r.json()
            row = body.get("result") if isinstance(body, dict) else None
            if row:
                return row
        rec = await client.get(
            f"{BASE}/extraction-results/{job_id}/records",
            headers=headers,
            params={"page": 1, "page_size": 1},
        )
        if rec.status_code == 200:
            data = rec.json()
            records = data.get("records") or data.get("data") or []
            if records and isinstance(records[0], dict):
                return {
                    "job_id": job_id,
                    "document_type": data.get("document_type") or "Contact",
                    "sample_record": records[0],
                }
        await asyncio.sleep(3)
    return None


async def _run() -> int:
    from db.connection import database

    await database.connect()
    try:
        token, user_id = await _mint_token()
    except Exception:
        await database.disconnect()
        raise

    headers = {"Authorization": f"Bearer {token}"}
    failures: List[str] = []

    print(f"User: {user_id}")
    print(f"Prompt: {PROMPT}\n")

    extraction_job_id: Optional[str] = None
    map_payload: Optional[Dict[str, Any]] = None

    try:
        async with httpx.AsyncClient(verify=False, timeout=600.0) as client:
            print("→ POST /architect/stream (extract + map)...")
            body = {
                "prompt": PROMPT,
                "conversation_history": [],
                "conversation_id": None,
                "planner_resume": None,
                "plan_auto_run": True,
            }
            deadline = time.time() + 480
            async with client.stream(
                "POST",
                f"{BASE}/architect/stream",
                headers=headers,
                json=body,
            ) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread())[:800]
                    print(f"✗ architect/stream HTTP {resp.status_code}: {text}")
                    return 1
                async for chunk in resp.aiter_text():
                    if time.time() > deadline:
                        failures.append("architect/stream timed out after 480s")
                        break
                    for evt in _parse_sse_chunk(chunk):
                        meta = evt.get("metadata") or {}
                        et = evt.get("event_type") or meta.get("event_type") or evt.get("status")
                        if et == "extraction_completed" or evt.get("action") == "extraction_completed":
                            extraction_job_id = str(
                                evt.get("job_id")
                                or meta.get("job_id")
                                or evt.get("source_job_id")
                                or meta.get("source_job_id")
                                or extraction_job_id
                                or ""
                            ) or extraction_job_id
                        if evt.get("job_id") and not extraction_job_id:
                            if et not in ("migration_started", "migration_completed"):
                                extraction_job_id = str(evt["job_id"])
                        if meta.get("job_id") and not extraction_job_id:
                            extraction_job_id = str(meta["job_id"])
                        m = _collect_map_event(evt)
                        if m:
                            map_payload = m
                            extraction_job_id = (
                                str(m.get("job_id") or extraction_job_id or "") or extraction_job_id
                            )

            if extraction_job_id and not map_payload:
                print(f"→ Wait for extraction row {extraction_job_id}...")
                row = await _wait_extraction_row(client, extraction_job_id, headers)
                flat = {}
                doc_type = "Contact"
                doc_desc = ""
                if row:
                    doc_type = row.get("document_type") or doc_type
                    doc_desc = row.get("document_description") or ""
                    sample = row.get("sample_record")
                    if isinstance(sample, dict):
                        flat = sample
                    else:
                        data = row.get("extracted_data")
                        if isinstance(data, list) and data:
                            flat = data[0] if isinstance(data[0], dict) else {}
                if not flat:
                    failures.append(f"Could not load sample record for job {extraction_job_id}")
                else:
                    print(f"→ POST /map-fields for job {extraction_job_id}...")
                    r = await client.post(
                        f"{BASE}/map-fields",
                        headers=headers,
                        json={
                            "job_id": extraction_job_id,
                            "destination": "salesforce",
                            "destination_name": "SF 2",
                            "document_type": doc_type,
                            "document_description": doc_desc,
                            "extracted_schema": flat,
                            "extracted_schema_type": "salesforce",
                            "source_name": "SF 1",
                            "mapping_context_summary": PROMPT,
                        },
                        timeout=180.0,
                    )
                    if r.status_code == 200:
                        map_payload = r.json()
                    else:
                        failures.append(f"map-fields HTTP {r.status_code}: {r.text[:400]}")
    finally:
        await database.disconnect()

    print("\n=== Map payload ===")
    if not map_payload:
        failures.append("No map_fields_ready event")
    else:
        mapping_cfg = map_payload.get("mapping_config") or map_payload.get("mapping") or {}
        if isinstance(mapping_cfg, dict) and "mapping" in mapping_cfg:
            mapping = mapping_cfg.get("mapping") or mapping_cfg
        else:
            mapping = mapping_cfg if isinstance(mapping_cfg, dict) else {}

        audit = map_payload.get("mapping_audit") or {}
        src_fields = map_payload.get("source_fields") or []
        print(f"  source_fields: {src_fields[:12]}")
        print(f"  mapping keys: {list(mapping.keys())}")
        print(f"  mapping_audit: {json.dumps(audit, indent=2)[:600]}")

        expected_extract = {"FirstName", "LastName", "Email", "AccountName", "AccountPhone"}
        if src_fields:
            src_set = set(src_fields)
            missing = expected_extract - src_set
            if missing:
                failures.append(f"source_fields missing expected columns: {sorted(missing)}")

        if audit:
            score = audit.get("mapping_score")
            if score is not None:
                print(f"  mapping_score: {score}")
                if float(score) < 0.75:
                    failures.append(f"mapping_score too low: {score}")
            codes = [i.get("code") for i in (audit.get("issues") or []) if isinstance(i, dict)]
            if "BASE_NAME_SPLIT_OK" not in codes:
                failures.append(f"expected BASE_NAME_SPLIT_OK in audit issues, got {codes}")
            if audit.get("allow_auto_migration") is False and audit.get("status") == "error":
                failures.append("allow_auto_migration=false with error status")
        elif map_payload.get("action") == "map_fields_ready":
            print("  ⚠ mapping_audit missing on SSE payload (check agent map step)")
        else:
            print("  ⚠ mapping_audit empty on /map-fields response (mapping_plan v2 path)")

        for key, val in mapping.items():
            if _mapping_entry_is_invalid_split(val):
                failures.append(f"invalid split mapping on '{key}': {val}")
            if key.lower() == "company" and "AccountName" in mapping:
                failures.append("duplicate company + AccountName mapping keys")

        for key in ("AccountName", "AccountPhone"):
            if key not in mapping:
                continue
            entry = mapping[key]
            if isinstance(entry, list):
                failures.append(f"{key} must not be a split array")
            elif isinstance(entry, dict):
                if entry.get("object") != "Account":
                    failures.append(f"{key} should map to Account, got {entry.get('object')}")
                if not entry.get("lookup_of"):
                    failures.append(f"{key} missing lookup_of Contact.AccountId")

    if failures:
        print("\n✗ E2E FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\n✓ SF Contact mapping E2E passed")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
