#!/usr/bin/env python3
"""
Backfill extraction_result_records table from existing extraction_results.extracted_data.

Run from backend directory:
    python scripts/backfill_extraction_result_records.py

Or with optional flags:
    python scripts/backfill_extraction_result_records.py --dry-run
    python scripts/backfill_extraction_result_records.py --limit 100
    python scripts/backfill_extraction_result_records.py --force  # Re-backfill even if records exist
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import database


BATCH_SIZE = 500


async def backfill(dry_run: bool = False, limit: Optional[int] = None, force: bool = False) -> None:
    """Backfill extraction_result_records from extraction_results."""
    await database.connect()

    try:
        # Fetch extraction_results that have non-empty extracted_data
        query = """
        SELECT id, job_id, extracted_data, record_count
        FROM extraction_results
        WHERE extracted_data IS NOT NULL
          AND jsonb_array_length(extracted_data) > 0
        ORDER BY created_at DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"

        rows = await database.fetch_all(query)
        total_jobs = len(rows)
        total_records_inserted = 0
        jobs_processed = 0
        jobs_skipped = 0
        jobs_failed = 0

        print(f"Found {total_jobs} extraction_results with data to backfill")

        for row in rows:
            result_id = row["id"]
            job_id = row["job_id"]
            extracted_data = row["extracted_data"]
            record_count = row["record_count"] or 0

            if not extracted_data:
                jobs_skipped += 1
                continue

            # Parse extracted_data (may be list or JSON string)
            if isinstance(extracted_data, str):
                try:
                    extracted_data = json.loads(extracted_data)
                except (json.JSONDecodeError, TypeError):
                    print(f"  ⚠️ Skipping job_id={job_id}: invalid JSON in extracted_data")
                    jobs_failed += 1
                    continue

            if not isinstance(extracted_data, list):
                print(f"  ⚠️ Skipping job_id={job_id}: extracted_data is not a list")
                jobs_failed += 1
                continue

            if not force:
                # Check if records already exist
                exists = await database.fetch_one(
                    """
                    SELECT 1 FROM extraction_result_records
                    WHERE job_id = :job_id LIMIT 1
                    """,
                    values={"job_id": job_id},
                )
                if exists:
                    jobs_skipped += 1
                    continue

            if dry_run:
                print(f"  [DRY RUN] Would backfill job_id={job_id}: {len(extracted_data)} records")
                jobs_processed += 1
                total_records_inserted += len(extracted_data)
                continue

            # Insert in batches
            inserted = 0
            for batch_start in range(0, len(extracted_data), BATCH_SIZE):
                batch = extracted_data[batch_start : batch_start + BATCH_SIZE]
                values_list = []
                for i, rec in enumerate(batch):
                    ordinal = batch_start + i
                    if not isinstance(rec, dict):
                        rec = {}
                    try:
                        record_json = json.dumps(rec, default=str)
                    except (TypeError, ValueError):
                        record_json = "{}"
                    values_list.append({
                        "extraction_result_id": result_id,
                        "job_id": job_id,
                        "ordinal": ordinal,
                        "record_value": record_json,
                    })
                if values_list:
                    insert_query = """
                    INSERT INTO extraction_result_records (extraction_result_id, job_id, ordinal, record)
                    VALUES (:extraction_result_id, :job_id, :ordinal, CAST(:record_value AS jsonb))
                    """
                    await database.execute_many(query=insert_query, values=values_list)
                    inserted += len(values_list)

            total_records_inserted += inserted
            jobs_processed += 1
            print(f"  ✅ job_id={job_id}: inserted {inserted} records")

    finally:
        await database.disconnect()

    print(f"\nDone: {jobs_processed} jobs backfilled, {jobs_skipped} skipped, {jobs_failed} failed")
    print(f"Total records inserted: {total_records_inserted}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill extraction_result_records from extraction_results")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without inserting")
    parser.add_argument("--limit", type=int, default=None, help="Max number of extraction_results to process")
    parser.add_argument("--force", action="store_true", help="Re-backfill even if records already exist")
    args = parser.parse_args()

    asyncio.run(backfill(dry_run=args.dry_run, limit=args.limit, force=args.force))


if __name__ == "__main__":
    main()
