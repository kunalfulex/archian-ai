"""
Salesforce Bulk API 2.0 Migration Flow

This module provides bulk migration capabilities that integrate with the existing
migration flow, automatically using Bulk API 2.0 for high-volume migrations.
"""

from typing import Dict, Any, List, Optional
from collections import defaultdict
from services.salesforce_bulk_migration_service import (
    migrate_object_with_bulk_api,
    resolve_lookup_references_in_bulk,
    inject_lookup_ids_into_records
)
from services.migration_records_service import MigrationRecordsService
from utils.mapping_utils import record_value_for_mapping_key
from utils.sf_mapping_lookup_sanitize import parse_lookup_of_reference
from utils.salesforce_bulk_api import normalize_field_value
from services.rich_text_image_service import process_rich_text_field
from utils.rich_text_handler import is_rich_text_field, fetch_field_data_types
from services.data_transformation_service import DataTransformationService


def _unmatched_record_error_message(record: Dict[str, Any]) -> str:
    """Prefer concrete source-data validation detail over generic bulk-match text."""
    from utils.field_validation import infer_source_data_issue

    for field, value in (record or {}).items():
        if str(field).startswith("_"):
            continue
        issue = infer_source_data_issue(str(field), value)
        if issue:
            return issue
    return "Record could not be matched to bulk API results"


def build_full_record_key(record: Dict[str, Any], exclude_fields: Optional[List[str]] = None, include_fields: Optional[set] = None) -> Optional[str]:
    """
    Build a key from ALL fields in a record (excluding metadata fields).
    
    This includes ALL fields, even empty ones, to ensure matching works correctly
    when records have many empty values.
    
    Args:
        record: Record dictionary
        exclude_fields: List of field names to exclude (e.g., ['sf__Id', 'sf__Error'])
        include_fields: Set of field names to include (if None, includes all fields except excluded ones)
    
    Returns:
        Key string like "Email:user@example.com|FirstName:John|LastName:Doe" or None
        Empty values are represented as "field:" (with empty value after colon)
    """
    if exclude_fields is None:
        exclude_fields = []
    
    # Default metadata fields to exclude
    # Note: sf__Created is a metadata field returned by Bulk API results
    default_exclude = ['sf__Id', 'sf__Error', 'sf__Created', 'Id', 'Error']
    all_exclude = set(exclude_fields + default_exclude)
    
    key_parts = []
    # If include_fields is specified, only use those fields (filtered by exclude list)
    fields_to_process = include_fields if include_fields is not None else set(record.keys())
    
    for field in sorted(fields_to_process):
        # Skip metadata fields and fields starting with _
        if field in all_exclude or field.startswith('_'):
            continue
        
        # Only process fields that exist in the record (when include_fields is used)
        if include_fields is not None and field not in record:
            continue
        
        value = record.get(field)
        # Include ALL fields, even if empty - this ensures matching works when records have empty values
        if value is None or value == "":
            # Represent empty values explicitly
            key_parts.append(f"{field}:")
        else:
            normalized = normalize_field_value(field, value)
            key_parts.append(f"{field}:{normalized}")
    
    return "|".join(key_parts) if key_parts else None


def _record_value_for_object_field(
    record: Dict[str, Any],
    mapping: Dict[str, Any],
    obj: str,
    salesforce_field: str,
) -> Any:
    """Resolve a Salesforce field value from a row that may use source or API keys."""
    for source_field, mapping_info in mapping.items():
        if isinstance(mapping_info, list):
            for mapping_item in mapping_info:
                if not isinstance(mapping_item, dict):
                    continue
                if (
                    mapping_item.get("object") == obj
                    and mapping_item.get("field") == salesforce_field
                ):
                    value = record_value_for_mapping_key(record, source_field)
                    if value is not None:
                        return value
        elif isinstance(mapping_info, dict):
            if (
                mapping_info.get("object") == obj
                and mapping_info.get("field") == salesforce_field
            ):
                value = record_value_for_mapping_key(record, source_field)
                if value is not None:
                    return value
    return record.get(salesforce_field)


async def migrate_data_with_bulk_api(
    data: List[Dict[str, Any]],
    mapping: Dict[str, Any],
    access_token: str,
    instance_url: str,
    user_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    org_id: Optional[str] = None,
    source_access_token: Optional[str] = None,
    source_instance_url: Optional[str] = None,
    migration_job_id: Optional[str] = None,
    job_store=None,
) -> Dict[str, Any]:
    """
    Migrate data to Salesforce using Bulk API 2.0.
    
    This function processes all records in bulk:
    1. Parses mapping to extract object records, duplicate_check_fields, and lookup_references
    2. Groups records by Salesforce object
    3. Resolves lookups in bulk
    4. Migrates each object in bulk
    
    Args:
        data: List of records to migrate
        mapping: Field mapping configuration
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        user_id: User ID for tracking (optional)
        batch_id: Batch ID for tracking (optional)
    
    Returns:
        Dictionary with migration results
    """
    print(f"🔄 [BULK API 2.0] Starting bulk migration for {len(data)} records")
    
    # Create cancel_check when migration_job_id and job_store are provided (background migration)
    cancel_check = None
    if migration_job_id and job_store:
        from utils.migration_cancel import make_cancel_check
        cancel_check = make_cancel_check(migration_job_id, job_store)
    
    if not data:
        print("⚠️ [BULK API 2.0] No data to migrate, skipping")
        return {
            "status": "skipped",
            "message": "No data to migrate",
            "results": {}
        }
    
    # Fetch field data types for rich text detection
    field_data_types = {}
    if user_id:
        field_data_types = await fetch_field_data_types(mapping, user_id, org_id)
    
    # Step 1: Parse mapping and build object records for all records
    # Two-pass approach: Separate rich text fields from regular fields
    print(f"📋 [BULK API 2.0] Step 1: Parsing mapping and grouping {len(data)} records by Salesforce object...")
    all_object_records = defaultdict(list)  # object_name -> list of records (regular fields only)
    all_rich_text_fields = defaultdict(lambda: defaultdict(dict))  # record_idx -> {obj: {field: value}}
    all_attachments = {}  # record_idx -> list of attachment metadata
    all_duplicate_check_fields = defaultdict(set)  # object_name -> set of field names
    all_lookup_references = []
    rich_text_image_ids_by_record = {}  # record_idx -> list of rich text image IDs (initialized early for scope)
    
    for record_idx, record in enumerate(data):
        object_records = {}  # object_name -> {field: value} (regular fields only)
        duplicate_check_fields = {}  # object_name -> {field: value}
        lookup_references = []
        
        # Parse mapping (same logic as migrate_single_record_to_salesforce)
        for source_field, mapping_info in mapping.items():
            # Handle array mappings (split fields)
            if isinstance(mapping_info, list):
                for mapping_item in mapping_info:
                    if not isinstance(mapping_item, dict):
                        continue
                    
                    obj = mapping_item.get("object")
                    field = mapping_item.get("field")
                    if not obj or not field:
                        continue
                    
                    # After transformation, destination field name is the key
                    destination_field_name = field
                    value = record_value_for_mapping_key(record, destination_field_name)
                    if value is None:
                        value = record.get(destination_field_name)
                    is_duplicate_check = mapping_item.get("is_duplicate_check", False)
                    
                    # Skip None or empty strings
                    if value is None or (isinstance(value, str) and value.strip() == ''):
                        continue
                    
                    # Handle lookup relationships
                    lookup_parts = parse_lookup_of_reference(mapping_item.get("lookup_of"))
                    
                    # CRITICAL: If this field has lookup_of, it's ONLY for lookup resolution
                    # DON'T add it to object_records for the lookup object (it will be processed in Step 2)
                    # Only add it to lookup_references
                    if lookup_parts:
                        child_obj, child_field = lookup_parts
                        lookup_references.append({
                            "raw_value": value,
                            "lookup_object": obj,
                            "lookup_field": field,  # Destination field: "Name"
                            "source_field": destination_field_name,  # For array mappings, use destination_field_name
                            "inject_into": child_obj,
                            "inject_field": child_field,
                            "record_index": record_idx,
                            "is_duplicate_check": is_duplicate_check
                        })
                        # Skip adding to object_records - lookup objects are processed in Step 2, not Step 4
                        continue
                    
                    # Check if this is a rich text field (separate from regular fields)
                    field_data_type = field_data_types.get((obj, field), "")
                    is_rich = field_data_type and isinstance(value, str) and value.strip() and is_rich_text_field(field_data_type, value)
                    
                    if is_rich:
                        # Store rich text field for processing after record creation
                        all_rich_text_fields[record_idx].setdefault(obj, {})[field] = value
                    else:
                        to_store = DataTransformationService.migrate_coerce_value_for_salesforce(value, field_data_type)
                        # Regular field: Add to duplicate check fields if needed
                        if is_duplicate_check:
                            duplicate_check_fields.setdefault(obj, {})
                            duplicate_check_fields[obj][field] = to_store
                        
                        # Add to object records (only for non-lookup, non-rich-text fields)
                        object_records.setdefault(obj, {})
                        object_records[obj][field] = to_store
            else:
                # Simple mapping (one-to-one)
                if not isinstance(mapping_info, dict):
                    continue
                
                obj = mapping_info.get("object")
                field = mapping_info.get("field")
                if not obj or not field:
                    continue
                
                value = record_value_for_mapping_key(record, source_field)
                is_duplicate_check = mapping_info.get("is_duplicate_check", False)
                
                # Skip None or empty strings
                if value is None or (isinstance(value, str) and value.strip() == ''):
                    continue
                
                # Handle lookup relationships
                lookup_parts = parse_lookup_of_reference(mapping_info.get("lookup_of"))
                
                # CRITICAL: If this field has lookup_of, it's ONLY for lookup resolution
                # DON'T add it to object_records for the lookup object (it will be processed in Step 2)
                # Only add it to lookup_references
                if lookup_parts:
                    child_obj, child_field = lookup_parts
                    lookup_references.append({
                        "raw_value": value,
                        "lookup_object": obj,
                        "lookup_field": field,  # Destination field: "Name"
                        "source_field": source_field,  # Source field: "CompanyName"
                        "inject_into": child_obj,
                        "inject_field": child_field,
                        "record_index": record_idx,
                        "is_duplicate_check": is_duplicate_check
                    })
                    # Skip adding to object_records - lookup objects are processed in Step 2, not Step 4
                    continue
                
                # Check if this is a rich text field (separate from regular fields)
                field_data_type = field_data_types.get((obj, field), "")
                is_rich = field_data_type and isinstance(value, str) and value.strip() and is_rich_text_field(field_data_type, value)
                
                if is_rich:
                    # Store rich text field for processing after record creation
                    all_rich_text_fields[record_idx].setdefault(obj, {})[field] = value
                else:
                    to_store = DataTransformationService.migrate_coerce_value_for_salesforce(value, field_data_type)
                    # Regular field: Add to duplicate check fields if needed
                    if is_duplicate_check:
                        duplicate_check_fields.setdefault(obj, {})
                        duplicate_check_fields[obj][field] = to_store
                    
                    # Add to object records (only for non-lookup, non-rich-text fields)
                    object_records.setdefault(obj, {})
                    object_records[obj][field] = to_store
        
        # Add to aggregated collections
        for obj, fields in object_records.items():
            # Add duplicate_check_field values to the record
            obj_duplicate_fields = duplicate_check_fields.get(obj, {})
            record_with_duplicate_check = {
                **fields,
                "_record_index": record_idx  # Track original record index
            }
            # Add duplicate check fields to record
            for field, value in obj_duplicate_fields.items():
                record_with_duplicate_check[field] = value
            
            all_object_records[obj].append(record_with_duplicate_check)
        
        # Track which fields are used for duplicate checking per object
        for obj, fields in duplicate_check_fields.items():
            for field in fields.keys():
                all_duplicate_check_fields[obj].add(field)
        
        all_lookup_references.extend(lookup_references)
        
        # Collect attachments for this record (similar to rich text collection)
        if record.get("_attachments") and source_access_token and source_instance_url:
            attachments = record.get("_attachments", [])
            if attachments and isinstance(attachments, list):
                all_attachments[record_idx] = attachments
    
    # Step 1.5: Resolve RecordTypeId for all records (name → destination ID)
    # Pre-fetch the record type map once for all objects, then apply to every record.
    # This mirrors the logic in migrate_single_record_to_salesforce (REST path).
    # Local import to avoid circular dependency (salesforce_migration_flow imports this module).
    from services.salesforce_migration_flow import fetch_record_type_map, resolve_record_type_id  # noqa: PLC0415
    print(f"🏷️  [BULK API 2.0] Step 1.5: Resolving RecordTypeId for all records...")
    record_type_map: Dict[str, Dict[str, str]] = {}
    if user_id:
        record_type_map = await fetch_record_type_map(mapping, user_id, org_id)

    if record_type_map:
        for obj, records in all_object_records.items():
            for rec in records:
                if "RecordTypeId" in rec:
                    rt_raw = rec["RecordTypeId"]
                    resolved_rt = resolve_record_type_id(obj, rt_raw, record_type_map)
                    if resolved_rt:
                        rec["RecordTypeId"] = resolved_rt
                    else:
                        # No match and no default — remove to avoid MALFORMED_ID
                        del rec["RecordTypeId"]
                        print(f"⚠️ [BULK API 2.0] Removed unresolvable RecordTypeId '{rt_raw}' from {obj} record")
                else:
                    # No RecordTypeId in payload — inject default if object has record types (EC-1)
                    default_rt = resolve_record_type_id(obj, None, record_type_map)
                    if default_rt:
                        rec["RecordTypeId"] = default_rt
        print(f"✅ [BULK API 2.0] RecordTypeId resolution complete for {len(all_object_records)} object(s)")
    else:
        # record_type_map is empty (fetch failed or no record types in DB)
        # Safety guard: if a record has a raw RecordTypeId that is NOT a valid 15/18-char SF ID, remove it
        for obj, records in all_object_records.items():
            for rec in records:
                if "RecordTypeId" in rec:
                    rt_raw = rec["RecordTypeId"]
                    rt_str = str(rt_raw).strip() if rt_raw else ""
                    if not (len(rt_str) in (15, 18) and rt_str.isalnum()):
                        del rec["RecordTypeId"]
                        print(f"⚠️ [BULK API 2.0] record_type_map empty, removed non-ID RecordTypeId '{rt_str}' from {obj} record")

    # Step 2: Resolve lookups in bulk
    print(f"🔗 [BULK API 2.0] Step 2: Resolving {len(all_lookup_references)} lookup references in bulk...")
    lookup_mapping = {}
    lookup_object_results = {}  # Store bulk API results for lookup objects
    if all_lookup_references:
        try:
            lookup_result = await resolve_lookup_references_in_bulk(
                all_lookup_references,
                access_token,
                instance_url
            )
            # Extract both lookup_mapping and lookup_object_results
            lookup_mapping = lookup_result.get("lookup_mapping", {})
            lookup_object_results = lookup_result.get("lookup_object_results", {})
            print(f"✅ [BULK API 2.0] Resolved {len(lookup_mapping)} lookup references")
            if lookup_object_results:
                print(f"📋 [BULK API 2.0] Found bulk API results for {len(lookup_object_results)} lookup object(s): {list(lookup_object_results.keys())}")
        except Exception as e:
            print(f"⚠️ [BULK API 2.0] Error resolving lookups in bulk: {e}")
    else:
        print(f"ℹ️ [BULK API 2.0] No lookup references to resolve")
    
    # Step 3: Inject lookup IDs into records
    # CRITICAL: We need the original data to get lookup field values from child records
    # Because lookup field values (e.g., Account.Name) are not stored in child records (Contact)
    # They're only in the original transformed data
    print(f"💉 [BULK API 2.0] Step 3: Injecting lookup IDs into records...")
    for obj, records in all_object_records.items():
        # Filter lookup references for this object
        obj_lookup_refs = [
            ref for ref in all_lookup_references
            if ref.get("inject_into") == obj
        ]
        
        if obj_lookup_refs:
            # Build mapping of record_index -> original record data for lookup field values
            record_index_to_original_data = {}
            for ref in obj_lookup_refs:
                record_index = ref.get("record_index")
                if record_index is not None and record_index < len(data):
                    if record_index not in record_index_to_original_data:
                        record_index_to_original_data[record_index] = data[record_index]
            
            inject_lookup_ids_into_records(records, lookup_mapping, obj_lookup_refs, record_index_to_original_data)
            print(f"   ✅ Injected lookup IDs for {obj} ({len(obj_lookup_refs)} lookups)")
    
    # Step 4: Migrate each object in bulk
    print(f"🚀 [BULK API 2.0] Step 4: Migrating {len(all_object_records)} Salesforce object(s) in bulk...")
    all_results = {}
    total_successful = 0
    total_failed = 0
    lookup_success_added = 0  # Track lookup successes to include in summary
    lookup_failed_added = 0   # Track lookup failures to include in summary
    
    try:
        from utils.batch_config import SF_BULK_MAX_RECORDS_PER_JOB
        max_per_job = SF_BULK_MAX_RECORDS_PER_JOB
    except ImportError:
        max_per_job = 500000

    for obj, records in all_object_records.items():
        # Check cancel before processing each object
        if cancel_check and await cancel_check():
            from utils.migration_cancel import MigrationCancelledError
            raise MigrationCancelledError("Migration cancelled by user")
        print(f"   📦 [BULK API 2.0] Processing {obj}: {len(records)} records")
        # Extract duplicate_check_fields for this object
        duplicate_check_field_names = list(all_duplicate_check_fields.get(obj, set()))
        duplicate_check_fields = {field: None for field in duplicate_check_field_names}

        # Split into chunks if over 10GB limit (Bulk API 2.0 max per job)
        if len(records) <= max_per_job:
            chunks = [records]
        else:
            chunks = [
                records[i : i + max_per_job]
                for i in range(0, len(records), max_per_job)
            ]
            print(f"   📦 [BULK API 2.0] Splitting {obj} into {len(chunks)} jobs (max {max_per_job} records/job)")

        obj_successful_total = 0
        obj_failed_total = 0
        chunk_results = []
        merged_update_result = None
        merged_insert_result = None
        merged_insert_record_indices: List[int] = []
        merged_update_record_indices: List[int] = []
        for idx, chunk in enumerate(chunks):
            # Check cancel before each chunk
            if cancel_check and await cancel_check():
                from utils.migration_cancel import MigrationCancelledError
                raise MigrationCancelledError("Migration cancelled by user")
            chunk_result = await migrate_object_with_bulk_api(
                obj,
                chunk,
                duplicate_check_fields,
                access_token,
                instance_url,
                cancel_check=cancel_check,
            )
            chunk_results.append(chunk_result)
            if chunk_result.get("status") == "completed":
                obj_successful_total += chunk_result.get("total_successful", 0)
                obj_failed_total += chunk_result.get("total_failed", 0)
                # Merge successful_results for record_id_map (Step 5)
                ur = chunk_result.get("update_result")
                ir = chunk_result.get("insert_result")
                if ur:
                    merged_update_result = merged_update_result or {"successful_results": [], "failed_results": []}
                    merged_update_result["successful_results"].extend(ur.get("successful_results", []))
                    merged_update_result["failed_results"].extend(ur.get("failed_results", []))
                if ir:
                    merged_insert_result = merged_insert_result or {"successful_results": [], "failed_results": []}
                    merged_insert_result["successful_results"].extend(ir.get("successful_results", []))
                    merged_insert_result["failed_results"].extend(ir.get("failed_results", []))
                merged_insert_record_indices.extend(chunk_result.get("insert_record_indices") or [])
                merged_update_record_indices.extend(chunk_result.get("update_record_indices") or [])

        # Build final result (single chunk or merged)
        if len(chunk_results) == 1:
            result = chunk_results[0]
        else:
            result = {
                "object_name": obj,
                "status": "completed",
                "total_successful": obj_successful_total,
                "total_failed": obj_failed_total,
                "total_records": len(records),
                "chunks": len(chunk_results),
                "update_result": merged_update_result,
                "insert_result": merged_insert_result,
                "insert_record_indices": merged_insert_record_indices,
                "update_record_indices": merged_update_record_indices,
            }

        all_results[obj] = result
        if result.get("status") == "completed":
            obj_successful = result.get("total_successful", 0)
            obj_failed = result.get("total_failed", 0)
            total_successful += obj_successful
            total_failed += obj_failed
            print(f"   ✅ [BULK API 2.0] {obj} completed: {obj_successful} successful, {obj_failed} failed")
        else:
            obj_failed = result.get("total_records", 0)
            total_failed += obj_failed
            print(f"   ❌ [BULK API 2.0] {obj} failed: {result.get('message', 'Unknown error')}")
    
    # Merge lookup object results into all_results (Account, etc.)
    # These were processed in Step 2 but not in Step 4
    if lookup_object_results:
        print(f"📋 [BULK API 2.0] Merging {len(lookup_object_results)} lookup object result(s) into all_results...")
        for lookup_obj, lookup_obj_result in lookup_object_results.items():
            if lookup_obj in all_results:
                # This object was already migrated as a primary object in Step 4.
                # Don't overwrite its result with the smaller lookup-only result —
                # doing so would cause create_migration_records_from_bulk_results to
                # match against the wrong (tiny) result set and lose all Contact rows.
                print(f"   ⏭️ [BULK API 2.0] Skipping lookup merge for {lookup_obj}: already present as primary object")
            else:
                all_results[lookup_obj] = lookup_obj_result
            # Update totals for lookup objects regardless (counts still apply)
            if lookup_obj_result.get("status") == "completed":
                # Prefer explicit totals if present; otherwise derive from insert/update counts
                obj_successful = lookup_obj_result.get("total_successful")
                obj_failed = lookup_obj_result.get("total_failed")
                if obj_successful is None:
                    obj_successful = (
                        lookup_obj_result.get("created_count", 0)
                        + lookup_obj_result.get("updated_count", 0)
                    )
                if obj_failed is None:
                    insert_failed = 0
                    update_failed = 0
                    insert_result = lookup_obj_result.get("insert_result") or {}
                    update_result = lookup_obj_result.get("update_result") or {}
                    insert_failed = insert_result.get("failed_count", 0)
                    update_failed = update_result.get("failed_count", 0)
                    obj_failed = insert_failed + update_failed
                total_successful += obj_successful
                total_failed += obj_failed
                lookup_success_added += obj_successful
                lookup_failed_added += obj_failed
                print(f"   ✅ [BULK API 2.0] {lookup_obj} (lookup) completed: {obj_successful} successful, {obj_failed} failed")
    
    # Step 5: Process rich text fields with record IDs (two-pass approach: Pass 2)
    # Build mapping of record_index -> {obj: record_id} from bulk results
    print(f"🖼️  [BULK API 2.0] Step 5: Processing rich text fields with record IDs...")
    record_id_map = {}  # record_index -> {obj: record_id}
    
    # Build mapping by matching bulk results to records sent to bulk API
    # Records in all_object_records have _record_index, so we can match results to those records
    for obj, obj_result in all_results.items():
        if obj_result.get("status") != "completed":
            continue
        
        # Get the records that were sent to bulk API for this object
        sent_records = all_object_records.get(obj, [])
        if not sent_records:
            continue
        
        # Collect all Salesforce field names for this object (excluding rich text and metadata fields)
        object_salesforce_fields = set()
        for sent_record in sent_records:
            for field in sent_record.keys():
                if field not in ['_record_index']:
                    object_salesforce_fields.add(field)
        
        # Build index map: key (from regular fields) -> (record_index, sent_record_index)
        index_map = {}
        for sent_idx, sent_record in enumerate(sent_records):
            record_idx = sent_record.get("_record_index")
            if record_idx is None:
                continue
            
            # Build key from sent record (excluding metadata)
            key = build_full_record_key(sent_record, exclude_fields=['_record_index'], include_fields=object_salesforce_fields)
            if key:
                if key not in index_map:
                    index_map[key] = []
                index_map[key].append((record_idx, sent_idx))
        
        # Match bulk results to sent records
        matched_sent_indices = set()
        
        # Process successful inserts
        if obj_result.get("insert_result"):
            insert_result = obj_result["insert_result"]
            successful_inserts = insert_result.get("successful_results", [])
            for success in successful_inserts:
                salesforce_id = success.get("sf__Id") or success.get("Id") or success.get("id")
                if not salesforce_id:
                    continue
                
                result_key = build_full_record_key(success, exclude_fields=['sf__Id', 'sf__Error', 'sf__Created', 'Id', 'Error'], include_fields=object_salesforce_fields)
                
                if result_key and result_key in index_map:
                    matches = index_map[result_key]
                    record_idx = None
                    for (rec_idx, sent_idx) in matches:
                        if sent_idx not in matched_sent_indices:
                            record_idx = rec_idx
                            matched_sent_indices.add(sent_idx)
                            break
                    if record_idx is None and matches:
                        record_idx = matches[0][0]
                    
                    if record_idx is not None:
                        if record_idx not in record_id_map:
                            record_id_map[record_idx] = {}
                        record_id_map[record_idx][obj] = salesforce_id
        
        # Process successful updates
        if obj_result.get("update_result"):
            update_result = obj_result["update_result"]
            successful_updates = update_result.get("successful_results", [])
            for success in successful_updates:
                salesforce_id = success.get("sf__Id") or success.get("Id") or success.get("id")
                if not salesforce_id:
                    continue
                
                result_key = build_full_record_key(success, exclude_fields=['sf__Id', 'sf__Error', 'sf__Created', 'Id', 'Error'], include_fields=object_salesforce_fields)
                
                if result_key and result_key in index_map:
                    matches = index_map[result_key]
                    record_idx = None
                    for (rec_idx, sent_idx) in matches:
                        if sent_idx not in matched_sent_indices:
                            record_idx = rec_idx
                            matched_sent_indices.add(sent_idx)
                            break
                    if record_idx is None and matches:
                        record_idx = matches[0][0]
                    
                    if record_idx is not None:
                        if record_idx not in record_id_map:
                            record_id_map[record_idx] = {}
                        record_id_map[record_idx][obj] = salesforce_id
    
    # Process rich text fields for each record that has record IDs
    total_rich_text_images_uploaded = 0
    total_rich_text_images_skipped = 0
    
    if all_rich_text_fields:
        print(f"   📝 [BULK API 2.0] Processing rich text fields for {len(all_rich_text_fields)} records...")
        
        for record_idx, obj_fields in all_rich_text_fields.items():
            record_ids = record_id_map.get(record_idx, {})
            
            if not record_ids:
                print(f"   ⚠️ [BULK API 2.0] No record IDs found for record {record_idx}, skipping rich text processing")
                continue
            
            # Process each object's rich text fields
            for obj, fields in obj_fields.items():
                record_id = record_ids.get(obj)
                if not record_id:
                    print(f"   ⚠️ [BULK API 2.0] No record ID for {obj} in record {record_idx}, skipping rich text processing")
                    continue
                
                # Process each rich text field with the record ID
                updated_fields = {}
                rich_text_image_ids = []  # Collect ContentDocument IDs for this record
                for field, value in fields.items():
                    field_data_type = field_data_types.get((obj, field), "")
                    if field_data_type and isinstance(value, str) and value.strip():
                        try:
                            processed = await process_rich_text_field(
                                field_value=value,
                                field_data_type=field_data_type,
                                access_token=access_token,
                                instance_url=instance_url,
                                source_access_token=source_access_token,
                                source_instance_url=source_instance_url,
                                target_record_id=record_id,  # Pass record ID (not used for file linking, kept for compatibility)
                                source_object_name=obj,  # Pass object name for rtaImage API calls
                                source_field_name=field,  # Pass field name for rtaImage API calls
                                user_id=user_id  # Pass user_id for plan-based upload restrictions
                            )
                            if processed.get("is_rich_text") and processed.get("updated_value"):
                                updated_fields[field] = processed["updated_value"]
                                total_rich_text_images_uploaded += processed.get("images_uploaded", 0)
                                total_rich_text_images_skipped += processed.get("images_skipped", 0)
                                # Collect rich text image IDs for rollback
                                rich_text_image_ids.extend(processed.get("rich_text_image_ids", []))
                        except Exception as e:
                            print(f"   ⚠️ [BULK API 2.0] Error processing rich text for {obj}.{field} (record {record_id}): {e}")
                
                # Store rich text image IDs for this record
                if rich_text_image_ids:
                    if record_idx not in rich_text_image_ids_by_record:
                        rich_text_image_ids_by_record[record_idx] = []
                    rich_text_image_ids_by_record[record_idx].extend(rich_text_image_ids)
                
                # Update record with processed rich text fields using REST API
                if updated_fields:
                    try:
                        from utils.salesforceapimigration import update_salesforce_record
                        await update_salesforce_record(access_token, instance_url, obj, record_id, updated_fields)
                        print(f"   ✅ [BULK API 2.0] Updated {obj} record {record_id} with {len(updated_fields)} rich text field(s)")
                    except Exception as e:
                        print(f"   ❌ [BULK API 2.0] Error updating {obj} record {record_id} with rich text fields: {e}")
        
        if total_rich_text_images_uploaded > 0 or total_rich_text_images_skipped > 0:
            print(f"   ✅ [BULK API 2.0] Rich text processing completed: {total_rich_text_images_uploaded} images uploaded, {total_rich_text_images_skipped} skipped (external URLs)")
    else:
        print(f"   ℹ️ [BULK API 2.0] No rich text fields to process")
    
    # Step 6: Process file attachments (Phase 1 + 2 unified path)
    # Path: Bulk Query metadata when record count >= threshold; parallel binary download; bulk upload + batch link
    print(f"📎 [BULK API 2.0] Step 6: Processing file attachments with record IDs...")
    total_attachments_processed = 0
    total_attachments_uploaded = 0
    total_attachments_skipped = 0
    attachment_results_by_record = {}  # record_idx -> list of attachment results
    
    if all_attachments and source_instance_url:
        print(f"   📝 [BULK API 2.0] Processing attachments for {len(all_attachments)} records...")
        
        from utils.salesforce_file_attachment import (
            download_files_parallel,
            upload_content_versions_bulk_binary,
            create_content_document_link,
            check_duplicate_attachment_by_filename
        )
        
        # Phase 1: Download all file binaries in parallel (source org)
        all_cv_ids = []
        for attachments in all_attachments.values():
            if not attachments or not isinstance(attachments, list):
                continue
            for att in attachments:
                cv_id = att.get("content_version_id")
                if cv_id:
                    all_cv_ids.append(cv_id)
        all_cv_ids = list(dict.fromkeys(all_cv_ids))  # unique, preserve order
        
        file_bytes_map: Dict[str, Optional[bytes]] = {}
        if all_cv_ids and source_access_token and source_instance_url:
            print(f"   📥 [BULK API 2.0] Downloading {len(all_cv_ids)} files in parallel from source org...")
            file_bytes_map = await download_files_parallel(
                content_version_ids=all_cv_ids,
                access_token=source_access_token,
                instance_url=source_instance_url,
            )
        
        # Phase 2: First pass — collect "link existing" / "failed" and build to_upload list
        to_upload: List[Dict[str, Any]] = []
        
        for record_idx, attachments in all_attachments.items():
            if not attachments or not isinstance(attachments, list):
                continue
            
            record_ids = record_id_map.get(record_idx, {})
            if not record_ids:
                print(f"   ⚠️ [BULK API 2.0] No record IDs found for record {record_idx}, skipping attachment processing")
                continue
            
            primary_record_id = None
            primary_object = None
            for obj, record_id in record_ids.items():
                if record_id:
                    primary_record_id = record_id
                    primary_object = obj
                    break
            
            if not primary_record_id:
                print(f"   ⚠️ [BULK API 2.0] No primary record ID for record {record_idx}, skipping attachment processing")
                continue
            
            print(f"   📎 [BULK API 2.0] Processing {len(attachments)} attachments for record {record_idx} ({primary_object}: {primary_record_id})...")
            
            attachment_results = []
            
            for attachment in attachments:
                try:
                    content_version_id = attachment.get("content_version_id")
                    title = attachment.get("title", "Untitled")
                    file_extension = attachment.get("file_extension", "")
                    share_type = attachment.get("share_type", "V")
                    visibility = attachment.get("visibility", "AllUsers")
                    
                    filename = title
                    if file_extension and not title.lower().endswith(f".{file_extension.lower()}"):
                        filename = f"{title}.{file_extension}"
                    
                    if not content_version_id:
                        attachment_results.append({"title": title, "status": "failed", "error": "Missing content_version_id"})
                        continue
                    
                    existing_content_document_id = await check_duplicate_attachment_by_filename(
                        record_id=primary_record_id,
                        filename=filename,
                        access_token=access_token,
                        instance_url=instance_url
                    )
                    
                    if existing_content_document_id:
                        print(f"   ⏭️  [BULK API 2.0] File '{filename}' already exists, linking to record {primary_record_id}...")
                        link_result = await create_content_document_link(
                            content_document_id=existing_content_document_id,
                            linked_entity_id=primary_record_id,
                            access_token=access_token,
                            instance_url=instance_url,
                            share_type=share_type,
                            visibility=visibility
                        )
                        if link_result.get("success"):
                            attachment_results.append({
                                "title": title,
                                "status": "linked_existing",
                                "content_document_id": existing_content_document_id,
                                "error": None
                            })
                            total_attachments_skipped += 1
                            continue
                        error_msg = str(link_result.get("error", "")).lower()
                        if any(k in error_msg for k in ["duplicate", "already exists", "duplicate value"]):
                            attachment_results.append({
                                "title": title,
                                "status": "skipped_duplicate",
                                "content_document_id": existing_content_document_id,
                                "error": None
                            })
                            total_attachments_skipped += 1
                            continue
                        print(f"   ⚠️ [BULK API 2.0] Duplicate file found but link failed ({link_result.get('error')}), proceeding with upload...")
                    
                    file_bytes = file_bytes_map.get(content_version_id) if file_bytes_map else None
                    if not file_bytes:
                        attachment_results.append({"title": title, "status": "failed", "error": "Download failed"})
                        continue
                    
                    to_upload.append({
                        "file_bytes": file_bytes,
                        "filename": filename,
                        "title": title,
                        "linked_entity_id": primary_record_id,
                        "share_type": share_type,
                        "visibility": visibility,
                        "record_idx": record_idx,
                        "attachment_title": title,
                    })
                
                except Exception as e:
                    print(f"   ❌ [BULK API 2.0] Exception processing {attachment.get('title', 'unknown')}: {e}")
                    import traceback
                    traceback.print_exc()
                    attachment_results.append({"title": attachment.get("title", "unknown"), "status": "failed", "error": str(e)})
            
            attachment_results_by_record[record_idx] = attachment_results
            total_attachments_processed += len(attachments)
        
        # Phase 2: Bulk upload + batch link (single call for all to_upload)
        if to_upload:
            print(f"   📤 [BULK API 2.0] Bulk uploading {len(to_upload)} files (parallel upload + batch link)...")
            upload_payload = [
                {
                    "file_bytes": item["file_bytes"],
                    "filename": item["filename"],
                    "title": item["title"],
                    "linked_entity_id": item["linked_entity_id"],
                    "share_type": item["share_type"],
                    "visibility": item["visibility"],
                    "key": i,
                }
                for i, item in enumerate(to_upload)
            ]
            bulk_results = await upload_content_versions_bulk_binary(
                files=upload_payload,
                access_token=access_token,
                instance_url=instance_url,
                user_id=user_id,
            )
            for item, res in zip(to_upload, bulk_results):
                record_idx = item["record_idx"]
                title = item["attachment_title"]
                status = "linked" if res.get("success") else ("uploaded_not_linked" if res.get("content_document_id") else "failed")
                attachment_results_by_record[record_idx].append({
                    "title": title,
                    "status": status,
                    "content_document_id": res.get("content_document_id"),
                    "error": res.get("error"),
                })
                if res.get("success"):
                    total_attachments_uploaded += 1
        
        if total_attachments_processed > 0:
            print(f"   ✅ [BULK API 2.0] Attachment processing completed: {total_attachments_uploaded} uploaded, {total_attachments_skipped} skipped (duplicates), {total_attachments_processed - total_attachments_uploaded - total_attachments_skipped} failed")
    else:
        print(f"   ℹ️ [BULK API 2.0] No attachments to process")
    
    print(f"✅ [BULK API 2.0] Migration completed: {total_successful} successful, {total_failed} failed out of {len(data)} total records")
    print(f"📊 [BULK API 2.0] Objects migrated: {list(all_results.keys())}")
    
    final_result = {
        "status": "completed",
        "results": all_results,
        "summary": {
            # Include lookup records (success + failure) in totals
            "total": len(data) + lookup_success_added + lookup_failed_added,
            "total_records": len(data) + lookup_success_added + lookup_failed_added,  # Keep for backward compatibility
            "successful": total_successful,  # Add for background_migration_service compatibility
            "failed": total_failed,  # Add for background_migration_service compatibility
            "total_successful": total_successful,  # Keep for backward compatibility
            "total_failed": total_failed,  # Keep for backward compatibility
            "objects_migrated": list(all_results.keys()),
            "rich_text_images_uploaded": total_rich_text_images_uploaded,
            "attachments_uploaded": total_attachments_uploaded,
        },
        "method": "bulk_api_2.0",  # Add method indicator
        # Expose lookup mapping so downstream (migration record creation) can map
        # each source record to its specific lookup Id instead of using a single Id
        "lookup_mapping": lookup_mapping,
        # Store attachment results by record index for migration record creation
        "attachment_results_by_record": attachment_results_by_record,
        # Store rich text image IDs by record index for migration record creation
        "rich_text_image_ids_by_record": rich_text_image_ids_by_record
    }
    return final_result


async def create_migration_records_from_bulk_results(
    bulk_result: Dict[str, Any],
    original_data: List[Dict[str, Any]],
    mapping: Dict[str, Any],
    batch_id: str,
    user_id: str,
    lookup_mapping: Optional[Dict[str, str]] = None,
    attachment_results_by_record: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    rich_text_image_ids_by_record: Optional[Dict[int, List[Dict[str, Any]]]] = None
) -> None:
    """
    Create migration records for all processed records based on bulk results.
    
    This function matches bulk API results back to original records and creates
    individual migration records in the database, similar to REST API flow.
    
    Args:
        bulk_result: Result from migrate_data_with_bulk_api()
        original_data: Original list of records
        mapping: Field mapping configuration
        batch_id: Existing batch ID from migration_batches table
        user_id: User ID
    Returns:
        Dict with index lists:
            - successful_record_indices: source record indices with overall success
            - failed_record_indices: source record indices with overall failure
    """
    if not user_id or not batch_id:
        print("⚠️ [BULK API 2.0] Skipping migration record creation: missing user_id or batch_id")
        return
    
    print(f"📝 [BULK API 2.0] Creating migration records from bulk results...")
    lookup_mapping = lookup_mapping or {}
    
    # Step 1: Extract duplicate_check_fields from mapping for each object
    # CRITICAL: Only include objects that have NON-LOOKUP fields (i.e., objects processed in Step 4)
    # Lookup-only objects (all fields have lookup_of) are processed in Step 2, not Step 4
    object_duplicate_check_fields = defaultdict(dict)  # object_name -> {field: None}
    object_has_non_lookup_fields = defaultdict(bool)  # object_name -> has non-lookup fields
    
    for source_field, mapping_info in mapping.items():
        if isinstance(mapping_info, list):
            for mapping_item in mapping_info:
                if not isinstance(mapping_item, dict):
                    continue
                obj = mapping_item.get("object")
                field = mapping_item.get("field")
                is_duplicate_check = mapping_item.get("is_duplicate_check", False)
                is_lookup_field = "lookup_of" in mapping_item
                
                # Track if this object has non-lookup fields
                if obj and field and not is_lookup_field:
                    object_has_non_lookup_fields[obj] = True
                
                # Only add to duplicate_check_fields if it's a duplicate check field
                if obj and field and is_duplicate_check:
                    object_duplicate_check_fields[obj][field] = None
        else:
            if not isinstance(mapping_info, dict):
                continue
            obj = mapping_info.get("object")
            field = mapping_info.get("field")
            is_duplicate_check = mapping_info.get("is_duplicate_check", False)
            is_lookup_field = "lookup_of" in mapping_info
            
            # Track if this object has non-lookup fields
            if obj and field and not is_lookup_field:
                object_has_non_lookup_fields[obj] = True
            
            # Only add to duplicate_check_fields if it's a duplicate check field
            if obj and field and is_duplicate_check:
                object_duplicate_check_fields[obj][field] = None
    
    # Step 2: Build index map of original records using ALL fields (not just duplicate_check_fields)
    # This allows O(1) lookup when matching results back to records
    # Using all fields makes matching more robust, especially when duplicate_check_fields are empty
    # CRITICAL: Only build record_index_maps for objects that have non-lookup fields (processed in Step 4)
    # Lookup-only objects (all fields have lookup_of) are handled in Step 4.5, not here
    record_index_maps = {}  # object_name -> {full_record_key: record_index}
    object_salesforce_fields_map = {}  # object_name -> set of Salesforce field names
    
    for obj, has_non_lookup in object_has_non_lookup_fields.items():
        # Skip lookup-only objects (objects with ONLY lookup fields) — handled in Step 4.5
        if not has_non_lookup:
            print(f"   ⏭️ [BULK API 2.0] Skipping record_index_map for lookup-only object: {obj}")
            continue
        index_map = {}
        
        # First, collect all Salesforce field names for this object from the mapping
        object_salesforce_fields = set()
        for source_field, mapping_info in mapping.items():
            if isinstance(mapping_info, list):
                for mapping_item in mapping_info:
                    if not isinstance(mapping_item, dict):
                        continue
                    if mapping_item.get("object") == obj:
                        salesforce_field = mapping_item.get("field")
                        if salesforce_field:
                            object_salesforce_fields.add(salesforce_field)
            else:
                if not isinstance(mapping_info, dict):
                    continue
                if mapping_info.get("object") == obj:
                    salesforce_field = mapping_info.get("field")
                    if salesforce_field:
                        object_salesforce_fields.add(salesforce_field)
        
        for record_idx, record in enumerate(original_data):
            # original_data already has Salesforce field names after transformation
            # Extract ALL Salesforce field values for this object directly from the record
            obj_record = {}  # Will contain Salesforce field names -> values
            
            for salesforce_field in object_salesforce_fields:
                value = _record_value_for_object_field(
                    record, mapping, obj, salesforce_field
                )
                obj_record[salesforce_field] = value if value is not None else ""
            
            # Build key using ALL fields (not just duplicate_check_fields).
            # Exclude RecordTypeId: original_data has the raw name ("Test 1") but
            # result rows carry the resolved Salesforce ID — they never match.
            key = build_full_record_key(obj_record, exclude_fields=['RecordTypeId'])
            if key:
                # Handle multiple records with same field values
                # Store as list of indices (use first one for now)
                if key not in index_map:
                    index_map[key] = []
                index_map[key].append(record_idx)
        
        record_index_maps[obj] = index_map
        object_salesforce_fields_map[obj] = object_salesforce_fields  # Store for later use
    
    # Step 3: Track which records have been processed (to avoid duplicates)
    processed_records = {}  # record_index -> {object_name: result_data}
    
    # Step 4: Process bulk results and match to original records
    all_results = bulk_result.get("results", {})
    
    for obj, obj_result in all_results.items():
        index_map = record_index_maps.get(obj, {})
        
        if not index_map:
            print(f"   ⚠️ [BULK API 2.0] Skipping {obj}: no index_map (no records mapped to this object)")
            continue
        
        # Process update results
        if obj_result.get("update_result"):
            update_result = obj_result["update_result"]
            
            successful_updates = update_result.get("successful_results", [])
            failed_updates = update_result.get("failed_results", [])
            
            # Determine which fields are present in UPDATE results (check ALL records for union of fields)
            update_result_fields = set()
            all_update_records = successful_updates + failed_updates
            for record in all_update_records:
                update_result_fields.update(record.keys())
            # Exclude metadata fields from field list
            metadata_fields = {'sf__Id', 'sf__Error', 'sf__Created', 'Id', 'Error'}
            update_result_fields = update_result_fields - metadata_fields
            
            if update_result_fields:
                object_salesforce_fields = object_salesforce_fields_map.get(obj, set())
                
                sf_to_transformed_key_map = {}
                for source_field, mapping_info in mapping.items():
                    if isinstance(mapping_info, list):
                        for mapping_item in mapping_info:
                            if not isinstance(mapping_item, dict):
                                continue
                            sf_field = mapping_item.get("field")
                            if sf_field and sf_field in update_result_fields:
                                sf_to_transformed_key_map[sf_field] = sf_field
                    elif isinstance(mapping_info, dict):
                        sf_field = mapping_info.get("field")
                        if sf_field and sf_field in update_result_fields:
                            if source_field in mapping:
                                sf_to_transformed_key_map[sf_field] = source_field
                            elif sf_field in mapping:
                                sf_to_transformed_key_map[sf_field] = sf_field
                
                update_index_map = {}
                for record_idx, record in enumerate(original_data):
                    obj_record = {}
                    for salesforce_field in update_result_fields:
                        if salesforce_field in object_salesforce_fields:
                            value = _record_value_for_object_field(
                                record, mapping, obj, salesforce_field
                            )
                            obj_record[salesforce_field] = value if value is not None else ""
                    
                    key = build_full_record_key(obj_record, exclude_fields=['RecordTypeId'])
                    if key:
                        if key not in update_index_map:
                            update_index_map[key] = []
                        update_index_map[key].append(record_idx)
                
                index_map = update_index_map
            
            matched_indices_for_update = set()
            
            for success in successful_updates:
                object_salesforce_fields = object_salesforce_fields_map.get(obj, set())
                mapped_fields_only = update_result_fields.intersection(object_salesforce_fields)
                
                key = build_full_record_key(success, exclude_fields=['sf__Created', 'RecordTypeId'], include_fields=mapped_fields_only)
                if key and key in index_map:
                    record_indices = index_map[key]
                    record_idx = None
                    for idx in record_indices:
                        if idx not in matched_indices_for_update:
                            record_idx = idx
                            matched_indices_for_update.add(idx)
                            break
                    
                    if record_idx is None:
                        record_idx = record_indices[0]
                    
                    if record_idx not in processed_records:
                        processed_records[record_idx] = {}
                    
                    if obj not in processed_records[record_idx]:
                        processed_records[record_idx][obj] = {}
                    processed_records[record_idx][obj]["status"] = "updated"
                    salesforce_id = success.get("sf__Id") or success.get("Id") or success.get("id")
                    processed_records[record_idx][obj]["id"] = salesforce_id
                    processed_records[record_idx][obj]["result"] = success
            
            for failure in failed_updates:
                object_salesforce_fields = object_salesforce_fields_map.get(obj, set())
                mapped_fields_only = update_result_fields.intersection(object_salesforce_fields)
                
                key = build_full_record_key(failure, exclude_fields=['RecordTypeId'], include_fields=mapped_fields_only)
                if key and key in index_map:
                    record_indices = index_map[key]
                    record_idx = None
                    for idx in record_indices:
                        if idx not in matched_indices_for_update:
                            record_idx = idx
                            matched_indices_for_update.add(idx)
                            break
                    
                    if record_idx is None:
                        record_idx = record_indices[0]
                    
                    if record_idx not in processed_records:
                        processed_records[record_idx] = {}
                    
                    if obj not in processed_records[record_idx]:
                        processed_records[record_idx][obj] = {}
                    processed_records[record_idx][obj]["status"] = "error"
                    error_message = failure.get("sf__Error") or failure.get("Error", "Unknown error")
                    processed_records[record_idx][obj]["message"] = error_message
                    processed_records[record_idx][obj]["result"] = failure
        
        # Process insert results
        if obj_result.get("insert_result"):
            insert_result = obj_result["insert_result"]
            
            successful_inserts = insert_result.get("successful_results", [])
            failed_inserts = insert_result.get("failed_results", [])
            
            insert_result_fields = set()
            all_insert_records = successful_inserts + failed_inserts
            for record in all_insert_records:
                insert_result_fields.update(record.keys())
            metadata_fields = {'sf__Id', 'sf__Error', 'sf__Created', 'Id', 'Error'}
            insert_result_fields = insert_result_fields - metadata_fields
            
            if insert_result_fields:
                object_salesforce_fields = object_salesforce_fields_map.get(obj, set())
                
                sf_to_transformed_key_map = {}
                for source_field, mapping_info in mapping.items():
                    if isinstance(mapping_info, list):
                        for mapping_item in mapping_info:
                            if not isinstance(mapping_item, dict):
                                continue
                            sf_field = mapping_item.get("field")
                            if sf_field and sf_field in insert_result_fields:
                                sf_to_transformed_key_map[sf_field] = sf_field
                    elif isinstance(mapping_info, dict):
                        sf_field = mapping_info.get("field")
                        if sf_field and sf_field in insert_result_fields:
                            if source_field in mapping:
                                sf_to_transformed_key_map[sf_field] = source_field
                            elif sf_field in mapping:
                                sf_to_transformed_key_map[sf_field] = sf_field
                
                insert_index_map = {}
                for record_idx, record in enumerate(original_data):
                    obj_record = {}
                    for salesforce_field in insert_result_fields:
                        if salesforce_field in object_salesforce_fields:
                            value = _record_value_for_object_field(
                                record, mapping, obj, salesforce_field
                            )
                            obj_record[salesforce_field] = value if value is not None else ""
                    
                    key = build_full_record_key(obj_record, exclude_fields=['RecordTypeId'])
                    if key:
                        if key not in insert_index_map:
                            insert_index_map[key] = []
                        insert_index_map[key].append(record_idx)
                
                index_map = insert_index_map
            
            matched_indices_for_insert = set()
            
            for success_idx, success in enumerate(successful_inserts):
                object_salesforce_fields = object_salesforce_fields_map.get(obj, set())
                mapped_fields_only = insert_result_fields.intersection(object_salesforce_fields)
                
                key = build_full_record_key(success, exclude_fields=['sf__Created', 'RecordTypeId'], include_fields=mapped_fields_only)
                
                if key and key in index_map:
                    record_indices = index_map[key]
                    record_idx = None
                    for idx in record_indices:
                        if idx not in matched_indices_for_insert:
                            record_idx = idx
                            matched_indices_for_insert.add(idx)
                            break
                    
                    if record_idx is None:
                        record_idx = record_indices[0]
                    
                    if record_idx not in processed_records:
                        processed_records[record_idx] = {}
                    
                    if obj not in processed_records[record_idx]:
                        processed_records[record_idx][obj] = {}
                    processed_records[record_idx][obj]["status"] = "created"
                    salesforce_id = success.get("sf__Id") or success.get("Id") or success.get("id")
                    processed_records[record_idx][obj]["id"] = salesforce_id
                    processed_records[record_idx][obj]["result"] = success

            # Bulk API insert successfulResults are often Id-only (no field values to match on).
            insert_record_indices = obj_result.get("insert_record_indices") or []
            positional_insert_count = 0
            for success_idx, success in enumerate(successful_inserts):
                if success_idx >= len(insert_record_indices):
                    continue
                record_idx = insert_record_indices[success_idx]
                if (
                    record_idx in processed_records
                    and obj in processed_records[record_idx]
                    and processed_records[record_idx][obj].get("id")
                ):
                    continue
                salesforce_id = success.get("sf__Id") or success.get("Id") or success.get("id")
                if not salesforce_id:
                    continue
                if record_idx not in processed_records:
                    processed_records[record_idx] = {}
                if obj not in processed_records[record_idx]:
                    processed_records[record_idx][obj] = {}
                processed_records[record_idx][obj]["status"] = "created"
                processed_records[record_idx][obj]["id"] = salesforce_id
                processed_records[record_idx][obj]["result"] = success
                matched_indices_for_insert.add(record_idx)
                positional_insert_count += 1
            if positional_insert_count:
                print(
                    f"   📍 [BULK API 2.0] Positional insert match for {obj}: "
                    f"{positional_insert_count} record(s)"
                )
            
            for failure in failed_inserts:
                object_salesforce_fields = object_salesforce_fields_map.get(obj, set())
                mapped_fields_only = insert_result_fields.intersection(object_salesforce_fields)
                
                key = build_full_record_key(failure, exclude_fields=['RecordTypeId'], include_fields=mapped_fields_only)
                if key and key in index_map:
                    record_indices = index_map[key]
                    record_idx = None
                    for idx in record_indices:
                        if idx not in matched_indices_for_insert:
                            record_idx = idx
                            matched_indices_for_insert.add(idx)
                            break
                    
                    if record_idx is None:
                        record_idx = record_indices[0]
                    
                    if record_idx not in processed_records:
                        processed_records[record_idx] = {}
                    
                    if obj not in processed_records[record_idx]:
                        processed_records[record_idx][obj] = {}
                    processed_records[record_idx][obj]["status"] = "error"
                    error_message = failure.get("sf__Error") or failure.get("Error", "Unknown error")
                    processed_records[record_idx][obj]["message"] = error_message
                    processed_records[record_idx][obj]["result"] = failure
    
    # Step 4.5: Handle lookup objects (objects in results but not in record_index_maps)
    lookup_objects = set(all_results.keys()) - set(record_index_maps.keys())
    
    if lookup_objects:
        print(f"   📋 [BULK API 2.0] Found {len(lookup_objects)} lookup object(s) to map back: {list(lookup_objects)}")
        
        lookup_object_to_record_indices = defaultdict(set)
        
        for source_field, mapping_info in mapping.items():
            lookup_object = None
            
            if isinstance(mapping_info, list):
                for mapping_item in mapping_info:
                    if not isinstance(mapping_item, dict):
                        continue
                    if "lookup_of" in mapping_item:
                        lookup_object = mapping_item.get("object")
                        break
            else:
                if isinstance(mapping_info, dict) and "lookup_of" in mapping_info:
                    lookup_object = mapping_info.get("object")
            
            if lookup_object and lookup_object in lookup_objects:
                for record_idx, record in enumerate(original_data):
                    value = record_value_for_mapping_key(record, source_field)
                    if value is not None and (not isinstance(value, str) or value.strip()):
                        lookup_object_to_record_indices[lookup_object].add(record_idx)
        
        for lookup_obj in lookup_objects:
            if lookup_obj not in all_results:
                continue
                
            obj_result = all_results[lookup_obj]
            record_indices = lookup_object_to_record_indices.get(lookup_obj, set())
            
            if not record_indices:
                print(f"   ⚠️ [BULK API 2.0] No source records found for lookup object {lookup_obj}")
                continue
            
            print(f"   🔗 [BULK API 2.0] Adding {lookup_obj} results to {len(record_indices)} source record(s)")
            
            lookup_status = None
            lookup_error = None
            successful_inserts = []
            failed_inserts = []
            successful_updates = []
            failed_updates = []
            
            if obj_result.get("insert_result"):
                insert_result = obj_result["insert_result"]
                successful_inserts = insert_result.get("successful_results", [])
                failed_inserts = insert_result.get("failed_results", [])
                if successful_inserts:
                    lookup_status = "created"
                elif failed_inserts:
                    lookup_status = "error"
                    lookup_error = failed_inserts[0].get("sf__Error") or failed_inserts[0].get("Error", "Unknown error")
            
            elif obj_result.get("update_result"):
                update_result = obj_result["update_result"]
                successful_updates = update_result.get("successful_results", [])
                failed_updates = update_result.get("failed_results", [])
                if successful_updates:
                    lookup_status = "updated"
                elif failed_updates:
                    lookup_status = "error"
                    lookup_error = failed_updates[0].get("sf__Error") or failed_updates[0].get("Error", "Unknown error")
            
            if lookup_status is None:
                print(f"   ⚠️ [BULK API 2.0] Lookup object {lookup_obj} has no valid status in results: {obj_result}")
                continue
            
            for record_idx in record_indices:
                record = original_data[record_idx] if record_idx < len(original_data) else {}
                lookup_fields = {}
                lookup_fields_with_duplicate_check = {}
                for source_field, mapping_info in mapping.items():
                    if isinstance(mapping_info, list):
                        for mapping_item in mapping_info:
                            if not isinstance(mapping_item, dict):
                                continue
                            if mapping_item.get("object") == lookup_obj and "lookup_of" in mapping_item:
                                dest_field = mapping_item.get("field")
                                is_duplicate_check = mapping_item.get("is_duplicate_check", False)
                                if dest_field:
                                    value = record_value_for_mapping_key(record, source_field)
                                    if value is not None and (not isinstance(value, str) or value.strip()):
                                        lookup_fields[dest_field] = value
                                        if is_duplicate_check:
                                            lookup_fields_with_duplicate_check[dest_field] = value
                    elif isinstance(mapping_info, dict):
                        if mapping_info.get("object") == lookup_obj and "lookup_of" in mapping_info:
                            dest_field = mapping_info.get("field")
                            is_duplicate_check = mapping_info.get("is_duplicate_check", False)
                            if dest_field:
                                value = record_value_for_mapping_key(record, source_field)
                                if value is not None and (not isinstance(value, str) or value.strip()):
                                    lookup_fields[dest_field] = value
                                    if is_duplicate_check:
                                        lookup_fields_with_duplicate_check[dest_field] = value
                
                lookup_id = None
                identifier = None
                if lookup_fields_with_duplicate_check:
                    composite_parts = []
                    for field in sorted(lookup_fields_with_duplicate_check.keys()):
                        val = lookup_fields_with_duplicate_check[field]
                        normalized = normalize_field_value(field, val)
                        composite_parts.append(f"{field}:{normalized}")
                    composite_key = "|".join(composite_parts)
                    identifier = f"{lookup_obj}|{composite_key}" if composite_key else None
                    if identifier:
                        lookup_id = lookup_mapping.get(identifier)
                elif lookup_fields:
                    composite_parts = []
                    for field in sorted(lookup_fields.keys()):
                        val = lookup_fields[field]
                        normalized = normalize_field_value(field, val)
                        composite_parts.append(f"{field}:{normalized}")
                    composite_key = "|".join(composite_parts)
                    identifier = f"{lookup_obj}|{composite_key}" if composite_key else None
                    if identifier:
                        lookup_id = lookup_mapping.get(identifier)
                
                if not lookup_id:
                    if len(successful_inserts) == 1:
                        lookup_id = successful_inserts[0].get("sf__Id") or successful_inserts[0].get("Id") or successful_inserts[0].get("id")
                    elif len(successful_updates) == 1:
                        lookup_id = successful_updates[0].get("sf__Id") or successful_updates[0].get("Id") or successful_updates[0].get("id")
                
                if record_idx not in processed_records:
                    processed_records[record_idx] = {}
                if lookup_obj not in processed_records[record_idx]:
                    processed_records[record_idx][lookup_obj] = {}
                
                if lookup_status == "error" or not lookup_id:
                    processed_records[record_idx][lookup_obj]["status"] = "error"
                    processed_records[record_idx][lookup_obj]["message"] = lookup_error or "Lookup Id not found for record"
                else:
                    processed_records[record_idx][lookup_obj]["status"] = lookup_status
                    processed_records[record_idx][lookup_obj]["id"] = lookup_id
    
    # Step 5: Handle unmatched records and create migration records for all processed records
    matched_indices = set(processed_records.keys())
    all_indices = set(range(len(original_data)))
    unmatched_indices = all_indices - matched_indices
    if unmatched_indices:
        print(f"   ⚠️ [BULK API 2.0] {len(unmatched_indices)} records could not be matched to bulk results - creating records with error status")
        all_results_dict = bulk_result.get("results", {})
        object_names = list(all_results_dict.keys())
        default_obj = object_names[0] if object_names else "Unknown"
        
        for record_idx in unmatched_indices:
            original_record = original_data[record_idx] if record_idx < len(original_data) else {}
            detail_message = _unmatched_record_error_message(original_record)
            processed_records[record_idx] = {
                default_obj: {
                    "status": "error",
                    "message": detail_message,
                }
            }
    
    print(f"   📝 [BULK API 2.0] Creating {len(processed_records)} migration records...")
    
    successful_record_indices: List[int] = []
    failed_record_indices: List[int] = []

    for record_idx, obj_results in processed_records.items():
        if record_idx >= len(original_data):
            print(f"   ⚠️ [BULK API 2.0] Record index {record_idx} out of range, skipping")
            continue
        
        original_record = original_data[record_idx]
        
        overall_status = "success"
        error_message = None
        
        salesforce_results = {}
        primary_object = None
        
        for obj, obj_result_data in obj_results.items():
            if obj_result_data.get("status") == "error":
                overall_status = "failed"
                if not error_message:
                    error_message = obj_result_data.get("message", "Unknown error")
                salesforce_results[obj] = {
                    "status": "error",
                    "message": obj_result_data.get("message", "Unknown error")
                }
            else:
                status = obj_result_data.get("status") or "success"
                salesforce_id = obj_result_data.get("id") or obj_result_data.get("Id") or obj_result_data.get("sf__Id")
                
                if not primary_object and salesforce_id:
                    primary_object = obj
                
                if status == "updated":
                    message = "Successfully updated in Salesforce"
                elif status == "created":
                    message = "Successfully created in Salesforce"
                else:
                    message = "Successfully processed in Salesforce"
                
                salesforce_results[obj] = {
                    "status": status,
                    "id": salesforce_id,
                    "message": message
                }
        
        if attachment_results_by_record and record_idx in attachment_results_by_record:
            attachments = attachment_results_by_record[record_idx]
            if attachments and primary_object and primary_object in salesforce_results:
                salesforce_results[primary_object]["attachments"] = attachments
        
        if rich_text_image_ids_by_record and record_idx in rich_text_image_ids_by_record:
            rich_text_images = rich_text_image_ids_by_record[record_idx]
            if rich_text_images and primary_object and primary_object in salesforce_results:
                if "rich_text_images" not in salesforce_results[primary_object]:
                    salesforce_results[primary_object]["rich_text_images"] = []
                salesforce_results[primary_object]["rich_text_images"].extend(rich_text_images)
        
        try:
            record_id = await MigrationRecordsService.create_migration_record(
                batch_id=batch_id,
                user_id=user_id,
                record_index=record_idx,
                source_data=original_record,
                mapped_data=original_record
            )
            
            await MigrationRecordsService.update_migration_record_result(
                record_id=record_id,
                salesforce_results=salesforce_results,
                status=overall_status,
                error_message=error_message
            )
        except Exception as e:
            print(f"   ❌ [BULK API 2.0] Failed to create migration record for index {record_idx}: {e}")
            continue
        
        if overall_status == "success":
            successful_record_indices.append(record_idx)
        else:
            failed_record_indices.append(record_idx)
    
    print(f"✅ [BULK API 2.0] Created {len(processed_records)} migration records")
    return {
        "successful_record_indices": sorted(set(successful_record_indices)),
        "failed_record_indices": sorted(set(failed_record_indices)),
    }
