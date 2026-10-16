#!/usr/bin/env python3
"""Queue background entity labeling for all pending entities (per user or all users)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from db.connection import database  # noqa: E402
from services.labeling.backfill import backfill_user_labeling  # noqa: E402
from services.labeling.store import LabelingStore  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill connector entity semantics")
    parser.add_argument("--user-id", help="Limit to a single user UUID")
    args = parser.parse_args()

    await database.connect()
    await LabelingStore.ensure_tables()

    if args.user_id:
        user_ids = [args.user_id]
    else:
        rows = await database.fetch_all("SELECT id::text AS id FROM users ORDER BY created_at")
        user_ids = [dict(r)["id"] for r in rows or [] if dict(r).get("id")]

    total_jobs = 0
    for uid in user_ids:
        result = await backfill_user_labeling(uid)
        n = result.get("jobs_queued", 0)
        total_jobs += n
        if n:
            print(f"user={uid} queued {n} labeling job(s): {result.get('job_ids')}")

    print(f"Done. {total_jobs} labeling job(s) queued for {len(user_ids)} user(s).")
    await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
