"""
Configurable batch sizes for high-volume operations.
Read from environment variables for deployment flexibility.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# SQL extraction: rows per fetchmany batch when streaming
EXTRACTION_BATCH_SIZE = int(os.getenv("EXTRACTION_BATCH_SIZE", "5000"))

# Migration: records per batch when iterating extraction_result_records
MIGRATION_BATCH_SIZE = int(os.getenv("MIGRATION_BATCH_SIZE", "500"))

# extraction_result_records: insert batch size
EXTRACTION_RESULT_RECORDS_BATCH_SIZE = int(os.getenv("EXTRACTION_RESULT_RECORDS_BATCH_SIZE", "500"))

# Google Sheets: max rows per API read (chunked reads for >10K)
GSHEETS_CHUNK_ROWS = int(os.getenv("GSHEETS_CHUNK_ROWS", "10000"))

# Google Sheets upsert: max row updates per batch_write_data call.
# Use max size to minimize API calls and avoid rate limits (60 write requests/min).
GSHEETS_BATCH_WRITE_SIZE = int(os.getenv("GSHEETS_BATCH_WRITE_SIZE", "5000"))

# Salesforce Bulk API 2.0: max records per job (10GB limit; split large jobs)
# ~10K bytes/record average -> ~1M records stays under 10GB
SF_BULK_MAX_RECORDS_PER_JOB = int(os.getenv("SF_BULK_MAX_RECORDS_PER_JOB", "500000"))

# Min batch size for migrations
MIGRATION_BATCH_SIZE_MIN = int(os.getenv("MIGRATION_BATCH_SIZE_MIN", "50"))
MIGRATION_BATCH_SIZE_MAX_SALESFORCE = int(os.getenv("MIGRATION_BATCH_SIZE_MAX_SALESFORCE", "10000"))
MIGRATION_BATCH_SIZE_MAX_DATABASE = int(os.getenv("MIGRATION_BATCH_SIZE_MAX_DATABASE", "500"))
MIGRATION_BATCH_SIZE_MAX_GSHEETS = int(os.getenv("MIGRATION_BATCH_SIZE_MAX_GSHEETS", "5000"))


def compute_migration_batch_size(total_records: int, destination: str) -> int:
    """
    Compute batch size for migration based on destination's max per-batch limit.
    - Small datasets (<= max_size): 1 batch (return total_records)
    - Large datasets (> max_size): use max_size per batch for minimal iterations
    No division by target batch count - use destination max directly.
    """
    if total_records <= 0:
        return MIGRATION_BATCH_SIZE_MIN
    dest = (destination or "").lower()
    if dest in ("sql", "database"):
        max_size = MIGRATION_BATCH_SIZE_MAX_DATABASE
    elif dest in ("google_sheets", "gsheets"):
        max_size = MIGRATION_BATCH_SIZE_MAX_GSHEETS
    else:
        max_size = MIGRATION_BATCH_SIZE_MAX_SALESFORCE  # default/salesforce

    if total_records <= max_size:
        return total_records
    return max(MIGRATION_BATCH_SIZE_MIN, max_size)
