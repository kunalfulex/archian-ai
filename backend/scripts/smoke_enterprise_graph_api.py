#!/usr/bin/env python3
"""Smoke test Enterprise Graph HTTP API (local)."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

import httpx
import jwt
from dotenv import load_dotenv

# Run from backend/: python scripts/smoke_enterprise_graph_api.py
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

load_dotenv(os.path.join(_BACKEND, ".env"))

BASE = os.getenv("SMOKE_API_BASE", "https://localhost:8000").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET")


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
        raise RuntimeError("No active user in database for smoke test")
    user_id = str(row["id"])
    payload = {
        "email": row["email"],
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=1),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return token, user_id


async def _pick_members(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    members: list[dict] = []

    sql = await client.get(f"{BASE}/api/sql-connectors/", headers=headers)
    if sql.status_code == 200:
        for c in (sql.json().get("connectors") or [])[:2]:
            if c.get("id") and c.get("is_active", True):
                members.append(
                    {
                        "connector_type": "sql",
                        "connector_id": c["id"],
                        "role": "source",
                    }
                )

    gs = await client.get(f"{BASE}/api/google-sheets-connection", headers=headers)
    if gs.status_code == 200 and gs.json().get("connected"):
        conn = gs.json().get("connection") or {}
        cid = conn.get("id")
        if cid:
            members.append(
                {
                    "connector_type": "google_sheets",
                    "connector_id": cid,
                    "role": "source",
                }
            )

    sf = await client.get(f"{BASE}/api/salesforce-connection", headers=headers)
    if sf.status_code == 200:
        for c in (sf.json().get("connections") or [])[:1]:
            oid = c.get("org_id")
            if oid:
                members.append(
                    {
                        "connector_type": "salesforce",
                        "connector_id": oid,
                        "role": "source",
                    }
                )
                break

    return members[:3]


async def main() -> int:
    from db.connection import database

    print(f"Enterprise Graph API smoke test → {BASE}\n")
    failures = 0
    await database.connect()

    try:
        return await _run_smoke(database, failures)
    finally:
        await database.disconnect()


async def _run_smoke(database, failures: int) -> int:
    async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
        # 1) Unauthenticated
        r = await client.get(f"{BASE}/api/enterprise-graphs")
        ok = r.status_code == 401
        print(f"{'✓' if ok else '✗'} GET /api/enterprise-graphs (no auth) → {r.status_code}")
        if not ok:
            failures += 1

        token, user_id = await _mint_token()
        headers = {"Authorization": f"Bearer {token}"}
        print(f"✓ Minted JWT for user {user_id[:8]}…")

        # 2) List (may be empty)
        r = await client.get(f"{BASE}/api/enterprise-graphs", headers=headers)
        ok = r.status_code == 200 and r.json().get("status") == "success"
        print(f"{'✓' if ok else '✗'} GET /api/enterprise-graphs → {r.status_code}")
        if not ok:
            print(f"    body: {r.text[:200]}")
            failures += 1

        # 3) Create graph
        r = await client.post(
            f"{BASE}/api/enterprise-graphs",
            headers=headers,
            json={"name": "Smoke test graph", "description": "API smoke test"},
        )
        ok = r.status_code == 200 and r.json().get("status") == "success"
        graph_id = (r.json().get("graph") or {}).get("id") if ok else None
        print(f"{'✓' if ok else '✗'} POST /api/enterprise-graphs → {r.status_code} id={graph_id}")
        if not ok:
            print(f"    body: {r.text[:300]}")
            failures += 1
            return 1

        # 4) Members
        members = await _pick_members(client, headers)
        if len(members) < 2:
            print(f"⚠ Only {len(members)} connector(s) available; need 2+ for member PUT test")
            print("  Skipping PUT /members (add SQL + Sheets or SF connections first)")
        else:
            r = await client.put(
                f"{BASE}/api/enterprise-graphs/{graph_id}/members",
                headers=headers,
                json={"members": members},
            )
            ok = r.status_code == 200 and len(r.json().get("members") or []) == len(members)
            print(f"{'✓' if ok else '✗'} PUT /members ({len(members)} connectors) → {r.status_code}")
            if not ok:
                print(f"    body: {r.text[:300]}")
                failures += 1

        # 5) GET graph detail
        r = await client.get(f"{BASE}/api/enterprise-graphs/{graph_id}", headers=headers)
        g = r.json().get("graph") or {}
        ok = r.status_code == 200 and g.get("name") == "Smoke test graph"
        member_count = len(g.get("members") or [])
        print(f"{'✓' if ok else '✗'} GET /api/enterprise-graphs/{{id}} → {r.status_code} members={member_count}")
        if not ok:
            failures += 1

        # 6) Archive
        r = await client.delete(f"{BASE}/api/enterprise-graphs/{graph_id}", headers=headers)
        ok = r.status_code == 200
        print(f"{'✓' if ok else '✗'} DELETE /api/enterprise-graphs/{{id}} (archive) → {r.status_code}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"FAILED ({failures} step(s))")
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
