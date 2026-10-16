"""
Salesforce Bulk API 2.0 Utility Module

This module provides functions for interacting with Salesforce Bulk API 2.0
to support high-volume data migrations (100K+ records).

Key Features:
- Bulk query operations (for finding existing records)
- Bulk insert/update operations
- CSV generation and parsing
- Job status polling and result retrieval
- Handles large datasets efficiently (200K+ records)
- Phase 3: 429/503 retry with exponential backoff and Retry-After
"""

import json
import csv
import io
import time
import asyncio
from typing import Optional, Dict, Any, List, Tuple, Callable, Awaitable
import aiohttp
from utils.salesforceapimigration import _new_session, format_soql_value

# Phase 3: Retry on rate limit / server error
BULK_API_MAX_RETRIES = 3


async def create_bulk_query_job(
    object_name: str,
    query: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> str:
    """
    Create a Bulk API 2.0 query job.
    
    The SOQL query is sent in the JSON request body, not the URL.
    This allows for much longer queries than REST API.
    
    Args:
        object_name: Salesforce object name (e.g., "Contact")
        query: Full SOQL query string
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        Job ID string
    
    Raises:
        Exception: If job creation fails
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "operation": "query",
        "query": query,
        "contentType": "CSV",
        "lineEnding": "LF"
    }
    # Note: 'object' field is NOT included for query jobs - it's inferred from the SOQL query
    
    async with _new_session() as session:
        for attempt in range(BULK_API_MAX_RETRIES):
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status in [200, 201]:
                    result = await response.json()
                    return result['id']
                if response.status in (429, 503) and attempt < BULK_API_MAX_RETRIES - 1:
                    retry_after = response.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after and str(retry_after).isdigit() else (2 ** attempt)
                    await asyncio.sleep(min(delay, 60))
                    continue
                error_text = await response.text()
                raise Exception(f"Failed to create bulk query job: {error_text}")


async def get_bulk_query_job_status(
    job_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Dict[str, Any]:
    """
    Get the status of a Bulk API 2.0 query job.
    
    Args:
        job_id: Bulk API 2.0 query job ID
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        Job status dictionary with fields like:
        - state: Job state (UploadComplete, InProgress, JobComplete, Failed, Aborted)
        - totalRecords: Total records processed
        - locator: Pagination locator (if results are paginated)
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/query/{job_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with _new_session() as session:
        for attempt in range(BULK_API_MAX_RETRIES):
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                if response.status in (429, 503) and attempt < BULK_API_MAX_RETRIES - 1:
                    retry_after = response.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after and str(retry_after).isdigit() else (2 ** attempt)
                    await asyncio.sleep(min(delay, 60))
                    continue
                error_text = await response.text()
                raise Exception(f"Failed to get query job status: {error_text}")


async def abort_bulk_query_job(
    job_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Dict[str, Any]:
    """
    Abort a Bulk API 2.0 query job.
    PATCH the job state to Aborted.

    Args:
        job_id: Bulk API 2.0 query job ID
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)

    Returns:
        Updated job status dictionary
    """
    try:
        current = await get_bulk_query_job_status(
            job_id, access_token, instance_url, api_version
        )
        if current.get("state") in ("JobComplete", "Failed", "Aborted"):
            return current
    except Exception:
        pass

    url = f"{instance_url}/services/data/{api_version}/jobs/query/{job_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {"state": "Aborted"}

    async with _new_session() as session:
        async with session.patch(url, headers=headers, json=payload) as response:
            if response.status not in [200, 201]:
                error_text = await response.text()
                raise Exception(f"Failed to abort bulk query job: {error_text}")
            return await response.json()


async def abort_bulk_ingest_job(
    job_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Dict[str, Any]:
    """
    Abort a Bulk API 2.0 ingest (data) job.
    PATCH the job state to Aborted.

    Args:
        job_id: Bulk API 2.0 ingest job ID
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)

    Returns:
        Updated job status dictionary
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/ingest/{job_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {"state": "Aborted"}

    async with _new_session() as session:
        async with session.patch(url, headers=headers, json=payload) as response:
            if response.status not in [200, 201]:
                error_text = await response.text()
                raise Exception(f"Failed to abort bulk ingest job: {error_text}")
            return await response.json()


async def wait_for_bulk_query_job_completion(
    job_id: str,
    access_token: str,
    instance_url: str,
    poll_interval: int = 5,
    max_wait_time: int = 3600,
    api_version: str = "v59.0",
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Dict[str, Any]:
    """
    Poll Bulk API 2.0 query job status until completion or timeout.
    
    Query jobs can take longer than data jobs, especially for large datasets.
    
    Args:
        job_id: Bulk API 2.0 query job ID
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        poll_interval: Seconds between status checks (default: 5)
        max_wait_time: Maximum time to wait in seconds (default: 3600 = 1 hour)
        api_version: Salesforce API version (default: v59.0)
        cancel_check: Optional async callable returning True if migration was cancelled (aborts job and raises MigrationCancelledError)
    
    Returns:
        Final job status dictionary
    
    Raises:
        Exception: If job times out or fails
        MigrationCancelledError: If cancel_check returns True (after aborting the job)
    """
    from utils.migration_cancel import MigrationCancelledError

    start_time = time.time()
    
    while True:
        # Check for user-initiated cancellation before polling
        if cancel_check:
            cancelled = await cancel_check()
            if cancelled:
                try:
                    await abort_bulk_query_job(job_id, access_token, instance_url, api_version)
                except Exception as abort_err:
                    err_text = str(abort_err)
                    if "INVALIDJOBSTATE" not in err_text:
                        print(f"⚠️ [BULK API 2.0] Failed to abort query job {job_id}: {abort_err}")
                raise MigrationCancelledError("Migration cancelled by user")
        
        try:
            job_status = await get_bulk_query_job_status(
                job_id, access_token, instance_url, api_version
            )
        except Exception as e:
            print(f"❌ [BULK API 2.0] Failed to get job status for {job_id}: {e}")
            raise
        
        state = job_status.get('state')
        
        if state in ['JobComplete', 'Failed', 'Aborted']:
            if state == 'Failed':
                error_message = job_status.get('errorMessage', 'Unknown error')
                raise Exception(f"Bulk query job {job_id} failed: {error_message}")
            elif state == 'Aborted':
                raise Exception(f"Bulk query job {job_id} was aborted")
            return job_status
        
        # Check timeout
        if time.time() - start_time > max_wait_time:
            raise Exception(
                f"Bulk query job {job_id} timed out after {max_wait_time} seconds (final state: {state})"
            )
        
        await asyncio.sleep(poll_interval)


async def get_bulk_query_results(
    job_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> List[Dict[str, Any]]:
    """
    Get query results from Bulk API 2.0 query job.
    
    Results are returned as CSV, which we parse and convert to list of dicts.
    Handles pagination if results are large.
    
    Args:
        job_id: Bulk API 2.0 query job ID
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        List of dictionaries, each representing a record
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/query/{job_id}/results"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    all_records = []
    
    async with _new_session() as session:
        # Get first page of results
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                csv_content = await response.text()
                
                # Parse CSV to list of dicts
                reader = csv.DictReader(io.StringIO(csv_content))
                all_records.extend(list(reader))
                
                # Check for pagination
                job_status = await get_bulk_query_job_status(
                    job_id, access_token, instance_url, api_version
                )
                locator = job_status.get('locator')
                
                # Get remaining pages if paginated
                while locator:
                    next_url = f"{url}?locator={locator}"
                    async with session.get(next_url, headers=headers) as next_response:
                        if next_response.status == 200:
                            next_csv = await next_response.text()
                            next_reader = csv.DictReader(io.StringIO(next_csv))
                            all_records.extend(list(next_reader))
                            
                            # Check for next page
                            job_status = await get_bulk_query_job_status(
                                job_id, access_token, instance_url, api_version
                            )
                            locator = job_status.get('locator')
                        else:
                            break
            elif response.status == 404:
                # No results (job may have returned no matches)
                return []
            else:
                error_text = await response.text()
                raise Exception(f"Failed to get query results: {error_text}")
    
    return all_records


async def stream_bulk_query_results(
    job_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0",
    *,
    chunk_size: int = 500,
):
    """
    Stream Bulk API 2.0 query results in chunks (assessment profiling).

    Yields lists of row dicts; does not load the full CSV into memory.
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/query/{job_id}/results"
    headers = {"Authorization": f"Bearer {access_token}"}
    locator = None

    async with _new_session() as session:
        while True:
            fetch_url = f"{url}?locator={locator}" if locator else url
            async with session.get(fetch_url, headers=headers) as response:
                if response.status == 404:
                    return
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Failed to get query results: {error_text}")

                csv_content = await response.text()
                if not csv_content.strip():
                    return

                reader = csv.DictReader(io.StringIO(csv_content))
                batch: List[Dict[str, Any]] = []
                for row in reader:
                    batch.append(dict(row))
                    if len(batch) >= chunk_size:
                        yield batch
                        batch = []
                if batch:
                    yield batch

                job_status = await get_bulk_query_job_status(
                    job_id, access_token, instance_url, api_version
                )
                locator = job_status.get("locator")
                if not locator:
                    return


# ============================================================================
# Bulk API 2.0 Data Operations (Insert/Update/Delete)
# ============================================================================

async def create_bulk_job(
    object_name: str,
    operation: str,  # 'insert', 'update', 'delete', 'upsert'
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0",
    external_id_field: Optional[str] = None  # Required for 'upsert' operation
) -> str:
    """
    Create a Bulk API 2.0 data job (insert, update, delete, or upsert).
    
    Args:
        object_name: Salesforce object name (e.g., "Contact")
        operation: Operation type ('insert', 'update', 'delete', 'upsert')
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
        external_id_field: External ID field name (required for 'upsert')
    
    Returns:
        Job ID string
    
    Raises:
        Exception: If job creation fails
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/ingest"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "operation": operation,
        "object": object_name,
        "contentType": "CSV",
        "lineEnding": "LF"
    }
    
    # Add external ID field for upsert operation
    if operation == 'upsert':
        if not external_id_field:
            raise ValueError("external_id_field is required for upsert operation")
        payload["externalIdFieldName"] = external_id_field
    
    async with _new_session() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status not in [200, 201]:
                error_text = await response.text()
                raise Exception(f"Failed to create bulk job: {error_text}")
            
            result = await response.json()
            return result['id']


async def upload_bulk_data(
    job_id: str,
    csv_data: bytes,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> None:
    """
    Upload CSV data to a Bulk API 2.0 job.
    
    Args:
        job_id: Bulk API 2.0 job ID
        csv_data: CSV content as bytes (UTF-8 encoded)
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
    
    Raises:
        Exception: If upload fails
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/ingest/{job_id}/batches"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "text/csv"
    }
    
    async with _new_session() as session:
        async with session.put(url, headers=headers, data=csv_data) as response:
            if response.status not in [200, 201]:
                error_text = await response.text()
                raise Exception(f"Failed to upload bulk data: {error_text}")


async def close_bulk_job(
    job_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Dict[str, Any]:
    """
    Close a Bulk API 2.0 job to start processing.
    
    Args:
        job_id: Bulk API 2.0 job ID
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        Job status dictionary
    
    Raises:
        Exception: If closing fails
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/ingest/{job_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "state": "UploadComplete"
    }
    
    async with _new_session() as session:
        async with session.patch(url, headers=headers, json=payload) as response:
            if response.status not in [200, 201]:
                error_text = await response.text()
                raise Exception(f"Failed to close bulk job: {error_text}")
            
            return await response.json()


async def get_bulk_job_status(
    job_id: str,
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> Dict[str, Any]:
    """
    Get the current status of a Bulk API 2.0 data job.
    
    Args:
        job_id: Bulk API 2.0 job ID
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        Job status dictionary with fields like:
        - state: Job state (Open, UploadComplete, InProgress, JobComplete, Failed, Aborted)
        - numberRecordsProcessed: Number of records processed
        - numberRecordsFailed: Number of records that failed
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/ingest/{job_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with _new_session() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            else:
                error_text = await response.text()
                raise Exception(f"Failed to get bulk job status: {error_text}")


async def wait_for_bulk_job_completion(
    job_id: str,
    access_token: str,
    instance_url: str,
    poll_interval: int = 5,
    max_wait_time: int = 3600,
    api_version: str = "v59.0",
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Dict[str, Any]:
    """
    Poll Bulk API 2.0 data job status until completion or timeout.
    
    Args:
        job_id: Bulk API 2.0 job ID
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        poll_interval: Seconds between status checks (default: 5)
        max_wait_time: Maximum time to wait in seconds (default: 3600 = 1 hour)
        api_version: Salesforce API version (default: v59.0)
        cancel_check: Optional async callable returning True if migration was cancelled (aborts job and raises MigrationCancelledError)
    
    Returns:
        Final job status dictionary
    
    Raises:
        Exception: If job times out or fails
        MigrationCancelledError: If cancel_check returns True (after aborting the job)
    """
    from utils.migration_cancel import MigrationCancelledError

    start_time = time.time()
    poll_count = 0
    
    while True:
        # Check for user-initiated cancellation before polling
        if cancel_check:
            cancelled = await cancel_check()
            if cancelled:
                try:
                    await abort_bulk_ingest_job(job_id, access_token, instance_url, api_version)
                    print(f"🛑 [BULK API 2.0] Aborted ingest job {job_id} (user cancelled)")
                except Exception as abort_err:
                    print(f"⚠️ [BULK API 2.0] Failed to abort ingest job {job_id}: {abort_err}")
                raise MigrationCancelledError("Migration cancelled by user")
        
        poll_count += 1
        job_status = await get_bulk_job_status(
            job_id, access_token, instance_url, api_version
        )
        
        state = job_status.get('state')
        
        if state in ['JobComplete', 'Failed', 'Aborted']:
            if state == 'Failed':
                error_message = job_status.get('errorMessage', 'Unknown error')
                raise Exception(f"Bulk job {job_id} failed: {error_message}")
            elif state == 'Aborted':
                raise Exception(f"Bulk job {job_id} was aborted")
            return job_status
        
        # Check timeout
        if time.time() - start_time > max_wait_time:
            raise Exception(
                f"Bulk job {job_id} timed out after {max_wait_time} seconds"
            )
        
        await asyncio.sleep(poll_interval)


async def get_bulk_job_results(
    job_id: str,
    result_type: str,  # 'successfulResults' or 'failedResults'
    access_token: str,
    instance_url: str,
    api_version: str = "v59.0"
) -> List[Dict[str, Any]]:
    """
    Get results from a Bulk API 2.0 data job.
    
    Args:
        job_id: Bulk API 2.0 job ID
        result_type: 'successfulResults' or 'failedResults'
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        api_version: Salesforce API version (default: v59.0)
    
    Returns:
        List of result dictionaries (CSV parsed to dicts)
    """
    url = f"{instance_url}/services/data/{api_version}/jobs/ingest/{job_id}/{result_type}"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    all_results = []
    
    async with _new_session() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                csv_content = await response.text()
                
                # Parse CSV to list of dicts
                reader = csv.DictReader(io.StringIO(csv_content))
                all_results = list(reader)
            elif response.status == 404:
                # No results of this type
                return []
            else:
                error_text = await response.text()
                raise Exception(f"Failed to get bulk job results: {error_text}")
    
    return all_results


# ============================================================================
# CSV Generation Utilities
# ============================================================================

def generate_csv_for_bulk_api(
    records: List[Dict[str, Any]],
    include_id: bool = False
) -> bytes:
    """
    Generate CSV data for Bulk API 2.0 from a list of record dictionaries.
    
    Args:
        records: List of record dictionaries
        include_id: Whether to include 'Id' field (required for update operations)
    
    Returns:
        CSV content as bytes (UTF-8 encoded)
    
    Example:
        records = [
            {"Email": "user@example.com", "Phone": "1234567890"},
            {"Email": "user2@example.com", "Phone": "0987654321"}
        ]
        csv_bytes = generate_csv_for_bulk_api(records, include_id=False)
    """
    if not records:
        return b""
    
    # Get all field names from records
    all_fields = set()
    for record in records:
        all_fields.update(record.keys())
    
    # Remove 'Id' if not needed
    if not include_id and 'Id' in all_fields:
        all_fields.remove('Id')
    
    # Remove metadata fields (fields starting with '_' are internal tracking fields)
    # These are not real Salesforce fields and will cause errors if included
    all_fields = {f for f in all_fields if not f.startswith('_')}
    
    # Sort fields for consistent CSV structure (Id first if included)
    field_list = ['Id'] if include_id and 'Id' in all_fields else []
    field_list.extend(sorted([f for f in all_fields if f != 'Id']))
    
    # Generate CSV with explicit LF line endings (required by Bulk API 2.0)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=field_list, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    
    for record in records:
        # Filter out None values and metadata fields (fields starting with '_')
        # Salesforce doesn't like None values, and metadata fields don't exist in Salesforce
        filtered_record = {
            k: v for k, v in record.items() 
            if v is not None and not k.startswith('_')
        }
        writer.writerow(filtered_record)
    
    csv_string = output.getvalue()
    # Ensure LF line endings (normalize any CRLF to LF)
    csv_string = csv_string.replace('\r\n', '\n').replace('\r', '\n')
    return csv_string.encode('utf-8')


# ============================================================================
# Helper Functions for Query Building
# ============================================================================

def normalize_field_value(field_name: str, value: Any) -> str:
    """
    Normalize field value for consistent matching.
    
    Same normalization as current format_soql_value() function.
    Used for building duplicate check keys.
    
    Args:
        field_name: Field name (for type-specific normalization)
        value: Field value to normalize
    
    Returns:
        Normalized string value
    """
    if value is None:
        return ""
    
    value_str = str(value).strip()
    
    # Email normalization
    if 'email' in field_name.lower():
        return value_str.lower()
    
    # Phone normalization
    if 'phone' in field_name.lower():
        return ''.join(filter(str.isdigit, value_str))
    
    # Default: lowercase and trim
    return value_str.lower().strip()


def build_duplicate_check_key(
    record: Dict[str, Any],
    duplicate_check_fields: Dict[str, Any]
) -> Optional[str]:
    """
    Build a key from duplicate_check_fields for mapping records.
    
    Uses same normalization as current approach.
    
    Args:
        record: Record dictionary
        duplicate_check_fields: Dictionary of field names to check
    
    Returns:
        Key string like "Email:user@example.com|Phone:1234567890" or None
    """
    key_parts = []
    for field in sorted(duplicate_check_fields.keys()):
        value = record.get(field)
        # Treat None and empty strings as missing values (consistent with build_full_record_key)
        if value is not None and value != "":
            normalized = normalize_field_value(field, value)
            key_parts.append(f"{field}:{normalized}")
    
    return "|".join(key_parts) if key_parts else None

