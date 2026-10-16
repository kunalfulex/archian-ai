#!/usr/bin/env python3
"""Mint JWT and write Playwright storageState for local E2E (same user as API smoke tests)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from dotenv import load_dotenv

E2E_DIR = Path(__file__).resolve().parents[1]
BACKEND = E2E_DIR.parent / "backend"
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env")

OUT = E2E_DIR / "auth.json"
ORIGIN = os.getenv("E2E_FRONTEND_URL", "https://localhost:3000").rstrip("/")


async def main() -> int:
    from db.connection import database

    secret = os.getenv("JWT_SECRET")
    if not secret:
        print("JWT_SECRET missing in backend/.env", file=sys.stderr)
        return 1

    await database.connect()
    try:
        row = await database.fetch_one(
            """
            SELECT id, email FROM users
            WHERE is_deleted = FALSE AND is_active = TRUE
            ORDER BY created_at ASC
            LIMIT 1
            """
        )
        if not row:
            print("No active user", file=sys.stderr)
            return 1
        token = jwt.encode(
            {
                "email": row["email"],
                "user_id": str(row["id"]),
                "exp": datetime.utcnow() + timedelta(hours=4),
            },
            secret,
            algorithm="HS256",
        )
    finally:
        await database.disconnect()

    state = {
        "cookies": [],
        "origins": [
            {
                "origin": ORIGIN,
                "localStorage": [{"name": "token", "value": token}],
            }
        ],
    }
    OUT.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} for {ORIGIN} (user {row['email']})")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
