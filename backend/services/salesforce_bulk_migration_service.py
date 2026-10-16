"""
Salesforce Bulk API 2.0 Migration Service

This service provides bulk migration capabilities using Salesforce Bulk API 2.0
for high-volume data migrations (100K+ records) while maintaining the same
accuracy as the current REST API approach.

Key Features:
- Bulk query existing records using AND conditions
- Bulk insert/update operations
- Lookup relationship resolution in bulk
- Same duplicate checking logic as current approach
- Handles 200K+ records efficiently
"""

from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable
from collections import defaultdict
import asyncio

from utils.salesforce_bulk_api import (
    create_bulk_query_job,
    wait_for_bulk_query_job_completion,
    get_bulk_query_results,
    create_bulk_job,
    upload_bulk_data,
    close_bulk_job,
    wait_for_bulk_job_completion as wait_for_data_job_completion,
    get_bulk_job_results,
    generate_csv_for_bulk_api,
    normalize_field_value,
    build_duplicate_check_key
)
from utils.salesforceapimigration import format_soql_value


def build_full_record_key(record: Dict[str, Any], exclude_fields: Optional[List[str]] = None) -> Optional[str]:
    """
    Build a key from ALL fields in a record (excluding metadata fields).
    
    This is more robust than duplicate_check_fields matching because it uses
    all available data, making it more likely to find matches even when
    some fields are empty.
    
    Args:
        record: Record dictionary
        exclude_fields: List of field names to exclude (e.g., ['sf__Id', 'sf__Error'])
    
    Returns:
        Key string like "Email:user@example.com|FirstName:John|LastName:Doe" or None
    """
    if exclude_fields is None:
        exclude_fields = []
    
    # Default metadata fields to exclude
    default_exclude = ['sf__Id', 'sf__Error', 'Id', 'Error']
    all_exclude = set(exclude_fields + default_exclude)
    
    key_parts = []
    for field in sorted(record.keys()):
        # Skip metadata fields and fields starting with _
        if field in all_exclude or field.startswith('_'):
            continue
        
        value = record.get(field)
        # Treat None and empty strings as missing values
        if value is not None and value != "":
            normalized = normalize_field_value(field, value)
            key_parts.append(f"{field}:{normalized}")
    
    return "|".join(key_parts) if key_parts else None


# ============================================================================
# Query Building Functions
# ============================================================================

def build_bulk_queries_with_and_conditions(
    object_name: str,
    duplicate_check_fields: Dict[str, Any],  # Field names
    all_records: List[Dict]  # All records to migrate
) -> List[str]:
    """
    Build SOQL queries with AND conditions for exact matching.
    
    Strategy:
    - Group records by their duplicate_check_field value combinations
    - Build queries with AND conditions: WHERE (Email = 'X' AND Phone = 'Y') OR ...
    - Salesforce returns only records that match ALL fields
    - No in-memory matching needed!
    
    Args:
        object_name: Salesforce object name
        duplicate_check_fields: Dictionary of field names to check for duplicates
        all_records: List of all records to migrate
    
    Returns:
        List of SOQL query strings (may need multiple queries for 200K+ records)
    """
    # Step 1: Group records by duplicate_check_field combinations
    record_groups = {}
    for record in all_records:
        # Build key from duplicate_check_fields
        key_parts = []
        for field in sorted(duplicate_check_fields.keys()):
            # RecordTypeId is not a valid identity field for duplicate checking —
            # its value at this stage is still the raw record type name (resolution
            # happens in Step 1.5, after this function is called). Exclude it so it
            # doesn't pollute the grouping key or the SOQL WHERE clause.
            if field == 'RecordTypeId':
                continue
            value = record.get(field)
            # Treat None and empty strings as missing values (consistent with matching logic)
            if value is not None and value != "":
                # Normalize value (same as current approach)
                normalized = normalize_field_value(field, value)
                key_parts.append(f"{field}:{normalized}")
        
        if key_parts:
            key = "|".join(key_parts)
            if key not in record_groups:
                record_groups[key] = {
                    'values': {field: record.get(field) for field in duplicate_check_fields.keys()},
                    'records': []
                }
            record_groups[key]['records'].append(record)
    
    # Step 2: Build queries for each unique combination
    # Handle query length limits by batching
    queries = []
    fields_to_select = [f for f in duplicate_check_fields.keys() if f != 'RecordTypeId'] + ['Id']
    unique_combinations = list(record_groups.keys())
    
    # SOQL WHERE clause has limit of ~20,000 characters
    # Estimate: Each combination adds ~50-200 characters (depends on field values)
    # Conservative batch size: ~100-200 combinations per query
    MAX_QUERY_LENGTH = 15000  # Conservative limit (leave buffer)
    current_batch = []
    current_length = 0
    
    for key in unique_combinations:
        group = record_groups[key]
        and_conditions = []
        
        for field, value in group['values'].items():
            # Treat None and empty strings as missing values (consistent with key building)
            if value is not None and value != "":
                # RecordTypeId is resolved from a name to a Salesforce ID after this query is
                # built (Step 1.5), so the value here is still the raw record type name which
                # is not a valid SOQL filter value for an ID field — skip it entirely.
                if field == 'RecordTypeId':
                    continue
                formatted_value = format_soql_value(value)
                and_conditions.append(f"{field} = {formatted_value}")
        
        if and_conditions:
            # Build the AND condition string
            and_clause = f"({' AND '.join(and_conditions)})"
            estimated_length = len(and_clause) + 4  # +4 for " OR "
            
            # Check if adding this would exceed limit
            if current_length + estimated_length > MAX_QUERY_LENGTH and current_batch:
                # Save current batch as a query
                where_clause = ' OR '.join(current_batch)
                query = f"SELECT {', '.join(fields_to_select)} FROM {object_name} WHERE {where_clause}"
                queries.append(query)
                
                # Start new batch
                current_batch = [and_clause]
                current_length = estimated_length
            else:
                # Add to current batch
                current_batch.append(and_clause)
                current_length += estimated_length
    
    # Add remaining batch
    if current_batch:
        where_clause = ' OR '.join(current_batch)
        query = f"SELECT {', '.join(fields_to_select)} FROM {object_name} WHERE {where_clause}"
        queries.append(query)
    
    return queries if queries else [f"SELECT {', '.join(fields_to_select)} FROM {object_name} WHERE Id = null"]  # No matches


async def bulk_query_existing_records(
    object_name: str,
    queries: List[str],
    access_token: str,
    instance_url: str,
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> List[Dict[str, Any]]:
    """
    Execute bulk queries using Bulk API 2.0.
    
    For 200K records, we may need multiple queries if there are many unique combinations.
    Each query is sent as a separate Bulk API 2.0 job.
    Queries can run in parallel for better performance.
    
    Args:
        object_name: Salesforce object name
        queries: List of SOQL query strings
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
    
    Returns:
        List of existing records with their Ids and duplicate_check_fields
    """
    all_existing_records = []
    
    # Execute all queries (can run in parallel for better performance)
    tasks = []
    for idx, query in enumerate(queries):
        print(f"      📤 [BULK API 2.0] Creating query job {idx + 1}/{len(queries)}...")
        task = execute_single_bulk_query(
            object_name, query, access_token, instance_url, cancel_check=cancel_check
        )
        tasks.append(task)
    
    # Wait for all queries to complete
    print(f"      ⏳ [BULK API 2.0] Waiting for {len(queries)} query job(s) to complete...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Combine results from all queries
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"      ❌ [BULK API 2.0] Query job {idx + 1} error: {result}")
            continue
        all_existing_records.extend(result)
        print(f"      ✅ [BULK API 2.0] Query job {idx + 1} completed: {len(result)} records")
    
    return all_existing_records


async def execute_single_bulk_query(
    object_name: str,
    query: str,
    access_token: str,
    instance_url: str,
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> List[Dict[str, Any]]:
    """
    Execute a single Bulk API 2.0 query job.
    
    Args:
        object_name: Salesforce object name
        query: SOQL query string
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
    
    Returns:
        List of records from query results
    """
    # Step 1: Create Bulk API 2.0 query job
    job_id = await create_bulk_query_job(
        object_name,
        query,
        access_token,
        instance_url
    )
    print(f"         📋 [BULK API 2.0] Query job created: {job_id}")
    
    # Step 2: Wait for job completion (poll status)
    job_status = await wait_for_bulk_query_job_completion(
        job_id, access_token, instance_url, cancel_check=cancel_check
    )
    print(f"         ✅ [BULK API 2.0] Query job {job_id} completed: {job_status.get('state')}")
    
    # Step 3: Get results (CSV format from Bulk API 2.0)
    existing_records = await get_bulk_query_results(
        job_id,
        access_token,
        instance_url
    )
    
    return existing_records


# ============================================================================
# Record Matching and Mapping
# ============================================================================

def build_id_mapping_from_query_results(
    existing_records: List[Dict[str, Any]],
    duplicate_check_fields: Dict[str, Any]
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Build mapping from duplicate_check_field combination to Salesforce Id.
    Also builds a secondary mapping using ALL fields for fallback matching.
    
    Primary key: "Email:user@example.com|Phone:1234567890" (using duplicate_check_fields)
    Fallback key: "Email:user@example.com|FirstName:John|LastName:Doe|..." (using ALL fields)
    Value: "001XX000003DHP0YAM"
    
    Args:
        existing_records: List of existing records from bulk query
        duplicate_check_fields: Dictionary of field names used for duplicate checking
    
    Returns:
        Tuple of (duplicate_check_id_mapping, full_field_id_mapping)
    """
    duplicate_check_id_mapping = {}
    full_field_id_mapping = {}
    
    for existing_record in existing_records:
        # Build primary key from duplicate_check_fields (for initial matching)
        key = build_duplicate_check_key(existing_record, duplicate_check_fields)
        if key and existing_record.get('Id'):
            duplicate_check_id_mapping[key] = existing_record['Id']
        
        # Build fallback key from ALL fields (for matching when duplicate_check_fields are empty)
        full_key = build_full_record_key(existing_record)
        if full_key and existing_record.get('Id'):
            full_field_id_mapping[full_key] = existing_record['Id']
    
    return duplicate_check_id_mapping, full_field_id_mapping


def split_records_for_bulk_operations(
    records: List[Dict[str, Any]],
    duplicate_check_fields: Dict[str, Any],
    duplicate_check_id_mapping: Dict[str, str],
    full_field_id_mapping: Dict[str, str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Split records into update (has Id) vs insert (no Id) operations.
    
    First tries matching using duplicate_check_fields (primary method).
    If that fails, tries matching using ALL fields (fallback for records with empty duplicate_check_fields).
    
    Args:
        records: List of records to migrate
        duplicate_check_fields: Dictionary of field names used for duplicate checking
        duplicate_check_id_mapping: Mapping from duplicate_check_key -> Salesforce Id (primary matching)
        full_field_id_mapping: Mapping from full_field_key -> Salesforce Id (fallback matching)
    
    Returns:
        Tuple of (records_to_update, records_to_insert)
    """
    records_to_update = []
    records_to_insert = []
    
    for record in records:
        matched = False
        salesforce_id = None
        
        # Try primary matching using duplicate_check_fields
        key = build_duplicate_check_key(record, duplicate_check_fields)
        if key and key in duplicate_check_id_mapping:
            salesforce_id = duplicate_check_id_mapping[key]
            matched = True
        
        # If primary matching failed, try fallback matching using ALL fields
        if not matched:
            full_key = build_full_record_key(record)
            if full_key and full_key in full_field_id_mapping:
                salesforce_id = full_field_id_mapping[full_key]
                matched = True
        
        if matched and salesforce_id:
            # Existing record - add Id for update
            record['Id'] = salesforce_id
            records_to_update.append(record)
        else:
            # New record - no Id for insert
            records_to_insert.append(record)
    
    return records_to_update, records_to_insert


# ============================================================================
# Bulk Insert/Update Operations
# ============================================================================

async def bulk_update_records(
    object_name: str,
    records: List[Dict[str, Any]],
    access_token: str,
    instance_url: str,
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Dict[str, Any]:
    """
    Bulk update existing records using Bulk API 2.0.
    
    Args:
        object_name: Salesforce object name
        records: List of records to update (must include 'Id' field)
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
    
    Returns:
        Dictionary with job status and results
    """
    if not records:
        return {"status": "skipped", "message": "No records to update"}
    
    print(f"         🔄 [BULK API 2.0] Creating bulk UPDATE job for {object_name}...")
    # Create bulk update job
    job_id = await create_bulk_job(
        object_name,
        operation='update',
        access_token=access_token,
        instance_url=instance_url
    )
    print(f"         📋 [BULK API 2.0] UPDATE job created: {job_id}")
    
    # Generate CSV (include Id column)
    csv_data = generate_csv_for_bulk_api(records, include_id=True)
    print(f"         📄 [BULK API 2.0] Generated CSV: {len(csv_data)} bytes for {len(records)} records")
    
    # Upload CSV data
    print(f"         📤 [BULK API 2.0] Uploading CSV data to job {job_id}...")
    await upload_bulk_data(job_id, csv_data, access_token, instance_url)
    print(f"         ✅ [BULK API 2.0] CSV data uploaded to job {job_id}")
    
    # Close job and wait for completion
    print(f"         🔒 [BULK API 2.0] Closing job {job_id} and waiting for completion...")
    await close_bulk_job(job_id, access_token, instance_url)
    job_status = await wait_for_data_job_completion(
        job_id, access_token, instance_url, cancel_check=cancel_check
    )
    print(f"         ✅ [BULK API 2.0] UPDATE job {job_id} completed: {job_status.get('state')}")
    
    # Get results
    successful_results = await get_bulk_job_results(
        job_id, 'successfulResults', access_token, instance_url
    )
    failed_results = await get_bulk_job_results(
        job_id, 'failedResults', access_token, instance_url
    )
    
    result = {
        "job_id": job_id,
        "status": job_status.get('state'),
        "successful_count": len(successful_results),
        "failed_count": len(failed_results),
        "successful_results": successful_results,
        "failed_results": failed_results,
    }
    return result


async def bulk_insert_records(
    object_name: str,
    records: List[Dict[str, Any]],
    access_token: str,
    instance_url: str,
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Dict[str, Any]:
    """
    Bulk insert new records using Bulk API 2.0.
    
    Args:
        object_name: Salesforce object name
        records: List of records to insert (should NOT include 'Id' field)
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
    
    Returns:
        Dictionary with job status and results
    """
    if not records:
        return {"status": "skipped", "message": "No records to insert"}
    
    print(f"         ➕ [BULK API 2.0] Creating bulk INSERT job for {object_name}...")
    # Create bulk insert job
    job_id = await create_bulk_job(
        object_name,
        operation='insert',
        access_token=access_token,
        instance_url=instance_url
    )
    print(f"         📋 [BULK API 2.0] INSERT job created: {job_id}")
    
    # Generate CSV (exclude Id column)
    csv_data = generate_csv_for_bulk_api(records, include_id=False)
    print(f"         📄 [BULK API 2.0] Generated CSV: {len(csv_data)} bytes for {len(records)} records")
    
    # Upload CSV data
    print(f"         📤 [BULK API 2.0] Uploading CSV data to job {job_id}...")
    await upload_bulk_data(job_id, csv_data, access_token, instance_url)
    print(f"         ✅ [BULK API 2.0] CSV data uploaded to job {job_id}")
    
    # Close job and wait for completion
    print(f"         🔒 [BULK API 2.0] Closing job {job_id} and waiting for completion...")
    await close_bulk_job(job_id, access_token, instance_url)
    job_status = await wait_for_data_job_completion(
        job_id, access_token, instance_url, cancel_check=cancel_check
    )
    print(f"         ✅ [BULK API 2.0] INSERT job {job_id} completed: {job_status.get('state')}")
    
    # Get results
    successful_results = await get_bulk_job_results(
        job_id, 'successfulResults', access_token, instance_url
    )
    failed_results = await get_bulk_job_results(
        job_id, 'failedResults', access_token, instance_url
    )
    
    result = {
        "job_id": job_id,
        "status": job_status.get('state'),
        "successful_count": len(successful_results),
        "failed_count": len(failed_results),
        "successful_results": successful_results,
        "failed_results": failed_results,
    }
    return result


async def bulk_delete_records(
    object_name: str,
    record_ids: List[str],
    access_token: str,
    instance_url: str
) -> Dict[str, Any]:
    """
    Bulk delete records using Bulk API 2.0.
    
    Args:
        object_name: Salesforce object name
        record_ids: List of Salesforce record IDs to delete
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
    
    Returns:
        Dictionary with job status and results
    """
    if not record_ids:
        return {"status": "skipped", "message": "No records to delete"}
    
    print(f"         🗑️ [BULK API 2.0] Creating bulk DELETE job for {object_name}...")
    # Create bulk delete job
    job_id = await create_bulk_job(
        object_name,
        operation='delete',
        access_token=access_token,
        instance_url=instance_url
    )
    print(f"         📋 [BULK API 2.0] DELETE job created: {job_id}")
    
    # For delete operations, CSV should only contain Id column
    # Convert record_ids to list of dicts with Id field
    records = [{"Id": record_id} for record_id in record_ids]
    
    # Validate record IDs
    invalid_ids = [rid for rid in record_ids if not rid or len(rid) < 15 or not rid.startswith(('00', '01', '02', '03', '04', '05', '06', '07', '08', '09'))]
    if invalid_ids:
        print(f"         ⚠️ [BULK API 2.0] Found {len(invalid_ids)} potentially invalid record IDs")
    
    csv_data = generate_csv_for_bulk_api(records, include_id=True)
    print(f"         📄 [BULK API 2.0] Generated CSV: {len(csv_data)} bytes for {len(record_ids)} records")
    
    # Upload CSV data
    print(f"         📤 [BULK API 2.0] Uploading CSV data to job {job_id}...")
    await upload_bulk_data(job_id, csv_data, access_token, instance_url)
    print(f"         ✅ [BULK API 2.0] CSV data uploaded to job {job_id}")
    
    # Close job and wait for completion
    print(f"         🔒 [BULK API 2.0] Closing job {job_id} and waiting for completion...")
    await close_bulk_job(job_id, access_token, instance_url)
    job_status = await wait_for_data_job_completion(
        job_id, access_token, instance_url
    )
    print(f"         ✅ [BULK API 2.0] DELETE job {job_id} completed: {job_status.get('state')}")
    
    # Get results
    successful_results = await get_bulk_job_results(
        job_id, 'successfulResults', access_token, instance_url
    )
    
    failed_results = await get_bulk_job_results(
        job_id, 'failedResults', access_token, instance_url
    )
    
    return {
        "job_id": job_id,
        "status": job_status.get('state'),
        "successful_count": len(successful_results),
        "failed_count": len(failed_results),
        "successful_results": successful_results,
        "failed_results": failed_results
    }


# ============================================================================
# Main Bulk Migration Functions
# ============================================================================

async def migrate_object_with_bulk_api(
    object_name: str,
    records: List[Dict[str, Any]],
    duplicate_check_fields: Dict[str, Any],
    access_token: str,
    instance_url: str,
    cancel_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Dict[str, Any]:
    """
    Migrate records for a single Salesforce object using Bulk API 2.0.
    
    This function:
    1. Builds SOQL queries with AND conditions for duplicate checking
    2. Executes bulk queries to find existing records
    3. Maps results back to source records
    4. Splits into update vs insert operations
    5. Executes bulk update and insert operations
    
    Args:
        object_name: Salesforce object name (e.g., "Contact")
        records: List of records to migrate for this object
        duplicate_check_fields: Dictionary of field names to check for duplicates
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
    
    Returns:
        Dictionary with migration results
    """
    if not records:
        return {
            "object_name": object_name,
            "status": "skipped",
            "message": "No records to migrate"
        }
    
    try:
        # Step 1: Build bulk queries with AND conditions
        queries = build_bulk_queries_with_and_conditions(
            object_name,
            duplicate_check_fields,
            records
        )
        print(f"   📝 [BULK API 2.0] Created {len(queries)} SOQL query(ies) for {object_name}")
        
        # Step 2: Execute bulk queries
        print(f"   🔎 [BULK API 2.0] Executing Bulk API 2.0 query job(s) for {object_name}...")
        existing_records = await bulk_query_existing_records(
            object_name,
            queries,
            access_token,
            instance_url,
            cancel_check=cancel_check,
        )
        print(f"   ✅ [BULK API 2.0] Query completed: Found {len(existing_records)} existing records in {object_name}")
        
        # Step 3: Map results to source records
        # Build two mappings: one using duplicate_check_fields (primary), one using ALL fields (fallback)
        duplicate_check_id_mapping, full_field_id_mapping = build_id_mapping_from_query_results(
            existing_records,
            duplicate_check_fields
        )
        print(f"   🗺️ [BULK API 2.0] Built ID mapping: {len(duplicate_check_id_mapping)} records (duplicate_check_fields), {len(full_field_id_mapping)} records (all fields)")
        
        # Step 4: Split records into update vs insert
        # Use duplicate_check_fields first, then fallback to ALL fields matching
        records_to_update, records_to_insert = split_records_for_bulk_operations(
            records,
            duplicate_check_fields,
            duplicate_check_id_mapping,
            full_field_id_mapping
        )
        insert_record_indices = [
            r.get("_record_index")
            for r in records_to_insert
            if r.get("_record_index") is not None
        ]
        update_record_indices = [
            r.get("_record_index")
            for r in records_to_update
            if r.get("_record_index") is not None
        ]
        print(f"   📊 [BULK API 2.0] Split records: {len(records_to_update)} to update, {len(records_to_insert)} to insert")
        
        # Step 5: Execute bulk operations
        update_result = None
        insert_result = None
        
        if records_to_update:
            # Check cancel before UPDATE job
            if cancel_check and await cancel_check():
                from utils.migration_cancel import MigrationCancelledError
                raise MigrationCancelledError("Migration cancelled by user")
            print(f"   🔄 [BULK API 2.0] Creating bulk UPDATE job for {object_name} ({len(records_to_update)} records)...")
            update_result = await bulk_update_records(
                object_name,
                records_to_update,
                access_token,
                instance_url,
                cancel_check=cancel_check,
            )
            print(f"   ✅ [BULK API 2.0] UPDATE job completed: Job ID {update_result.get('job_id')}, {update_result.get('successful_count', 0)} successful, {update_result.get('failed_count', 0)} failed")
        
        if records_to_insert:
            # Check cancel before INSERT job
            if cancel_check and await cancel_check():
                from utils.migration_cancel import MigrationCancelledError
                raise MigrationCancelledError("Migration cancelled by user")
            print(f"   ➕ [BULK API 2.0] Creating bulk INSERT job for {object_name} ({len(records_to_insert)} records)...")
            insert_result = await bulk_insert_records(
                object_name,
                records_to_insert,
                access_token,
                instance_url,
                cancel_check=cancel_check,
            )
            print(f"   ✅ [BULK API 2.0] INSERT job completed: Job ID {insert_result.get('job_id')}, {insert_result.get('successful_count', 0)} successful, {insert_result.get('failed_count', 0)} failed")
        
        # Combine results
        total_successful = 0
        total_failed = 0
        
        if update_result:
            total_successful += update_result.get('successful_count', 0)
            total_failed += update_result.get('failed_count', 0)
        
        if insert_result:
            total_successful += insert_result.get('successful_count', 0)
            total_failed += insert_result.get('failed_count', 0)
        
        result = {
            "object_name": object_name,
            "status": "completed",
            "update_result": update_result,
            "insert_result": insert_result,
            "insert_record_indices": insert_record_indices,
            "update_record_indices": update_record_indices,
            "total_successful": total_successful,
            "total_failed": total_failed,
            "total_records": len(records)
        }
        return result
    
    except Exception as e:
        from utils.migration_cancel import MigrationCancelledError
        if isinstance(e, MigrationCancelledError):
            raise  # Propagate so we skip create_migration_records, usage tracking, etc.
        print(f"❌ Error migrating {object_name} with Bulk API: {e}")
        return {
            "object_name": object_name,
            "status": "error",
            "message": str(e),
            "total_records": len(records)
        }


# ============================================================================
# Lookup Relationship Resolution
# ============================================================================

async def resolve_lookup_references_in_bulk(
    lookup_references: List[Dict[str, Any]],
    access_token: str,
    instance_url: str
) -> Dict[str, Any]:
    """
    Resolve lookup references in bulk using Bulk API 2.0 with value-based deduplication.
    
    This function:
    1. Groups lookup references by (lookup_object, inject_into, inject_field) - combines all fields
    2. Deduplicates by unique value combinations (same values = one record)
    3. Resolves each unique lookup record (queries for existing, creates if not found)
    4. Maps lookup IDs back to all child records that need them
    5. Returns mapping: lookup_identifier -> Salesforce Id AND bulk API results for lookup objects
    
    Args:
        lookup_references: List of lookup reference dictionaries with:
            - lookup_object: Salesforce object name for lookup
            - lookup_field: Field name in lookup object
            - inject_into: Child object name
            - inject_field: Field name in child object
            - raw_value: Field value
            - is_duplicate_check: Whether to use this field for duplicate checking
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
    
    Returns:
        Dictionary with:
            - "lookup_mapping": Dictionary mapping lookup_identifier -> Salesforce Id
              Format: "lookup_object|field1:value1|field2:value2" -> "001XX000003DHP0YAM"
            - "lookup_object_results": Dictionary mapping lookup_object -> bulk API result
              Format: {"Account": {status: "completed", insert_result: {...}, ...}}
    """
    if not lookup_references:
        return {"lookup_mapping": {}, "lookup_object_results": {}}
    
    # Step 1: Group references by source record (record_index) to build complete field-value combinations
    # Group structure: (lookup_object, inject_into, inject_field, record_index) -> list of references
    record_lookup_groups = defaultdict(lambda: {
        'lookup_object': None,
        'inject_into': None,
        'inject_field': None,
        'references': []
    })
    
    for ref in lookup_references:
        lookup_object = ref.get('lookup_object')
        lookup_field = ref.get('lookup_field')
        value = ref.get('raw_value')
        inject_into = ref.get('inject_into')
        inject_field = ref.get('inject_field')
        record_index = ref.get('record_index')
        is_duplicate_check = ref.get('is_duplicate_check', False)
        
        if not all([lookup_object, lookup_field, value, inject_into, inject_field]):
            continue
        
        # Skip empty values
        if value is None or (isinstance(value, str) and value.strip() == ''):
            continue
        
        # Group by (lookup_object, inject_into, inject_field, record_index)
        # This groups all fields for the same lookup from the same source record
        group_key = (lookup_object, inject_into, inject_field, record_index)
        
        record_lookup_groups[group_key]['lookup_object'] = lookup_object
        record_lookup_groups[group_key]['inject_into'] = inject_into
        record_lookup_groups[group_key]['inject_field'] = inject_field
        record_lookup_groups[group_key]['references'].append(ref)
    
    # Step 2: Build unique lookup records from complete field-value combinations
    # Group by lookup_object for batch processing (Option A for Question 3)
    by_lookup_object = defaultdict(lambda: {
        'unique_records': {},  # composite_key -> record data
        'duplicate_check_fields': {}  # Field names (not values) used for duplicate checking
    })
    
    for group_key, group_data in record_lookup_groups.items():
        lookup_object = group_data['lookup_object']
        references = group_data['references']
        
        # Build complete field-value combination from all references
        all_fields = {}  # All fields for the lookup record
        duplicate_check_fields_dict = {}  # Only duplicate_check fields for composite key
        
        for ref in references:
            lookup_field = ref['lookup_field']
            value = ref['raw_value']
            is_duplicate_check = ref.get('is_duplicate_check', False)
            
            # Store all fields
            all_fields[lookup_field] = value
            
            # Store duplicate_check fields only (Option A for Question 2)
            if is_duplicate_check:
                duplicate_check_fields_dict[lookup_field] = value
                # Track field names for query building
                by_lookup_object[lookup_object]['duplicate_check_fields'][lookup_field] = None
        
        # Skip if no duplicate_check fields
        if not duplicate_check_fields_dict:
            # Fallback: use all fields if no duplicate_check specified
            duplicate_check_fields_dict = all_fields
            for field in all_fields.keys():
                by_lookup_object[lookup_object]['duplicate_check_fields'][field] = None
        
        # Build composite key from duplicate_check fields only (sorted for consistency)
        composite_key_parts = []
        for field in sorted(duplicate_check_fields_dict.keys()):
            value = duplicate_check_fields_dict[field]
            if value is not None and value != '':
                normalized = normalize_field_value(field, value)
                composite_key_parts.append(f"{field}:{normalized}")
        
        composite_key = "|".join(composite_key_parts) if composite_key_parts else None
        if not composite_key:
            continue
        
        # Create or update unique lookup record (Option A for Question 1 - group by complete combinations)
        unique_key = (lookup_object, composite_key)
        if unique_key not in by_lookup_object[lookup_object]['unique_records']:
            by_lookup_object[lookup_object]['unique_records'][unique_key] = {
                'lookup_object': lookup_object,
                'inject_into': group_data['inject_into'],
                'inject_field': group_data['inject_field'],
                'composite_key': composite_key,
                'fields': all_fields,  # All fields for creating/updating record
                'duplicate_check_fields': duplicate_check_fields_dict,  # Only duplicate_check fields
                'references': references  # All references that match this combination
            }
        else:
            # Same field-value combination - add references to existing group
            by_lookup_object[lookup_object]['unique_records'][unique_key]['references'].extend(references)
    
    # Step 3: Resolve each unique lookup record
    all_lookup_mappings = {}
    lookup_object_results = {}  # Store bulk API results for lookup objects
    
    for lookup_object, lookup_data in by_lookup_object.items():
        unique_records_dict = lookup_data['unique_records']
        duplicate_check_fields = lookup_data['duplicate_check_fields']
        
        if not unique_records_dict:
            continue
        
        try:
            # Build unique records list (one per unique field-value combination)
            unique_records = []
            composite_keys = []
            
            for unique_key, record_data in unique_records_dict.items():
                composite_key = record_data['composite_key']
                unique_records.append(record_data['fields'])
                composite_keys.append(composite_key)
            
            # Query for existing records
            queries = build_bulk_queries_with_and_conditions(
                lookup_object,
                duplicate_check_fields,
                unique_records
            )
            
            existing_records = await bulk_query_existing_records(
                lookup_object,
                queries,
                access_token,
                instance_url
            )
            
            # Build mapping: composite_key -> Salesforce Id
            existing_id_map = {}
            for existing_record in existing_records:
                composite_key_parts = []
                for field in sorted(duplicate_check_fields.keys()):
                    value = existing_record.get(field)
                    if value is not None and value != '':
                        normalized = normalize_field_value(field, value)
                        composite_key_parts.append(f"{field}:{normalized}")
                
                if composite_key_parts:
                    composite_key = "|".join(composite_key_parts)
                    existing_id_map[composite_key] = existing_record.get('Id')
            
            # Split into update vs create
            records_to_update = []
            records_to_create = []
            create_composite_keys = []
            
            for idx, (unique_key, record_data) in enumerate(unique_records_dict.items()):
                composite_key = record_data['composite_key']
                
                if composite_key in existing_id_map:
                    record = record_data['fields'].copy()
                    record['Id'] = existing_id_map[composite_key]
                    records_to_update.append(record)
                else:
                    records_to_create.append(record_data['fields'])
                    create_composite_keys.append(composite_key)
            
            # Create missing records
            insert_result = None
            created_id_map = {}
            if records_to_create:
                insert_result = await bulk_insert_records(
                    lookup_object,
                    records_to_create,
                    access_token,
                    instance_url
                )
                
                # NOTE: Bulk API 2.0 job states use values like "JobComplete"
                # Our bulk_* helpers return the raw job state in `status`.
                insert_status = (insert_result or {}).get("status")
                insert_completed_states = {"JobComplete", "completed", "Complete", "Completed"}
                if insert_result and insert_status in insert_completed_states:
                    successful_results = insert_result.get('successful_results', [])
                    for idx, result in enumerate(successful_results):
                        if idx < len(create_composite_keys):
                            composite_key = create_composite_keys[idx]
                            record_id = result.get('Id') or result.get('sf__Id') or result.get('id')
                            if record_id:
                                created_id_map[composite_key] = record_id
            
            # Update existing records
            update_result = None
            if records_to_update:
                update_result = await bulk_update_records(
                    lookup_object,
                    records_to_update,
                    access_token,
                    instance_url
                )
            
            # Build final ID mapping
            all_id_map = {**existing_id_map, **created_id_map}
            # Map lookup IDs back
            for unique_key, record_data in unique_records_dict.items():
                composite_key = record_data['composite_key']
                lookup_id = all_id_map.get(composite_key)
                
                if lookup_id:
                    # Build lookup identifier using duplicate_check_fields to ensure consistency
                    # Use duplicate_check_fields dict (field names) as second arg, and fields (values) as third arg
                    duplicate_check_fields_for_identifier = record_data.get('duplicate_check_fields', record_data['fields'])
                    lookup_identifier = build_lookup_identifier(
                        lookup_object,
                        duplicate_check_fields_for_identifier,  # Field names (keys)
                        record_data['fields']  # Field values
                    )
                    
                    all_lookup_mappings[lookup_identifier] = lookup_id
                    all_lookup_mappings[f"{lookup_object}|{composite_key}"] = lookup_id
            
            # Store bulk API results
            lookup_object_results[lookup_object] = {
                'status': 'completed',
                'insert_result': insert_result,
                'update_result': update_result,
                'queried_count': len(existing_records),
                'created_count': len(records_to_create),
                'updated_count': len(records_to_update)
            }
        
        except Exception as e:
            print(f"⚠️ Error resolving lookup object {lookup_object}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    return {
        "lookup_mapping": all_lookup_mappings,
        "lookup_object_results": lookup_object_results
    }








































def build_lookup_identifier(
    lookup_object: str,
    duplicate_check_fields: Dict[str, Any],
    record: Dict[str, Any]
) -> str:
    """
    Build identifier for lookup record mapping.
    
    Args:
        lookup_object: Salesforce object name
        duplicate_check_fields: Dictionary of field names
        record: Record dictionary
    
    Returns:
        Identifier string like "Contact|Email:user@example.com"
    """
    key_parts = [lookup_object]
    for field in sorted(duplicate_check_fields.keys()):
        value = record.get(field)
        if value is not None:
            normalized = normalize_field_value(field, value)
            key_parts.append(f"{field}:{normalized}")
    
    return "|".join(key_parts)


def inject_lookup_ids_into_records(
    records: List[Dict[str, Any]],
    lookup_mapping: Dict[str, str],
    lookup_references: List[Dict[str, Any]],
    record_index_to_original_data: Dict[int, Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Inject lookup Salesforce Ids into child records.
    
    This function builds lookup identifiers from the child record's lookup field values
    and matches them to the lookup_mapping to inject the correct Salesforce IDs.
    
    Args:
        records: List of child records (will be modified in place)
        lookup_mapping: Mapping from lookup_identifier -> Salesforce Id
        lookup_references: List of lookup reference dictionaries
    
    Returns:
        Records with lookup IDs injected (same list, modified in place)
    """
    # Step 1: Build mapping of (inject_into, inject_field) -> list of lookup fields
    # This tells us which lookup fields belong to each injection point
    injection_to_lookup_fields = defaultdict(lambda: {
        'lookup_object': None,
        'fields': []  # List of (lookup_field, source_field, is_duplicate_check) tuples
    })
    
    for ref in lookup_references:
        inject_into = ref.get('inject_into')
        inject_field = ref.get('inject_field')
        lookup_object = ref.get('lookup_object')
        lookup_field = ref.get('lookup_field')
        source_field = ref.get('source_field')  # Source field name (e.g., "CompanyName")
        
        if not all([inject_into, inject_field, lookup_object, lookup_field]):
            continue
        
        key = (inject_into, inject_field)
        injection_to_lookup_fields[key]['lookup_object'] = lookup_object
        # Store both lookup_field (for identifier) and source_field (for getting value from original_record)
        injection_to_lookup_fields[key]['fields'].append((lookup_field, source_field, ref.get('is_duplicate_check', False)))
    
    # Step 2: Inject lookup IDs into each child record
    injected_count = 0
    for record_idx, record in enumerate(records):
        for (inject_into, inject_field), lookup_info in injection_to_lookup_fields.items():
            lookup_object = lookup_info['lookup_object']
            lookup_fields = lookup_info['fields']
            
            # Build a record containing only the lookup field values
            # CRITICAL: Lookup field values (e.g., Account.Name) are NOT in child records (Contact)
            # They're in the original transformed data, so we need to get them from there
            lookup_record = {}
            record_index = record.get("_record_index")
            original_record = None
            
            # Get original record data if available
            if record_index_to_original_data and record_index is not None:
                original_record = record_index_to_original_data.get(record_index)
            
            for lookup_field_info in lookup_fields:
                # lookup_field_info is now (lookup_field, source_field, is_duplicate_check)
                if len(lookup_field_info) == 3:
                    lookup_field, source_field, _ = lookup_field_info
                else:
                    # Backward compatibility: (lookup_field, is_duplicate_check)
                    lookup_field = lookup_field_info[0]
                    source_field = None
                
                # CRITICAL: Use source_field to get value from original_record (has source field names)
                # But use lookup_field as key in lookup_record (for building identifier with destination field name)
                value = None
                if original_record and source_field:
                    # Try source_field first (most reliable - matches original_record keys)
                    value = original_record.get(source_field)
                
                if value is None or value == '':
                    # Fallback: Try lookup_field (destination field name) in case data is already transformed
                    if original_record:
                        value = original_record.get(lookup_field)
                
                if value is None or value == '':
                    # Fallback: Try to get from child record (may not have the field)
                    value = record.get(lookup_field)
                
                if value is not None and value != '':
                    lookup_record[lookup_field] = value  # Use lookup_field as key for identifier building
            
            # Only proceed if we have at least one lookup field value
            if not lookup_record:
                continue
            
            # Build lookup identifier from all lookup field values
            # Use all fields (not just duplicate_check_fields) to match the identifier built during resolution
            lookup_identifier = build_lookup_identifier(
                lookup_object,
                lookup_record,  # Use all fields
                lookup_record   # Same record
            )
            
            # Find the lookup ID from mapping
            if lookup_identifier in lookup_mapping:
                record[inject_field] = lookup_mapping[lookup_identifier]
                injected_count += 1
            else:
                # Try alternative identifier format (composite key format)
                composite_key_parts = []
                for field in sorted(lookup_record.keys()):
                    value = lookup_record[field]
                    normalized = normalize_field_value(field, value)
                    composite_key_parts.append(f"{field}:{normalized}")
                composite_key = "|".join(composite_key_parts)
                alt_identifier = f"{lookup_object}|{composite_key}"
                
                if alt_identifier in lookup_mapping:
                    record[inject_field] = lookup_mapping[alt_identifier]
                    injected_count += 1
    
    if injected_count > 0:
        print(f"✅ Injected {injected_count} lookup IDs into {len(records)} records")
    return records

