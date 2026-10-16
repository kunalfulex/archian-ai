from utils.salesforceapimigration import (
    query_existing_salesforce_record,
    create_salesforce_record,
    update_salesforce_record,
    get_valid_salesforce_token,
)
from services.migration_records_service import MigrationRecordsService
from services.salesforce_bulk_migration_flow import migrate_data_with_bulk_api
from datetime import datetime
from services.entitlements import get_entitlements
from typing import Optional, Dict, Any, List
from db.connection import database
from services.rich_text_image_service import process_rich_text_field
from utils.rich_text_handler import fetch_field_data_types
from services.data_transformation_service import DataTransformationService
from utils.sf_mapping_lookup_sanitize import parse_lookup_of_reference


async def fetch_record_type_map(
    mapping: dict,
    user_id: str,
    org_id: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """
    Pre-fetch record type name→ID maps for all objects in the mapping that have record types.
    Called once before the migration loop to avoid N+1 DB queries.

    Returns:
        Dict keyed by object_api_name, each value is a dict of
        {lowercase_name: record_type_id} for all active record types of that object.
        Example: {"Contact": {"business account": "0121U000001ABCDEF", "person account": "0121U000001XYZABC"}}
    """
    if not user_id:
        return {}

    # Collect all destination objects from the mapping
    objects = set()
    for mapping_info in mapping.values():
        if isinstance(mapping_info, list):
            for item in mapping_info:
                if isinstance(item, dict) and item.get("object"):
                    objects.add(item["object"])
        elif isinstance(mapping_info, dict) and mapping_info.get("object"):
            objects.add(mapping_info["object"])

    if not objects:
        return {}

    try:
        org_filter = "AND rt.org_id = :org_id" if org_id else ""
        rows = await database.fetch_all(
            f"""
            SELECT o.object_api_name, rt.record_type_id, rt.name, rt.developer_name, rt.is_default
            FROM salesforce_object_record_types rt
            JOIN salesforce_objects o
              ON o.uuid_id = rt.salesforce_object_uuid
            WHERE rt.user_id = :user_id
              AND rt.is_active = true
              AND o.object_api_name = ANY(:objects)
              {org_filter}
            """,
            values={
                "user_id": str(user_id),
                "objects": list(objects),
                **({"org_id": org_id} if org_id else {}),
            },
        )

        record_type_map: Dict[str, Dict[str, str]] = {}
        default_map: Dict[str, str] = {}

        for row in rows:
            obj = row["object_api_name"]
            rt_id = row["record_type_id"]
            name = (row["name"] or "").lower()
            dev_name = (row["developer_name"] or "").lower()

            if obj not in record_type_map:
                record_type_map[obj] = {}

            record_type_map[obj][name] = rt_id
            if dev_name and dev_name != name:
                record_type_map[obj][dev_name] = rt_id

            if row["is_default"] and obj not in default_map:
                default_map[obj] = rt_id

        # Store defaults under a reserved key so resolve_record_type_id can find them
        for obj, default_id in default_map.items():
            record_type_map.setdefault(obj, {})
            record_type_map[obj]["__default__"] = default_id

        return record_type_map

    except Exception as e:
        print(f"⚠️ [RecordType] Failed to pre-fetch record type map: {e}")
        return {}


def resolve_record_type_id(
    object_api_name: str,
    value: Optional[str],
    record_type_map: Dict[str, Dict[str, str]],
) -> Optional[str]:
    """
    Resolve a record type name/developer_name/ID to the correct 18-char record_type_id.

    Resolution order:
      1. If value is None/empty → return default record type ID (or None if no record types)
      2. If value matches a name or developer_name (case-insensitive) → return that ID
      3. If value looks like an 18-char Salesforce ID → return as-is (pass-through)
      4. No match → return default record type ID (graceful fallback, Salesforce assigns default)
      5. If object has no record types at all → return None (no injection)
    """
    obj_map = record_type_map.get(object_api_name)
    if not obj_map:
        return None  # EC-4: object has no record types

    default_id = obj_map.get("__default__")

    if not value or (isinstance(value, str) and not value.strip()):
        return default_id  # EC-7: null/empty value → use default

    value_str = str(value).strip()

    # Try name / developer_name match (case-insensitive)
    matched = obj_map.get(value_str.lower())
    if matched:
        return matched

    # If it looks like a Salesforce 15 or 18-char ID, pass through
    if len(value_str) in (15, 18) and value_str.isalnum():
        return value_str

    # No match → graceful fallback to default (EC-5: different name between orgs)
    print(f"⚠️ [RecordType] No match for '{value_str}' on {object_api_name}, using default")
    return default_id


async def resolve_lookup_reference(access_token, instance_url, object_name, query_fields):
    """
    Resolve lookup reference by querying/creating a record in the lookup object.
    
    Args:
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        object_name: Name of the lookup object (e.g., "Contact")
        query_fields: Dictionary of field_name: value pairs to query/create with
                     (e.g., {"FirstName": "John", "LastName": "Doe"})
    
    Returns:
        The ID of the existing or newly created record, or None if error
    """
    try:
        # Validate query_fields
        if not query_fields or not isinstance(query_fields, dict):
            return None
        
        # Filter out None values and lists (invalid for queries)
        valid_query_fields = {
            k: v for k, v in query_fields.items() 
            if v is not None and not isinstance(v, list)
        }
        
        if not valid_query_fields:
            return None
        
        existing = await query_existing_salesforce_record(access_token, instance_url, object_name, valid_query_fields)
        if existing:
            return existing["Id"]

        created = await create_salesforce_record(access_token, instance_url, object_name, valid_query_fields)
        return created.get("id") if isinstance(created, dict) else None
    except Exception as e:
        print(f"❌ Lookup error for {object_name}: {e}")
        return None


#new logic for multiple records
async def migrate_single_record_to_salesforce(
    record, 
    mapping, 
    access_token, 
    instance_url,
    user_id: Optional[str] = None,
    org_id: Optional[str] = None,
    source_access_token: Optional[str] = None,
    source_instance_url: Optional[str] = None,
    field_data_types: Optional[Dict[tuple, str]] = None,
    record_type_map: Optional[Dict[str, Dict[str, str]]] = None,
):
    object_records = {}
    lookup_references = []
    duplicate_check_fields = {}
    
    # Use provided field_data_types or fetch if not provided
    if field_data_types is None:
        field_data_types = {}
        if user_id:
            field_data_types = await fetch_field_data_types(mapping, user_id, org_id)

    # Step 1: Build object field data
    for source_field, mapping_info in mapping.items():
        # Handle array mappings (split fields)
        # After transformation, split fields are removed from data and replaced with destination field names
        # e.g., "Contact Name" -> removed, replaced with "FirstName" and "LastName" in the data
        if isinstance(mapping_info, list):
            # Array mapping (split field) - process each item in the array
            # The transformation service already created new fields with destination field names
            # So we need to look for those destination field names in the record
            for mapping_item in mapping_info:
                if not isinstance(mapping_item, dict):
                    continue
                
                obj = mapping_item.get("object")
                field = mapping_item.get("field")
                if not obj or not field:
                    continue
                
                # After transformation, the destination field name is the key in the record
                # e.g., "FirstName" and "LastName" are the keys, not "Contact Name"
                destination_field_name = field
                value = record.get(destination_field_name)
                is_duplicate_check = mapping_item.get("is_duplicate_check", False)

                # Skip None or empty strings (empty strings should not be sent to Salesforce)
                if value is None or (isinstance(value, str) and value.strip() == ''):
                    continue

                field_data_type = field_data_types.get((obj, field), "")
                from utils.rich_text_handler import is_rich_text_field
                is_rich = field_data_type and isinstance(value, str) and value.strip() and is_rich_text_field(field_data_type, value)

                if is_rich:
                    to_store = value
                else:
                    to_store = DataTransformationService.migrate_coerce_value_for_salesforce(value, field_data_type)

                # Add to duplicate check fields if needed
                if is_duplicate_check:
                    duplicate_check_fields.setdefault(obj, {})
                    duplicate_check_fields[obj][field] = to_store

                # Add to object records for migration
                object_records.setdefault(obj, {})
                object_records[obj][field] = to_store

                # Handle lookup relationships (for both rich text and regular fields)
                lookup_parts = parse_lookup_of_reference(mapping_item.get("lookup_of"))
                if lookup_parts:
                    child_obj, child_field = lookup_parts
                    lookup_references.append({
                        "raw_value": value,
                        "lookup_object": obj,
                        "lookup_field": field,
                        "inject_into": child_obj,
                        "inject_field": child_field
                    })
        else:
            # Simple mapping (one-to-one)
            if not isinstance(mapping_info, dict):
                print(f"⚠️ Skipping {source_field} - invalid mapping_info type: {type(mapping_info)}")
                continue
            
            obj = mapping_info.get("object")
            field = mapping_info.get("field")
            if not obj or not field:
                print(f"⚠️ Skipping {source_field} - missing object or field")
                continue
            
            value = record.get(source_field)
            dest_field = mapping_info.get("field") or mapping_info.get("column")
            if value is None and dest_field and dest_field != source_field:
                value = record.get(dest_field)
            is_duplicate_check = mapping_info.get("is_duplicate_check", False)

            # Ensure we're working with the actual field value, not the mapping object
            # Skip None or empty strings (empty strings should not be sent to Salesforce)
            if value is None or (isinstance(value, str) and value.strip() == ''):
                continue

            # Check if this is a rich text field - if so, defer processing until after record creation
            field_data_type = field_data_types.get((obj, field), "")
            from utils.rich_text_handler import is_rich_text_field
            is_rich = field_data_type and isinstance(value, str) and value.strip() and is_rich_text_field(field_data_type, value)

            if is_rich:
                to_store = value
            else:
                # JSON numbers for currency/double/int/etc. (not strings like "3.5E8")
                to_store = DataTransformationService.migrate_coerce_value_for_salesforce(value, field_data_type)

            # Add to duplicate check fields if needed
            if is_duplicate_check:
                duplicate_check_fields.setdefault(obj, {})
                duplicate_check_fields[obj][field] = to_store

            # Add to object records for migration
            object_records.setdefault(obj, {})
            object_records[obj][field] = to_store
            
            # Handle lookup relationships (for both rich text and regular fields)
            lookup_parts = parse_lookup_of_reference(mapping_info.get("lookup_of"))
            if lookup_parts:
                child_obj, child_field = lookup_parts
                lookup_references.append({
                    "raw_value": value,
                    "lookup_object": obj,
                    "lookup_field": field,
                    "inject_into": child_obj,
                    "inject_field": child_field
                })

    # Step 2: Group and resolve lookups
    # Group lookup_references by (lookup_object, inject_into, inject_field)
    # This ensures split fields (e.g., FirstName, LastName) for the same lookup record
    # are resolved together in a single query
    lookup_groups = {}
    for ref in lookup_references:
        group_key = (ref["lookup_object"], ref["inject_into"], ref["inject_field"])
        if group_key not in lookup_groups:
            lookup_groups[group_key] = {
                "lookup_object": ref["lookup_object"],
                "inject_into": ref["inject_into"],
                "inject_field": ref["inject_field"],
                "query_fields": {}
            }
        # Collect all field-value pairs for this lookup group
        lookup_groups[group_key]["query_fields"][ref["lookup_field"]] = ref["raw_value"]
    
    # Resolve each lookup group with combined query fields
    for group_key, group_data in lookup_groups.items():
        lookup_id = await resolve_lookup_reference(
            access_token, instance_url,
            group_data["lookup_object"],
            group_data["query_fields"]
        )
        if lookup_id:
            object_records.setdefault(group_data["inject_into"], {})
            object_records[group_data["inject_into"]][group_data["inject_field"]] = lookup_id
            
            # ⚠️ DO NOT add lookup ID to duplicate_check_fields
            # Reason: Existing records might not have the lookup relationship yet
            # Example: Contact exists with Email/LastName but doesn't have AccountId set
            # If we include AccountId in duplicate check, we won't find the existing Contact
            # The lookup ID should only be in object_records for migration, not for duplicate checking

    # Step 3: Two-pass migration for rich text images
    # Pass 1: Create/update records WITHOUT rich text fields to get record IDs
    # Pass 2: Upload images with FirstPublishLocationId, then update records with rich text HTML
    
    # Separate rich text fields from other fields
    rich_text_fields_to_process = {}  # {obj: {field: value}}
    regular_fields = {}  # {obj: {field: value}}
    
    for obj, fields in object_records.items():
        regular_fields[obj] = {}
        rich_text_fields_to_process[obj] = {}
        
        for field, value in fields.items():
            field_data_type = field_data_types.get((obj, field), "")
            # Check if this is a rich text field that needs image processing
            from utils.rich_text_handler import is_rich_text_field
            is_rich = field_data_type and isinstance(value, str) and value.strip() and is_rich_text_field(field_data_type, value)
            
            if is_rich:
                # Store for second pass (after record creation)
                rich_text_fields_to_process[obj][field] = value
            else:
                # Regular field - include in first pass
                regular_fields[obj][field] = value
    
    # Pass 1: Create/update records (without rich text fields) to get record IDs
    results = {}
    record_ids = {}  # Store record IDs for image upload
    
    for obj, fields in regular_fields.items():
        try:
            query_fields = duplicate_check_fields.get(obj, {})
            
            if query_fields:
                cleaned_query_fields = {}
                for field_name, field_value in query_fields.items():
                    if field_value is None:
                        continue
                    if isinstance(field_value, str) and field_value.strip() == '':
                        continue
                    # RecordTypeId holds a raw record type name at this point (resolution
                    # happens later). Passing a name as an ID field value produces
                    # INVALID_QUERY_FILTER_OPERATOR in SOQL — skip it entirely.
                    if field_name == 'RecordTypeId':
                        continue
                    cleaned_query_fields[field_name] = field_value
                
                if cleaned_query_fields:
                    existing = await query_existing_salesforce_record(access_token, instance_url, obj, cleaned_query_fields)
                else:
                    existing = None
            else:
                existing = None

            if existing:
                record_id = existing["Id"]
                # On update: resolve RecordTypeId if already mapped (name→ID), but never inject a default
                # (RecordTypeId cannot be changed after creation via standard update in Salesforce)
                if "RecordTypeId" in fields:
                    rt_raw = fields["RecordTypeId"]
                    if record_type_map:
                        resolved_rt = resolve_record_type_id(obj, rt_raw, record_type_map)
                        if resolved_rt:
                            fields["RecordTypeId"] = resolved_rt
                        else:
                            del fields["RecordTypeId"]
                    else:
                        # Map empty — keep only if it's already a valid SF ID
                        rt_str = str(rt_raw).strip() if rt_raw else ""
                        if not (len(rt_str) in (15, 18) and rt_str.isalnum()):
                            print(f"⚠️ [RecordType] record_type_map empty, removing non-ID RecordTypeId '{rt_str}' from {obj} update payload")
                            del fields["RecordTypeId"]
                # Update with regular fields only (no rich text yet)
                await update_salesforce_record(access_token, instance_url, obj, record_id, fields)
                results[obj] = {"status": "updated", "id": record_id, "message": "Successfully updated in Salesforce"}
                record_ids[obj] = record_id
            else:
                # On create: resolve RecordTypeId if mapped, or inject default if object has record types
                if "RecordTypeId" in fields:
                    rt_raw = fields["RecordTypeId"]
                    # If the map is populated, try to resolve name → ID
                    if record_type_map:
                        resolved_rt = resolve_record_type_id(obj, rt_raw, record_type_map)
                        if resolved_rt:
                            fields["RecordTypeId"] = resolved_rt
                        else:
                            del fields["RecordTypeId"]  # No match and no default -- let Salesforce decide
                    else:
                        # Map is empty (fetch failed or no record types in DB).
                        # If the raw value is already a valid 15/18-char SF ID, keep it.
                        # Otherwise remove it to avoid MALFORMED_ID.
                        rt_str = str(rt_raw).strip() if rt_raw else ""
                        if not (len(rt_str) in (15, 18) and rt_str.isalnum()):
                            print(f"⚠️ [RecordType] record_type_map empty, removing non-ID RecordTypeId '{rt_str}' from {obj} payload")
                            del fields["RecordTypeId"]
                else:
                    # No RecordTypeId in payload -- inject default if object has record types (EC-1)
                    if record_type_map:
                        default_rt = resolve_record_type_id(obj, None, record_type_map)
                        if default_rt:
                            fields["RecordTypeId"] = default_rt
                created = await create_salesforce_record(access_token, instance_url, obj, fields)
                record_id = created.get("id")
                results[obj] = {"status": "created", "id": record_id, "message": "Successfully created in Salesforce"}
                record_ids[obj] = record_id
        except Exception as e:
            print(f"❌ Error while processing {obj}: {e}")
            results[obj] = {"status": "error", "message": str(e)}
    
    # Pass 2: Process rich text fields with record IDs for image upload
    for obj, fields in rich_text_fields_to_process.items():
        if not fields:
            continue
        
        record_id = record_ids.get(obj)
        if not record_id:
            print(f"⚠️ [RICH TEXT] No record ID for {obj}, skipping rich text processing")
            # Fallback: process without record ID (won't link files, but will still upload)
            continue
        
        try:
            # Process each rich text field with the record ID
            updated_fields = {}
            rich_text_image_ids = []  # Collect ContentDocument IDs for rollback
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
                            if processed.get("images_uploaded", 0) > 0:
                                print(f"   ✅ Uploaded {processed['images_uploaded']} images for {obj}.{field} (linked to record {record_id})")
                            # Collect rich text image IDs for rollback
                            rich_text_image_ids.extend(processed.get("rich_text_image_ids", []))
                        else:
                            updated_fields[field] = value  # Use original if processing didn't update
                    except Exception as e:
                        print(f"⚠️ [RICH TEXT] Error processing {obj}.{field}: {e}")
                        updated_fields[field] = value  # Use original on error
            
            # Update record with rich text fields
            if updated_fields:
                await update_salesforce_record(access_token, instance_url, obj, record_id, updated_fields)
                print(f"✅ [RICH TEXT] Updated {obj} record {record_id} with rich text fields")
            
            # Store rich text image IDs in results (for rollback)
            if rich_text_image_ids and obj in results:
                if "rich_text_images" not in results[obj]:
                    results[obj]["rich_text_images"] = []
                results[obj]["rich_text_images"].extend(rich_text_image_ids)
        except Exception as e:
            print(f"❌ Error updating rich text fields for {obj}: {e}")

    # Pass 3: Migrate file attachments if present
    if record.get("_attachments") and source_access_token and source_instance_url:
        attachments = record.get("_attachments", [])
        if attachments:
            print(f"📎 [ATTACHMENT MIGRATION] Processing {len(attachments)} attachments...")
            
            # Get the primary record ID (first object in results)
            primary_record_id = None
            primary_object = None
            for obj, result in results.items():
                if result.get("status") in ["created", "updated"] and result.get("id"):
                    primary_record_id = result.get("id")
                    primary_object = obj
                    break
            
            if not primary_record_id:
                print(f"⚠️ [ATTACHMENT MIGRATION] No record ID available, skipping attachment migration")
            else:
                from utils.salesforce_file_attachment import (
                    download_file_from_content_version,
                    upload_file_to_salesforce,
                    create_content_document_link,
                    check_duplicate_attachment_by_filename
                )
                
                attachment_results = []
                successful_uploads = []
                
                for attachment in attachments:
                    try:
                        content_version_id = attachment.get("content_version_id")
                        title = attachment.get("title", "Untitled")
                        file_extension = attachment.get("file_extension", "")
                        share_type = attachment.get("share_type", "V")
                        visibility = attachment.get("visibility", "AllUsers")
                        
                        # Construct filename with extension if available
                        filename = title
                        if file_extension:
                            # Only add extension if title doesn't already have it
                            if not title.lower().endswith(f".{file_extension.lower()}"):
                                filename = f"{title}.{file_extension}"
                        
                        if not content_version_id:
                            print(f"⚠️ [ATTACHMENT MIGRATION] Missing content_version_id for {title}, skipping")
                            attachment_results.append({
                                "title": title,
                                "status": "failed",
                                "error": "Missing content_version_id"
                            })
                            continue
                        
                        # Check for duplicate file before downloading/uploading
                        existing_content_document_id = await check_duplicate_attachment_by_filename(
                            record_id=primary_record_id,
                            filename=filename,
                            access_token=access_token,
                            instance_url=instance_url
                        )
                        
                        if existing_content_document_id:
                            # File already exists, try to link it (will succeed if not linked, or return error if already linked)
                            print(f"   ⏭️  [ATTACHMENT MIGRATION] File '{filename}' already exists, linking to record {primary_record_id}...")
                            
                            link_result = await create_content_document_link(
                                content_document_id=existing_content_document_id,
                                linked_entity_id=primary_record_id,
                                access_token=access_token,
                                instance_url=instance_url,
                                share_type=share_type,
                                visibility=visibility
                            )
                            
                            if link_result.get("success"):
                                print(f"   ✅ [ATTACHMENT MIGRATION] Linked existing file '{filename}' to record")
                                attachment_results.append({
                                    "title": title,
                                    "status": "linked_existing",
                                    "content_document_id": existing_content_document_id,
                                    "error": None
                                })
                                continue
                            else:
                                # Check if error indicates link already exists
                                error_msg = str(link_result.get("error", "")).lower()
                                if any(keyword in error_msg for keyword in ["duplicate", "already exists", "duplicate value"]):
                                    print(f"   ✅ [ATTACHMENT MIGRATION] Skipped duplicate file '{filename}' (already linked to record)")
                                    attachment_results.append({
                                        "title": title,
                                        "status": "skipped_duplicate",
                                        "content_document_id": existing_content_document_id,
                                        "error": None
                                    })
                                    continue
                                else:
                                    # Link failed for some other reason, proceed with upload
                                    print(f"   ⚠️ [ATTACHMENT MIGRATION] Duplicate file found but link failed ({link_result.get('error')}), proceeding with upload...")
                        
                        # Download file from source org
                        print(f"   📥 [ATTACHMENT MIGRATION] Downloading {title} from source org...")
                        file_bytes = await download_file_from_content_version(
                            content_version_id=content_version_id,
                            access_token=source_access_token,
                            instance_url=source_instance_url
                        )
                        
                        if not file_bytes:
                            print(f"   ❌ [ATTACHMENT MIGRATION] Failed to download {title}")
                            attachment_results.append({
                                "title": title,
                                "status": "failed",
                                "error": "Download failed"
                            })
                            continue
                        
                        # Validate file size against plan limits
                        file_size_mb = len(file_bytes) / (1024 * 1024)
                        if user_id:
                            is_valid, message, plan_info = await get_entitlements().validate_file_attachment_upload(
                                user_id=user_id,
                                file_size_mb=file_size_mb,
                                file_count=1
                            )
                            if not is_valid:
                                print(f"   ⚠️ [ATTACHMENT MIGRATION] {title}: {message}")
                                attachment_results.append({
                                    "title": title,
                                    "status": "failed",
                                    "error": message
                                })
                                continue
                        
                        # Upload file to destination org
                        print(f"   📤 [ATTACHMENT MIGRATION] Uploading {filename} to destination org...")
                        upload_result = await upload_file_to_salesforce(
                            file_bytes=file_bytes,
                            filename=filename,
                            access_token=access_token,
                            instance_url=instance_url,
                            title=title,
                            first_publish_location_id=None  # Don't link via FirstPublishLocationId, use ContentDocumentLink instead
                        )
                        
                        if not upload_result.get("success"):
                            print(f"   ❌ [ATTACHMENT MIGRATION] Failed to upload {title}: {upload_result.get('error')}")
                            attachment_results.append({
                                "title": title,
                                "status": "failed",
                                "error": upload_result.get("error", "Upload failed")
                            })
                            continue
                        
                        content_document_id = upload_result.get("content_document_id")
                        if not content_document_id:
                            print(f"   ⚠️ [ATTACHMENT MIGRATION] Upload succeeded but no ContentDocumentId for {title}")
                            attachment_results.append({
                                "title": title,
                                "status": "failed",
                                "error": "Missing ContentDocumentId after upload"
                            })
                            continue
                        
                        # Create ContentDocumentLink to link file to record
                        print(f"   🔗 [ATTACHMENT MIGRATION] Linking {title} to record {primary_record_id}...")
                        link_result = await create_content_document_link(
                            content_document_id=content_document_id,
                            linked_entity_id=primary_record_id,
                            access_token=access_token,
                            instance_url=instance_url,
                            share_type=share_type,
                            visibility=visibility
                        )
                        
                        if link_result.get("success"):
                            print(f"   ✅ [ATTACHMENT MIGRATION] Successfully migrated {title}")
                            attachment_results.append({
                                "title": title,
                                "status": "linked",
                                "content_document_id": content_document_id,
                                "error": None
                            })
                            successful_uploads.append({
                                "file_bytes": file_bytes,
                                "title": title
                            })
                        else:
                            print(f"   ⚠️ [ATTACHMENT MIGRATION] File uploaded but link failed for {title}: {link_result.get('error')}")
                            attachment_results.append({
                                "title": title,
                                "status": "uploaded_not_linked",
                                "content_document_id": content_document_id,
                                "error": link_result.get("error", "Link creation failed")
                            })
                            # Still count as successful upload for tracking
                            successful_uploads.append({
                                "file_bytes": file_bytes,
                                "title": title
                            })
                    
                    except Exception as e:
                        print(f"   ❌ [ATTACHMENT MIGRATION] Exception processing {attachment.get('title', 'unknown')}: {e}")
                        import traceback
                        traceback.print_exc()
                        attachment_results.append({
                            "title": attachment.get("title", "unknown"),
                            "status": "failed",
                            "error": str(e)
                        })
                
                # Track successful file uploads
                if user_id and successful_uploads:
                    total_size_mb = sum(
                        len(upload.get("file_bytes", b"")) / (1024 * 1024)
                        for upload in successful_uploads
                        if upload.get("file_bytes")
                    )
                    upload_count = len(successful_uploads)
                    
                    await get_entitlements().track_file_attachment_upload(
                        user_id=user_id,
                        file_count=upload_count,
                        total_size_mb=total_size_mb
                    )
                    
                    print(f"   📊 [ATTACHMENT MIGRATION] Tracked {upload_count} file uploads ({total_size_mb:.2f} MB)")
                
                # Store attachment results in primary object's result
                if primary_object and primary_object in results:
                    if "attachments" not in results[primary_object]:
                        results[primary_object]["attachments"] = []
                    results[primary_object]["attachments"] = attachment_results

    return results


async def migrate_data_to_salesforce(
    data,
    mapping,
    document_type,
    access_token,
    instance_url,
    user_id: str = None,
    batch_id: str = None,
    org_id: Optional[str] = None,
    connected_app_id: Optional[int] = None,
    destination_name: Optional[str] = None,
    use_bulk_api: Optional[bool] = None,
    bulk_api_threshold: int = 50,
    source_access_token: Optional[str] = None,
    source_instance_url: Optional[str] = None,
    migration_job_id: Optional[str] = None,
    job_store=None,
):
    """
    Migrate data to Salesforce with database tracking.
    
    Automatically uses Bulk API 2.0 for high-volume migrations (>= bulk_api_threshold records)
    and falls back to REST API for smaller batches.
    
    Args:
        data: List of records to migrate
        mapping: Field mapping configuration
        document_type: Type of document being migrated
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        user_id: User ID for tracking migrations
        use_bulk_api: Force use of Bulk API (True) or REST API (False). If None, auto-detect.
        bulk_api_threshold: Minimum number of records to use Bulk API (default: 50)
    """
    # Auto-detect whether to use Bulk API
    if use_bulk_api is None:
        use_bulk_api = len(data) >= bulk_api_threshold

    if use_bulk_api:
        print(f"🚀 [BULK API 2.0] Using Bulk API 2.0 for {len(data)} records (threshold: {bulk_api_threshold})")
        print(f"📊 [BULK API 2.0] Record count: {len(data)}, Threshold: {bulk_api_threshold}, Using Bulk API: {use_bulk_api}")
        return await _migrate_with_bulk_api(
            data, mapping, document_type, access_token, instance_url,
            user_id, batch_id, org_id, connected_app_id, destination_name,
            source_access_token, source_instance_url,
            migration_job_id, job_store
        )
    else:
        print(f"📝 [REST API] Using REST API for {len(data)} records (below threshold: {bulk_api_threshold})")
        return await _migrate_with_rest_api(
            data, mapping, document_type, access_token, instance_url,
            user_id, batch_id, org_id, connected_app_id, destination_name,
            source_access_token, source_instance_url
        )


async def _migrate_with_rest_api(
    data,
    mapping,
    document_type,
    access_token,
    instance_url,
    user_id: str = None,
    batch_id: str = None,
    org_id: Optional[str] = None,
    connected_app_id: Optional[int] = None,
    destination_name: Optional[str] = None,
    source_access_token: Optional[str] = None,
    source_instance_url: Optional[str] = None,
):
    """
    Migrate data using REST API (original implementation).
    """
    if not user_id:
        print("⚠️ No user_id provided, skipping database tracking")
        return await _migrate_without_tracking(data, mapping, access_token, instance_url)
    
    # Use provided batch_id or create new one if not provided
    if not batch_id:
        batch_name = f"Migration_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        # Simplify mapping_config to avoid database issues
        simplified_mapping = {
            "document_type": document_type,
            "field_count": len(mapping),
            "field_names": list(mapping.keys()),
            "objects_involved": list(set([m.get("object", "Unknown") for m in mapping.values() if isinstance(m, dict)]))
        }
        if destination_name:
            simplified_mapping["destination_name"] = destination_name
        if org_id:
            simplified_mapping["org_id"] = org_id
        if connected_app_id is not None:
            simplified_mapping["connected_app_id"] = connected_app_id
        
        try:
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=len(data),
                mapping_config=simplified_mapping,
                destination_type="salesforce",
            )
        except Exception as e:
            print(f"❌ Failed to create migration batch: {e}")
            raise
    
    all_results = []
    successful_count = 0
    failed_count = 0
    token_refreshed = False  # Only force-refresh once per migration job

    def _is_401(text: str) -> bool:
        return any(k in text for k in ("401", "Unauthorized", "INVALID_SESSION_ID", "Session expired"))

    # Fetch field data types once for all records (optimization)
    field_data_types = {}
    if user_id:
        field_data_types = await fetch_field_data_types(mapping, user_id, org_id)

    # Pre-fetch record type name→ID map for all destination objects (EC-6: avoid N+1 per record)
    record_type_map = {}
    if user_id:
        record_type_map = await fetch_record_type_map(mapping, user_id, org_id)
    
    try:
        for idx, record in enumerate(data):
            record_id = None
            for record_attempt in range(2):  # attempt 0 = normal, attempt 1 = after token refresh
                try:
                    if not record_id:
                        record_id = await MigrationRecordsService.create_migration_record(
                            batch_id=batch_id,
                            user_id=user_id,
                            record_index=idx,
                            source_data=record,
                            mapped_data=record
                        )

                    result = await migrate_single_record_to_salesforce(
                        record,
                        mapping,
                        access_token,
                        instance_url,
                        user_id=user_id,
                        org_id=org_id,
                        field_data_types=field_data_types,
                        record_type_map=record_type_map,
                        source_access_token=source_access_token,
                        source_instance_url=source_instance_url
                    )

                    overall_status = "success"
                    error_message = None
                    for obj_result in result.values():
                        if isinstance(obj_result, dict) and obj_result.get("status") == "error":
                            overall_status = "failed"
                            error_message = obj_result.get("message", "Unknown error")
                            # Retry on 401 once
                            if _is_401(error_message) and record_attempt == 0 and not token_refreshed:
                                try:
                                    ti = await get_valid_salesforce_token(
                                        user_id=user_id, org_id=org_id,
                                        connected_app_id=connected_app_id, force_refresh=True,
                                    )
                                    access_token = ti["access_token"]
                                    token_refreshed = True
                                    print(f"⚠️ [MIGRATION-REST] 401 on record {idx + 1} — token refreshed, retrying")
                                except Exception:
                                    pass
                            break

                    # If we refreshed the token due to a 401, retry the record
                    if overall_status == "failed" and _is_401(error_message or "") and record_attempt == 0 and token_refreshed:
                        continue

                    await MigrationRecordsService.update_migration_record_result(
                        record_id=record_id,
                        salesforce_results=result,
                        status=overall_status,
                        error_message=error_message
                    )

                    if overall_status == "success":
                        successful_count += 1
                    else:
                        failed_count += 1

                    all_results.append(result)
                    break

                except Exception as e:
                    err_text = str(e)
                    # On 401, refresh token once and retry the record
                    if _is_401(err_text) and record_attempt == 0 and not token_refreshed:
                        try:
                            ti = await get_valid_salesforce_token(
                                user_id=user_id, org_id=org_id,
                                connected_app_id=connected_app_id, force_refresh=True,
                            )
                            access_token = ti["access_token"]
                            token_refreshed = True
                            print(f"⚠️ [MIGRATION-REST] 401 on record {idx + 1} — token refreshed, retrying")
                            continue
                        except Exception:
                            pass

                    print(f"❌ Error in record {idx + 1}: {e}")
                    failed_count += 1
                    if record_id:
                        await MigrationRecordsService.update_migration_record_result(
                            record_id=record_id,
                            salesforce_results={"error": err_text},
                            status="failed",
                            error_message=err_text
                        )
                    all_results.append({
                        "status": "error",
                        "record_index": idx,
                        "message": err_text
                    })
                    break
        
        print(f"✅ Migration completed. Success: {successful_count}, Failed: {failed_count}")
        
        # Update batch status
        await MigrationRecordsService.update_batch_status(
            batch_id=batch_id,
            status="completed",
            successful_count=successful_count,
            failed_count=failed_count,
            total_records=len(data)
        )
        
    except Exception as e:
        print(f"❌ Migration batch failed: {e}")
        await MigrationRecordsService.update_batch_status(
            batch_id=batch_id,
            status="failed",
            error_summary=str(e)
        )
        raise

    # Count rich text images uploaded (for credit deduction)
    rich_text_images_uploaded = 0
    for r in all_results:
        if isinstance(r, dict) and r.get("status") != "error":
            for obj_data in r.values():
                if isinstance(obj_data, dict):
                    rich_text_images_uploaded += len(obj_data.get("rich_text_images", []))

    # Count file attachments uploaded (for credit deduction)
    _ATTACHMENT_SUCCESS_STATUSES = ("linked", "uploaded_not_linked", "linked_existing")
    attachments_uploaded = 0
    for r in all_results:
        if isinstance(r, dict) and r.get("status") != "error":
            for obj_data in r.values():
                if isinstance(obj_data, dict):
                    for att in obj_data.get("attachments", []):
                        if isinstance(att, dict) and att.get("status") in _ATTACHMENT_SUCCESS_STATUSES:
                            attachments_uploaded += 1

    return {
        "batch_id": batch_id,
        "results": all_results,
        "summary": {
            "total": len(data),
            "successful": successful_count,
            "failed": failed_count,
            "rich_text_images_uploaded": rich_text_images_uploaded,
            "attachments_uploaded": attachments_uploaded,
        }
    }


async def _migrate_without_tracking(data, mapping, access_token, instance_url):
    """Fallback migration without database tracking"""
    all_results = []
    
    for idx, record in enumerate(data):
        try:
            # Note: Without user_id/org_id, rich text image processing won't work
            result = await migrate_single_record_to_salesforce(
                record, 
                mapping, 
                access_token, 
                instance_url,
                user_id=None,
                org_id=None
            )
            all_results.append(result)
        except Exception as e:
            print(f"❌ Error in record {idx + 1}: {e}")
            all_results.append({
                "status": "error",
                "record_index": idx,
                "message": str(e)
            })
    
    return all_results


async def _migrate_with_bulk_api(
    data,
    mapping,
    document_type,
    access_token,
    instance_url,
    user_id: str = None,
    batch_id: str = None,
    org_id: Optional[str] = None,
    connected_app_id: Optional[int] = None,
    destination_name: Optional[str] = None,
    source_access_token: Optional[str] = None,
    source_instance_url: Optional[str] = None,
    migration_job_id: Optional[str] = None,
    job_store=None,
):
    """
    Migrate data using Bulk API 2.0 with database tracking.
    """
    if not user_id:
        print("⚠️ No user_id provided, skipping database tracking")
        return await migrate_data_with_bulk_api(
            data,
            mapping,
            access_token,
            instance_url,
            user_id=None,
            batch_id=None,
            org_id=org_id,
            migration_job_id=migration_job_id,
            job_store=job_store,
        )
    
    # Use provided batch_id or create new one if not provided
    if not batch_id:
        batch_name = f"Migration_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        # Simplify mapping_config to avoid database issues
        simplified_mapping = {
            "document_type": document_type,
            "field_count": len(mapping),
            "field_names": list(mapping.keys()),
            "objects_involved": list(set([m.get("object", "Unknown") for m in mapping.values() if isinstance(m, dict)]))
        }
        if destination_name:
            simplified_mapping["destination_name"] = destination_name
        if org_id:
            simplified_mapping["org_id"] = org_id
        if connected_app_id is not None:
            simplified_mapping["connected_app_id"] = connected_app_id
        
        try:
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=len(data),
                mapping_config=simplified_mapping,
                destination_type="salesforce",
            )
        except Exception as e:
            print(f"❌ Failed to create migration batch: {e}")
            raise
    
    def _is_401_bulk(text: str) -> bool:
        return any(k in text for k in ("401", "Unauthorized", "INVALID_SESSION_ID", "Session expired"))

    try:
        # Execute bulk migration — retry once with a refreshed token on 401
        bulk_token_refreshed = False
        for bulk_attempt in range(2):
            try:
                bulk_result = await migrate_data_with_bulk_api(
                    data,
                    mapping,
                    access_token,
                    instance_url,
                    user_id=user_id,
                    batch_id=batch_id,
                    org_id=org_id,
                    source_access_token=source_access_token,
                    source_instance_url=source_instance_url,
                    migration_job_id=migration_job_id,
                    job_store=job_store,
                )
                break
            except Exception as bulk_exc:
                from utils.migration_cancel import MigrationCancelledError
                if isinstance(bulk_exc, MigrationCancelledError):
                    raise
                err_text = str(bulk_exc)
                if _is_401_bulk(err_text) and bulk_attempt == 0 and not bulk_token_refreshed:
                    try:
                        ti = await get_valid_salesforce_token(
                            user_id=user_id, org_id=org_id,
                            connected_app_id=connected_app_id, force_refresh=True,
                        )
                        access_token = ti["access_token"]
                        bulk_token_refreshed = True
                        print(f"⚠️ [MIGRATION-BULK] 401 Unauthorized — token refreshed, retrying bulk job")
                        continue
                    except Exception:
                        pass
                raise
        
        # Process results for database tracking
        # Note: Bulk API processes records in batches, so we need to map results back
        # For now, we'll create a summary. Detailed per-record tracking can be added later.
        summary = bulk_result.get("summary", {})
        # Bulk API returns "total_successful" and "total_failed", but background_migration_service expects "successful" and "failed"
        successful_count = summary.get("total_successful", 0) or summary.get("successful", 0)
        failed_count = summary.get("total_failed", 0) or summary.get("failed", 0)
        
        # Update batch status
        await MigrationRecordsService.update_batch_status(
            batch_id=batch_id,
            status="completed",
            successful_count=successful_count,
            failed_count=failed_count
        )
        
        print(f"✅ [BULK API 2.0] Bulk migration completed. Success: {successful_count}, Failed: {failed_count}")
        print(f"📊 [BULK API 2.0] Summary: {len(data)} total records, {successful_count} successful, {failed_count} failed")
        
        # Create individual migration records from bulk results (post-processing)
        try:
            from services.salesforce_bulk_migration_flow import create_migration_records_from_bulk_results
            bulk_record_index_summary = await create_migration_records_from_bulk_results(
                bulk_result=bulk_result,
                original_data=data,
                mapping=mapping,
                batch_id=batch_id,
                user_id=user_id,
                lookup_mapping=bulk_result.get("lookup_mapping", {}),
                attachment_results_by_record=bulk_result.get("attachment_results_by_record", {}),
                rich_text_image_ids_by_record=bulk_result.get("rich_text_image_ids_by_record", {})
            )
        except Exception as e:
            print(f"⚠️ [BULK API 2.0] Failed to create migration records: {e}")
            # Don't fail the entire migration if record creation fails
            bulk_record_index_summary = {}
        
        return {
            "batch_id": batch_id,
            "results": bulk_result.get("results", {}),
            "summary": bulk_result.get("summary", {}),
            "method": "bulk_api",
            "successful_record_indices": bulk_record_index_summary.get("successful_record_indices", []),
            "failed_record_indices": bulk_record_index_summary.get("failed_record_indices", []),
        }
    
    except Exception as e:
        from utils.migration_cancel import MigrationCancelledError
        if isinstance(e, MigrationCancelledError):
            raise  # Let background_migration_service set status="cancelled"
        print(f"❌ Bulk migration batch failed: {e}")
        await MigrationRecordsService.update_batch_status(
            batch_id=batch_id,
            status="failed",
            error_summary=str(e)
        )
        raise
