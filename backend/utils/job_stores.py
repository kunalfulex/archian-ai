"""
Central job stores for extraction, migration, and SQL tasks.
Uses Redis with in-memory fallback for scalability and persistence.
"""
from utils.redis_service import (
    assessment_job_store,
    enterprise_graph_job_store,
    extraction_job_store,
    metadata_sync_job_store,
    migration_job_store,
    sql_task_store,
)
from utils.async_job_store import AsyncJobStore

# Extraction jobs: File, Salesforce, Google Sheets
extraction_jobs = AsyncJobStore(extraction_job_store)

# Migration jobs: Salesforce, metadata deploy, etc.
migration_jobs = AsyncJobStore(migration_job_store)

# SQL extraction tasks (Redis-backed, same pattern as extraction_jobs)
sql_tasks = AsyncJobStore(sql_task_store)

# Connector data assessment jobs
assessment_jobs = AsyncJobStore(assessment_job_store)

# Connector metadata schema sync jobs
metadata_sync_jobs = AsyncJobStore(metadata_sync_job_store)

enterprise_graph_jobs = AsyncJobStore(enterprise_graph_job_store)
