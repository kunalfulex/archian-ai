from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from uuid import uuid4
from datetime import datetime, timezone
from db.connection import database
from routes.auth import get_current_user
from uuid import UUID
from typing import Optional, List, Any, Set, Dict, Tuple
import json
import logging
import re
import pytz
from utils.soql_parse import (
    split_outer_select_clause,
    sync_select_query_with_field_items,
    sync_select_query_with_source_fields,
    validate_select_query_for_mapping,
    validate_soql_from_clause_for_mapping,
)
from utils.soql_salesforce_field_validation import build_salesforce_mapping_select_items
from services.incremental_sync_service import IncrementalSyncService
from services.bidirectional_flow_service import (
    create_reverse_leg_flow,
    prepare_delete_flow_bidirectional,
)

router = APIRouter()
logger = logging.getLogger(__name__)

class SaveMappingFlowRequest(BaseModel):
    flow_name: str
    document_type: str
    file_key: str | None = None  # Optional for SQL flows
    openai_file_id: str | None = None  # Optional for SQL flows
    extracted_schema: dict
    all_records: list[dict]
    mapping: Dict[str, Any]  # extracted_field -> dict (1:1) or list (split fields)
    user_prompt: str  # New field for storing user prompt
    # SQL-specific fields
    extracted_schema_type: str = "file"  # "file" or "sql"
    connector_id: str | None = None  # For SQL flows
    sql_query: str | None = None  # For SQL flows
    table_name: str | None = None  # For SQL flows
    destination: str | None = None
    destination_name: str | None = None
    destination_id: str | None = None
    migration_rules: dict | None = None  # {
    #   missing_field_preference: {option: "placeholder"|"skip", placeholderValue: "string"},
    #   mapping_transformation: {fields: [...]}  # Mapping-stage splits/combines (not extraction_rules.transformation_rules)
    #   extraction_rules: {  # Extraction stage rules (applied during extraction)
    #     filter_rules: {...},
    #     transformation_rules: {...},  # Extraction transformation rules
    #     deduplication_rules: {...}
    #   }
    # }

# Google Sheets Models
class GoogleSheetsConnection(BaseModel):
    id: str
    user_id: str
    connection_name: str
    google_account_email: str | None = None
    google_account_name: str | None = None
    access_token: str | None = None  # OAuth access token for Google Sheets API
    is_active: bool
    created_at: datetime
    updated_at: datetime

class GoogleSheetsConnectionResponse(BaseModel):
    connected: bool
    connection: GoogleSheetsConnection | None = None
    message: str | None = None

@router.post("/save-mapping-flow")
async def save_mapping_flow(
    payload: SaveMappingFlowRequest,
    user_id: UUID = Depends(get_current_user)
):
    flow_id = str(uuid4())

    # Fetch org_id from salesforce_tokens (by destination_name or destination_id as token_id/org_id)
    destination_type = (payload.destination or "salesforce").lower()
    org_id = None

    if destination_type == "salesforce":
        if not payload.destination_id and not (payload.destination_name and str(payload.destination_name or "").strip() and str(payload.destination_name or "").strip() != "unknown"):
            raise HTTPException(
                status_code=400,
                detail="Salesforce destination (org) is required for Salesforce flows. Provide destination_id (token id) or destination_name.",
            )

        org_result = None
        dest_id = str(payload.destination_id).strip() if payload.destination_id else None

        # 1) Prefer destination_name → org_display_name
        if payload.destination_name and str(payload.destination_name).strip() and str(payload.destination_name).strip() != "unknown":
            org_result = await database.fetch_one(
                """
                SELECT org_id, id AS token_id
                FROM salesforce_tokens
                WHERE user_id = :user_id
                  AND LOWER(TRIM(org_display_name)) = LOWER(TRIM(:destination_name))
                ORDER BY is_default DESC NULLS LAST, issued_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id), "destination_name": payload.destination_name},
            )

        # 2) Fallback: resolve by destination_id as token_id (UUID)
        if not org_result and dest_id:
            try:
                token_uuid = UUID(dest_id)
                org_result = await database.fetch_one(
                    """
                    SELECT org_id, id AS token_id
                    FROM salesforce_tokens
                    WHERE user_id = :user_id AND id = :token_id
                    LIMIT 1
                    """,
                    {"user_id": str(user_id), "token_id": str(token_uuid)},
                )
            except (TypeError, ValueError):
                org_result = None

        # 3) Last resort: default token
        if not org_result:
            org_result = await database.fetch_one(
                """
                SELECT org_id, id AS token_id
                FROM salesforce_tokens
                WHERE user_id = :user_id
                ORDER BY is_default DESC NULLS LAST, issued_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id)},
            )

        if not org_result or not org_result["org_id"]:
            raise HTTPException(
                status_code=400,
                detail="Salesforce org not found. Please select a valid Salesforce connection.",
            )

        org_result_dict = dict(org_result)
        org_id = org_result_dict["org_id"]
    elif destination_type == "google_sheets":
        if not payload.destination_id:
            raise HTTPException(status_code=400, detail="Spreadsheet ID required for Google Sheets flows")
        org_id = payload.destination_id
        print(f"🗂️ Saving Google Sheets flow -> spreadsheet_id: {payload.destination_id}")
    elif destination_type == "sql":
        if not payload.destination_id:
            raise HTTPException(status_code=400, detail="Database connector required for SQL flows")
        org_id = payload.destination_id

    # 1. Prepare migration_rules with source_schema and metadata (for file flows)
    migration_rules = payload.migration_rules or {}
    
    # Add source_schema when not already in extraction_rules (all source types)
    extraction_rules = migration_rules.get("extraction_rules")
    has_schema = bool(migration_rules.get("source_schema")) or (
        isinstance(extraction_rules, dict) and extraction_rules.get("source_schema")
    )
    if not has_schema and payload.all_records and len(payload.all_records) > 0:
        from utils.rule_accumulation import extract_source_schema_from_data

        source_schema = extract_source_schema_from_data(payload.all_records)
        if source_schema:
            if payload.extracted_schema_type == "file":
                migration_rules["source_schema"] = source_schema
            elif isinstance(extraction_rules, dict):
                extraction_rules.setdefault("source_schema", source_schema)
                migration_rules["extraction_rules"] = extraction_rules
            else:
                migration_rules["extraction_rules"] = {"source_schema": source_schema}
            print(f"📋 [SAVE FLOW] Added source_schema to migration_rules: {source_schema}")
    
    # Add metadata to transformation rules if extraction_rules exist
    extraction_rules = migration_rules.get("extraction_rules")
    if extraction_rules and isinstance(extraction_rules, dict):
        transformation_rules = _normalize_extraction_transformation_rules(
            extraction_rules.get("transformation_rules")
        )
        if transformation_rules is None:
            tr_raw = extraction_rules.get("transformation_rules")
            transformation_rules = tr_raw if isinstance(tr_raw, dict) else None
        if transformation_rules and isinstance(transformation_rules, dict):
            migration_rules["extraction_rules"]["transformation_rules"] = transformation_rules
            from utils.rule_accumulation import add_metadata_to_transformation_rules
            # Only add metadata if rules don't already have it (preserve existing metadata)
            has_metadata = False
            fields = transformation_rules.get("fields", [])
            if fields:
                first_rule = fields[0] if fields else {}
                has_metadata = all(key in first_rule for key in ["rule_id", "timestamp", "operation_type", "order"])
            
            if not has_metadata:
                migration_rules["extraction_rules"]["transformation_rules"] = add_metadata_to_transformation_rules(
                    transformation_rules,
                    operation_type="extraction"
                )
                print(f"📋 [SAVE FLOW] Added metadata to transformation rules")
    
    from utils.migration_rules_utils import normalize_migration_rules_for_save

    migration_rules = normalize_migration_rules_for_save(migration_rules) or {}
    migration_rules_json = json.dumps(migration_rules) if migration_rules else None
    
    await database.execute(
        """
        INSERT INTO migration_flows (id, user_id, org_id, migration_name, destination_type, migration_rules, created_at, updated_at)
        VALUES (:id, :user_id, :org_id, :name, :destination_type, :migration_rules, :created_at, :updated_at)
        """,
        {
            "id": flow_id,
            "user_id": str(user_id),
            "org_id": org_id,
            "name": payload.flow_name,
            "destination_type": destination_type,
            "migration_rules": migration_rules_json,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
    )

    # 2. Insert extracted_schema (for record 1)
    schema_id = str(uuid4())
    
    # For Google Sheets, resolve connector_id from spreadsheet_id if needed
    # The connector_id column in extracted_schemas expects a UUID (google_sheets_connection_id)
    # but the frontend might send a spreadsheet_id (string) instead
    resolved_connector_id = payload.connector_id
    resolved_table_name = payload.table_name  # Default to payload value
    
    if payload.extracted_schema_type == 'gsheets' and payload.connector_id:
        try:
            # Check if connector_id is a valid UUID
            UUID(payload.connector_id)
            print(f"🗂️ Valid UUID: {UUID(payload.connector_id)}")
            # If it's already a UUID, use it as-is (it's already the connection ID)
            resolved_connector_id = payload.connector_id
            # Tab name (sheet_name) is stored in table_name — aligns with SQL table / SF object
            if not resolved_table_name:
                spreadsheet_record = await database.fetch_one(
                    """
                    SELECT spreadsheet_name, sheet_name
                    FROM user_google_spreadsheets
                    WHERE user_id = :user_id AND id = :connection_id
                    ORDER BY last_accessed_at DESC
                    LIMIT 1
                    """,
                    {"user_id": str(user_id), "connection_id": payload.connector_id}
                )
                if spreadsheet_record:
                    resolved_table_name = (
                        spreadsheet_record["sheet_name"] or spreadsheet_record["spreadsheet_name"]
                    )
        except (ValueError, TypeError):
            # Not a UUID: treat as spreadsheet_id and resolve UGS row id
            spreadsheet_record = await database.fetch_one(
                """
                SELECT id, google_sheets_connection_id, spreadsheet_name, sheet_name
                FROM user_google_spreadsheets
                WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
                ORDER BY last_accessed_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id), "spreadsheet_id": payload.connector_id}
            )

            # Database records are accessed using bracket notation, not .get()
            if spreadsheet_record:
                connection_id = spreadsheet_record["id"]
                if connection_id:
                    resolved_connector_id = str(connection_id)
                    if not resolved_table_name:
                        resolved_table_name = (
                            spreadsheet_record["sheet_name"] or spreadsheet_record["spreadsheet_name"]
                        )
                else:
                    # If lookup fails, raise an error
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Google Sheets connection not found for spreadsheet_id: {payload.connector_id}"
                    )
            else:
                # If no record found, raise an error
                raise HTTPException(
                    status_code=400, 
                    detail=f"Google Sheets connection not found for spreadsheet_id: {payload.connector_id}"
                )
    
    await database.execute(
        """
        INSERT INTO extracted_schemas (
          id, user_id, document_type, file_key, openai_file_id, extracted_schema, user_prompt, approved_by_user, destination_type, created_at, migration_flow_id,
          extracted_schema_type, connector_id, sql_query, table_name
        )
        VALUES (:id, :user_id, :doc_type, :file_key, :file_id, :schema, :user_prompt, true, :destination_type, :created_at, :flow_id,
                :extracted_schema_type, :connector_id, :sql_query, :table_name)
        """,
        {
            "id": schema_id,
            "user_id": str(user_id),
            "doc_type": payload.document_type,
            "file_key": payload.file_key,
            "file_id": payload.openai_file_id,
            "schema": json.dumps(payload.extracted_schema),
            "user_prompt": payload.user_prompt,
            "destination_type": destination_type,
            "created_at": datetime.utcnow(),
            "flow_id": flow_id,
            "extracted_schema_type": payload.extracted_schema_type,
            "connector_id": resolved_connector_id,
            "sql_query": payload.sql_query,
            "table_name": resolved_table_name
        }
    )

    # 3. Insert field mappings
    for field_name, config in payload.mapping.items():
        # Check if this is an array mapping (split field)
        # Handle both actual arrays (from frontend) and dicts with numeric keys (legacy format)
        is_array_mapping = isinstance(config, list) or (
            isinstance(config, dict) and any(
                key.isdigit() for key in config.keys() 
                if isinstance(key, str) and key not in ["is_required"]
            )
        )
        
        if is_array_mapping:
            # Handle array mapping (split field) - save each element separately
            if isinstance(config, list):
                # Actual array from frontend: [{"object": "Contact", "field": "FirstName"}, ...]
                mapping_items = config
            else:
                # Legacy dict format with numeric keys: {"0": {...}, "1": {...}}
                sorted_keys = sorted(
                    [k for k in config.keys() if isinstance(k, str) and k.isdigit()],
                    key=lambda x: int(x)
                )
                mapping_items = [config[key] for key in sorted_keys]
            
            for mapping_item in mapping_items:
                # Extract mapping details from array element
                if destination_type == "salesforce":
                    object_value = mapping_item.get("object")
                    field_value = mapping_item.get("field")
                else:
                    object_value = mapping_item.get("table") or mapping_item.get("sheet") or mapping_item.get("object")
                    field_value = mapping_item.get("column") or mapping_item.get("field")
                
                # Skip records where object_value is null
                if not object_value:
                    continue
                
                # Save each array element as a separate field_mapping row
                # All rows share the same extracted_field_name but have different destination fields
                await database.execute(
                    """
                    INSERT INTO field_mappings (
                      id, migration_flow_id, extracted_field_name, salesforce_object, salesforce_field, lookup_of, is_duplicate_check, is_required, created_at
                    ) VALUES (
                      :id, :flow_id, :field_name, :object, :field, :lookup, :is_duplicate_check, :is_required, :created_at
                    )
                    """,
                    {
                        "id": str(uuid4()),
                        "flow_id": flow_id,
                        "field_name": field_name,  # Same extracted_field_name for all split fields
                        "object": object_value,
                        "field": field_value,
                        "lookup": mapping_item.get("lookup_of"),
                        "is_duplicate_check": mapping_item.get("is_duplicate_check", False),
                        "is_required": mapping_item.get("is_required", False),
                        "created_at": datetime.utcnow()
                    }
                )
        else:
            # Handle simple mapping (one-to-one)
            if destination_type == "salesforce":
                object_value = config.get("object")
                field_value = config.get("field")
            else:
                object_value = config.get("table") or config.get("sheet") or config.get("object")
                field_value = config.get("column") or config.get("field")

            # Skip records where object_value is null (salesforce_object column has NOT NULL constraint)
            if not object_value:
                continue

            await database.execute(
                """
                INSERT INTO field_mappings (
                  id, migration_flow_id, extracted_field_name, salesforce_object, salesforce_field, lookup_of, is_duplicate_check, is_required, created_at
                ) VALUES (
                  :id, :flow_id, :field_name, :object, :field, :lookup, :is_duplicate_check, :is_required, :created_at
                )
                """,
                {
                    "id": str(uuid4()),
                    "flow_id": flow_id,
                    "field_name": field_name,
                    "object": object_value,
                    "field": field_value,
                    "lookup": config.get("lookup_of"),
                    "is_duplicate_check": config.get("is_duplicate_check", False),
                    "is_required": config.get("is_required", False),
                    "created_at": datetime.utcnow()
                }
            )

    return {"status": "success", "flow_id": flow_id}


async def _lookup_gsheets_spreadsheet_name(user_id: UUID, connector_ref: str) -> Optional[str]:
    """
    Resolve display name for a Google Sheets source. connector_ref may be
    user_google_spreadsheets.id (UUID) or Google spreadsheet_id string.
    """
    connector_ref = (connector_ref or "").strip()
    if not connector_ref:
        return None
    src_row = None
    try:
        UUID(str(connector_ref))
        src_row = await database.fetch_one(
            """
            SELECT spreadsheet_name
            FROM user_google_spreadsheets
            WHERE user_id = :user_id AND id = :ugs_id
            ORDER BY last_accessed_at DESC NULLS LAST
            LIMIT 1
            """,
            {"user_id": str(user_id), "ugs_id": str(connector_ref)},
        )
    except (ValueError, TypeError):
        pass
    if not src_row:
        src_row = await database.fetch_one(
            """
            SELECT spreadsheet_name
            FROM user_google_spreadsheets
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
            ORDER BY last_accessed_at DESC NULLS LAST
            LIMIT 1
            """,
            {"user_id": str(user_id), "spreadsheet_id": connector_ref},
        )
    if not src_row:
        return None
    return src_row["spreadsheet_name"]


async def _lookup_gsheets_sheet_tab_name(user_id: UUID, connector_ref: str) -> Optional[str]:
    """
    Tab name within the spreadsheet (user_google_spreadsheets.sheet_name).
    Stored in extracted_schemas.table_name for gsheets — same role as SQL table / SF object name.
    """
    connector_ref = (connector_ref or "").strip()
    if not connector_ref:
        return None
    src_row = None
    try:
        UUID(str(connector_ref))
        src_row = await database.fetch_one(
            """
            SELECT sheet_name
            FROM user_google_spreadsheets
            WHERE user_id = :user_id AND id = :ugs_id
            ORDER BY last_accessed_at DESC NULLS LAST
            LIMIT 1
            """,
            {"user_id": str(user_id), "ugs_id": str(connector_ref)},
        )
    except (ValueError, TypeError):
        pass
    if not src_row:
        src_row = await database.fetch_one(
            """
            SELECT sheet_name
            FROM user_google_spreadsheets
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
            ORDER BY last_accessed_at DESC NULLS LAST
            LIMIT 1
            """,
            {"user_id": str(user_id), "spreadsheet_id": connector_ref},
        )
    if not src_row:
        return None
    tab = src_row["sheet_name"]
    return str(tab).strip() if tab is not None else None


@router.get("/list-mapping-flows")
async def list_mapping_flows(user_id: UUID = Depends(get_current_user)):
    # Get flows with additional details
    rows = await database.fetch_all(
        """
        SELECT 
            mf.id, 
            mf.migration_name, 
            mf.created_at,
            mf.updated_at,
            mf.destination_type,
            mf.org_id,
            mf.incremental_sync_enabled,
            mf.schedule_enabled,
            mf.schedule_frequency,
            mf.schedule_config,
            mf.next_run_at,
            mf.last_run_at,
            mf.schedule_timezone,
            mf.bidirectional_sync_enabled,
            mf.reverse_flow_id,
            es.document_type,
            es.user_prompt,
            es.extracted_schema_type,
            es.connector_id,
            es.sql_query,
            es.table_name,
            COUNT(fm.id) as field_mappings_count
        FROM migration_flows mf
        LEFT JOIN extracted_schemas es ON mf.id = es.migration_flow_id
        LEFT JOIN field_mappings fm ON mf.id = fm.migration_flow_id
        WHERE mf.user_id = :user_id
          AND mf.parent_flow_id IS NULL
        GROUP BY mf.id, mf.migration_name, mf.created_at, mf.updated_at, mf.destination_type, mf.org_id,
                 mf.incremental_sync_enabled,
                 mf.schedule_enabled, mf.schedule_frequency, mf.schedule_config, mf.next_run_at, 
                 mf.last_run_at, mf.schedule_timezone, mf.bidirectional_sync_enabled, mf.reverse_flow_id,
                 es.document_type, es.user_prompt, 
                 es.extracted_schema_type, es.connector_id, es.sql_query, es.table_name
        ORDER BY mf.created_at DESC
        """,
        {"user_id": str(user_id)}
    )
    
    flows = []
    for row in rows:
        flow_data = dict(row)
        # Convert datetime objects to ISO strings for JSON serialization
        if flow_data.get('created_at'):
            flow_data['created_at'] = flow_data['created_at'].isoformat()
        if flow_data.get('updated_at'):
            flow_data['updated_at'] = flow_data['updated_at'].isoformat()
        destination_type = (flow_data.get('destination_type') or 'salesforce').lower()
        destination_name = None
        destination_id = flow_data.get('org_id')
        connector_id = flow_data.get('connector_id')
        if connector_id:
            connector_id = str(connector_id)

        if destination_type == 'salesforce' and destination_id:
            dest_row = await database.fetch_one(
                """
                SELECT COALESCE(org_display_name, instance_url) AS display_name, id AS token_id
                FROM salesforce_tokens
                WHERE user_id = :user_id AND org_id = :org_id
                ORDER BY issued_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id), "org_id": destination_id},
            )
            if dest_row:
                destination_name = dest_row["display_name"]
                flow_data['salesforce_token_id'] = dest_row["token_id"]
        elif destination_type == 'google_sheets':
            lookup_id = destination_id or connector_id
            if lookup_id:
                dest_row = await database.fetch_one(
                    """
                    SELECT spreadsheet_name
                    FROM user_google_spreadsheets
                    WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
                    ORDER BY last_accessed_at DESC
                    LIMIT 1
                    """,
                    {"user_id": str(user_id), "spreadsheet_id": lookup_id},
                )
                if dest_row:
                    destination_name = dest_row["spreadsheet_name"]
        elif destination_type == 'sql':
            lookup_id = destination_id or connector_id
            if lookup_id:
                dest_row = await database.fetch_one(
                    """
                    SELECT name
                    FROM sql_connectors
                    WHERE user_id = :user_id AND id = :connector_id
                    """,
                    {"user_id": str(user_id), "connector_id": lookup_id},
                )
                if dest_row:
                    destination_name = dest_row["name"]

        if flow_data.get('salesforce_token_id'):
            flow_data['salesforce_token_id'] = str(flow_data['salesforce_token_id'])

        source_type = (flow_data.get('extracted_schema_type') or 'file').lower()
        source_name = None
        source_id = flow_data.get('connector_id')
        if source_id:
            source_id = str(source_id)

        if source_type == "sql" and source_id:
            src_row = await database.fetch_one(
                """
                SELECT name
                FROM sql_connectors
                WHERE user_id = :user_id AND id = :connector_id
                """,
                {"user_id": str(user_id), "connector_id": source_id},
            )
            if src_row:
                source_name = src_row["name"]
        elif source_type == "gsheets" and source_id:
            source_name = await _lookup_gsheets_spreadsheet_name(user_id, source_id)
        elif source_type == "salesforce" and source_id:
            src_row = await database.fetch_one(
                """
                SELECT COALESCE(org_display_name, instance_url) AS display_name
                FROM salesforce_tokens
                WHERE user_id = :user_id AND id = :token_id
                LIMIT 1
                """,
                {"user_id": str(user_id), "token_id": source_id},
            )
            if src_row:
                source_name = src_row["display_name"]

        if not source_name:
            if source_type == "salesforce":
                # table_name stores SF object API name, not org display name
                pass
            elif source_type in ("sql", "gsheets"):
                source_name = flow_data.get('table_name')
            else:
                source_name = flow_data.get('document_type')

        flow_data['destination_type'] = destination_type
        flow_data['destination_name'] = destination_name
        flow_data['destination_id'] = destination_id or connector_id
        flow_data['source_type'] = source_type
        flow_data['source_name'] = source_name
        
        # Add schedule information
        schedule_config = flow_data.get('schedule_config')
        if schedule_config and isinstance(schedule_config, str):
            try:
                schedule_config = json.loads(schedule_config)
            except:
                schedule_config = {}
        
        # Convert next_run_at and last_run_at to UTC ISO string
        # PostgreSQL TIMESTAMPTZ returns timezone-aware datetime, ensure we convert to UTC
        next_run_at = flow_data.get('next_run_at')
        next_run_at_iso = None
        if next_run_at:
            # If it's timezone-aware, ensure it's in UTC; if naive, assume UTC
            if hasattr(next_run_at, 'tzinfo') and next_run_at.tzinfo:
                next_run_at_utc = next_run_at.astimezone(pytz.UTC)
                next_run_at_iso = next_run_at_utc.isoformat()
            else:
                next_run_at_iso = next_run_at.isoformat() + 'Z'  # Mark as UTC
        
        last_run_at = flow_data.get('last_run_at')
        last_run_at_iso = None
        if last_run_at:
            if hasattr(last_run_at, 'tzinfo') and last_run_at.tzinfo:
                last_run_at_utc = last_run_at.astimezone(pytz.UTC)
                last_run_at_iso = last_run_at_utc.isoformat()
            else:
                last_run_at_iso = last_run_at.isoformat() + 'Z'  # Mark as UTC
        
        flow_data['schedule'] = {
            'schedule_enabled': flow_data.get('schedule_enabled', False),
            'schedule_frequency': flow_data.get('schedule_frequency'),
            'schedule_config': schedule_config if schedule_config else {},
            'next_run_at': next_run_at_iso,
            'last_run_at': last_run_at_iso,
            'schedule_timezone': flow_data.get('schedule_timezone')
        }

        flows.append(flow_data)

    return {"status": "success", "flows": flows}


@router.get("/flow/{flow_id}")
async def get_flow_details(flow_id: str, user_id: UUID = Depends(get_current_user)):
    # Get detailed flow information including field mappings and extracted schema
    flow_result = await database.fetch_one(
        """
        SELECT 
            mf.id, 
            mf.migration_name, 
            mf.created_at,
            mf.updated_at,
            mf.org_id,
            mf.destination_type,
            mf.migration_rules,
            mf.incremental_sync_enabled,
            mf.incremental_cursor_field,
            mf.incremental_high_watermark,
            mf.incremental_high_watermark_text,
            mf.incremental_last_extraction_success_at,
            mf.incremental_query_fingerprint,
            mf.bidirectional_sync_enabled,
            mf.parent_flow_id,
            mf.reverse_flow_id
        FROM migration_flows mf
        WHERE mf.id = :flow_id AND mf.user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)}
    )
    
    if not flow_result:
        raise HTTPException(status_code=404, detail="Flow not found")
    
    # Get extracted schema details
    schema_result = await database.fetch_one(
        """
        SELECT 
            id, document_type, file_key, openai_file_id, extracted_schema, user_prompt, approved_by_user, created_at,
            extracted_schema_type, connector_id, sql_query, table_name
        FROM extracted_schemas
        WHERE migration_flow_id = :flow_id
        """,
        {"flow_id": flow_id}
    )
    
    # Get field mappings
    field_mappings = await database.fetch_all(
        """
        SELECT 
            id, extracted_field_name, salesforce_object, salesforce_field, lookup_of, is_duplicate_check, is_required, created_at
        FROM field_mappings
        WHERE migration_flow_id = :flow_id
        ORDER BY created_at
        """,
        {"flow_id": flow_id}
    )
    
    # Get Salesforce org details
    org_result = None
    if flow_result["destination_type"] == 'salesforce' and flow_result["org_id"]:
        org_result = await database.fetch_one(
            """
            SELECT instance_url, id AS token_id
            FROM salesforce_tokens
            WHERE org_id = :org_id
            ORDER BY issued_at DESC
            LIMIT 1
            """,
            {"org_id": flow_result["org_id"]}
        )
    
    # Prepare response
    flow_data = dict(flow_result)
    for _uuid_key in ("parent_flow_id", "reverse_flow_id"):
        if flow_data.get(_uuid_key):
            flow_data[_uuid_key] = str(flow_data[_uuid_key])
    if flow_data.get('created_at'):
        flow_data['created_at'] = flow_data['created_at'].isoformat()
    if flow_data.get('updated_at'):
        flow_data['updated_at'] = flow_data['updated_at'].isoformat()
    for _ts_key in (
        "incremental_high_watermark",
        "incremental_last_extraction_success_at",
    ):
        if flow_data.get(_ts_key):
            flow_data[_ts_key] = flow_data[_ts_key].isoformat()
    if flow_data.get('migration_rules'):
        if isinstance(flow_data['migration_rules'], str):
            flow_data['migration_rules'] = json.loads(flow_data['migration_rules'])
        from utils.migration_rules_utils import normalize_migration_rules_for_save

        flow_data['migration_rules'] = (
            normalize_migration_rules_for_save(flow_data['migration_rules']) or {}
        )
    
    schema_data = None
    if schema_result:
        schema_data = dict(schema_result)
        if schema_data.get('created_at'):
            schema_data['created_at'] = schema_data['created_at'].isoformat()
        if schema_data.get('extracted_schema'):
            schema_data['extracted_schema'] = json.loads(schema_data['extracted_schema'])
        if schema_data.get('salesforce_token_id'):
            schema_data['salesforce_token_id'] = str(schema_data['salesforce_token_id'])
        # Ensure JSON clients always get a string (asyncpg / driver edge cases)
        sq = schema_data.get("sql_query")
        if sq is not None and not isinstance(sq, str):
            schema_data["sql_query"] = str(sq)

    destination_type = (flow_data.get('destination_type') or 'salesforce').lower()
    destination_name = None
    destination_id = flow_data.get('org_id')
    connector_id = schema_data.get('connector_id') if schema_data else None
    if connector_id:
        connector_id = str(connector_id)

    if destination_type == 'salesforce' and destination_id:
        dest_row = await database.fetch_one(
            """
            SELECT COALESCE(org_display_name, instance_url) AS display_name, id AS token_id
            FROM salesforce_tokens
            WHERE user_id = :user_id AND org_id = :org_id
            ORDER BY issued_at DESC
            LIMIT 1
            """,
            {"user_id": str(user_id), "org_id": destination_id},
        )
        if dest_row:
            destination_name = dest_row["display_name"]
            flow_data['salesforce_token_id'] = dest_row["token_id"]
    elif destination_type == 'google_sheets':
        lookup_id = destination_id or connector_id
        if lookup_id:
            dest_row = await database.fetch_one(
                """
                SELECT spreadsheet_name
                FROM user_google_spreadsheets
                WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
                ORDER BY last_accessed_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id), "spreadsheet_id": lookup_id},
            )
            if dest_row:
                destination_name = dest_row["spreadsheet_name"]
    elif destination_type == 'sql':
        lookup_id = destination_id or connector_id
        if lookup_id:
            dest_row = await database.fetch_one(
                """
                SELECT name
                FROM sql_connectors
                WHERE user_id = :user_id AND id = :connector_id
                """,
                {"user_id": str(user_id), "connector_id": lookup_id},
            )
            if dest_row:
                destination_name = dest_row["name"]

    if flow_data.get('salesforce_token_id'):
        flow_data['salesforce_token_id'] = str(flow_data['salesforce_token_id'])

    source_type = 'file'
    source_name = None
    source_id = connector_id
    if schema_data:
        source_type = (schema_data.get('extracted_schema_type') or 'file').lower()
    else:
        source_type = (flow_data.get('extracted_schema_type') or 'file').lower()
        if source_type in ('sql', 'gsheets', 'salesforce'):
            source_id = flow_data.get('connector_id')
            if source_id:
                source_id = str(source_id)

    if source_type == 'sql' and source_id:
        src_row = await database.fetch_one(
            """
            SELECT name
            FROM sql_connectors
            WHERE user_id = :user_id AND id = :connector_id
            """,
            {"user_id": str(user_id), "connector_id": source_id},
        )
        if src_row:
            source_name = src_row["name"]
    elif source_type == 'gsheets' and source_id:
        source_name = await _lookup_gsheets_spreadsheet_name(user_id, source_id)
    elif source_type == 'salesforce' and source_id:
        src_row = await database.fetch_one(
            """
            SELECT COALESCE(org_display_name, instance_url) AS display_name
            FROM salesforce_tokens
            WHERE user_id = :user_id AND id = :token_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "token_id": source_id},
        )
        if src_row:
            source_name = src_row["display_name"]

    if not source_name:
        if schema_data and source_type in ('sql', 'gsheets'):
            source_name = schema_data.get('table_name')
        elif schema_data and source_type != 'salesforce':
            source_name = schema_data.get('document_type')
        elif source_type in ('sql', 'gsheets'):
            source_name = flow_data.get('table_name')
        elif source_type != 'salesforce':
            source_name = flow_data.get('document_type')

    flow_data['destination_type'] = destination_type
    flow_data['destination_name'] = destination_name
    flow_data['destination_id'] = destination_id or connector_id
    flow_data['source_type'] = source_type
    flow_data['source_name'] = source_name
    flow_data['source_target'] = schema_data.get('table_name')
    
    field_mappings_data = []
    for fm in field_mappings:
        fm_data = dict(fm)
        if fm_data.get('created_at'):
            fm_data['created_at'] = fm_data['created_at'].isoformat()
        field_mappings_data.append(fm_data)
    
    org_data = None
    if org_result:
        org_data = dict(org_result)

    reverse_flow_summary = None
    rid = flow_data.get("reverse_flow_id")
    if rid:
        try:
            rev_row = await database.fetch_one(
                """
                SELECT id, migration_name, incremental_sync_enabled, incremental_cursor_field,
                       incremental_high_watermark, parent_flow_id
                FROM migration_flows
                WHERE id = :rid AND user_id = :user_id
                """,
                {"rid": str(rid), "user_id": str(user_id)},
            )
            if rev_row:
                reverse_flow_summary = dict(rev_row)
                for _k in ("incremental_high_watermark",):
                    if reverse_flow_summary.get(_k) and hasattr(reverse_flow_summary[_k], "isoformat"):
                        reverse_flow_summary[_k] = reverse_flow_summary[_k].isoformat()
                reverse_flow_summary["id"] = str(reverse_flow_summary["id"])
                if reverse_flow_summary.get("parent_flow_id"):
                    reverse_flow_summary["parent_flow_id"] = str(reverse_flow_summary["parent_flow_id"])
        except Exception as e:
            print(f"⚠️ reverse_flow summary load failed: {e}")
    
    return {
        "status": "success",
        "flow": flow_data,
        "schema": schema_data,
        "field_mappings": field_mappings_data,
        "org": org_data,
        "reverse_flow": reverse_flow_summary,
    }


class PatchFlowNameRequest(BaseModel):
    migration_name: str


@router.patch("/flow/{flow_id}/name")
async def patch_flow_name(
    flow_id: str,
    body: PatchFlowNameRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Rename a migration flow (migration_name only)."""
    name = (body.migration_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Flow name cannot be empty.")
    if len(name) > 255:
        raise HTTPException(
            status_code=400,
            detail="Flow name is too long (maximum 255 characters).",
        )

    row = await database.fetch_one(
        """
        SELECT id FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Flow not found")

    now = datetime.utcnow()
    await database.execute(
        """
        UPDATE migration_flows
        SET migration_name = :name, updated_at = :now
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {
            "name": name,
            "flow_id": flow_id,
            "user_id": str(user_id),
            "now": now,
        },
    )

    return {
        "status": "success",
        "migration_name": name,
        "updated_at": now.isoformat(),
    }


class PatchIncrementalSyncRequest(BaseModel):
    incremental_sync_enabled: Optional[bool] = None
    incremental_cursor_field: Optional[str] = None
    # UTC instant for incremental predicate; omit = unchanged; JSON null clears watermark (+ text).
    incremental_high_watermark: Optional[datetime] = None


@router.patch("/flow/{flow_id}/incremental-sync")
async def patch_flow_incremental_sync(
    flow_id: str,
    body: PatchIncrementalSyncRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Update incremental sync settings for a flow (Salesforce / SQL sources)."""
    row = await database.fetch_one(
        """
        SELECT id FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Flow not found")

    patch_data = body.model_dump(exclude_unset=True)
    updates = []
    values: dict = {"flow_id": flow_id, "user_id": str(user_id), "now": datetime.utcnow()}
    if "incremental_sync_enabled" in patch_data:
        updates.append("incremental_sync_enabled = :incremental_sync_enabled")
        values["incremental_sync_enabled"] = patch_data["incremental_sync_enabled"]
    if "incremental_cursor_field" in patch_data:
        cur = (patch_data["incremental_cursor_field"] or "").strip()
        updates.append("incremental_cursor_field = :incremental_cursor_field")
        values["incremental_cursor_field"] = cur if cur else None
    if "incremental_high_watermark" in patch_data:
        wm = patch_data["incremental_high_watermark"]
        if wm is None:
            updates.append("incremental_high_watermark = NULL")
            updates.append("incremental_high_watermark_text = NULL")
        else:
            if wm.tzinfo is None:
                wm = wm.replace(tzinfo=timezone.utc)
            else:
                wm = wm.astimezone(timezone.utc)
            updates.append("incremental_high_watermark = :incremental_high_watermark")
            values["incremental_high_watermark"] = wm

    if not updates:
        raise HTTPException(status_code=400, detail="No incremental sync fields to update.")

    updates.append("updated_at = :now")
    await database.execute(
        f"""
        UPDATE migration_flows
        SET {", ".join(updates)}
        WHERE id = :flow_id AND user_id = :user_id
        """,
        values,
    )
    return {"status": "success"}


@router.post("/flow/{flow_id}/incremental-sync/reset")
async def reset_flow_incremental_watermark(
    flow_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Clear incremental watermark and fingerprint; next run behaves as baseline/full extract."""
    row = await database.fetch_one(
        """
        SELECT id FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Flow not found")

    await database.execute(
        """
        UPDATE migration_flows
        SET incremental_high_watermark = NULL,
            incremental_high_watermark_text = NULL,
            incremental_query_fingerprint = NULL,
            updated_at = NOW()
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    return {"status": "success"}


class PatchBidirectionalSyncRequest(BaseModel):
    bidirectional_sync_enabled: Optional[bool] = None


@router.patch("/flow/{flow_id}/bidirectional-sync")
async def patch_flow_bidirectional_sync(
    flow_id: str,
    body: PatchBidirectionalSyncRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Enable or disable bidirectional sync on the primary flow. Requires a reverse leg when enabling."""
    uid = str(user_id)
    row = await database.fetch_one(
        """
        SELECT id, parent_flow_id, reverse_flow_id, bidirectional_sync_enabled
        FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": uid},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Flow not found")
    rd = dict(row)
    if rd.get("parent_flow_id"):
        raise HTTPException(
            status_code=400,
            detail="Bidirectional settings can only be changed on the primary flow, not the reverse leg.",
        )
    patch = body.model_dump(exclude_unset=True)
    if "bidirectional_sync_enabled" not in patch:
        raise HTTPException(status_code=400, detail="No bidirectional fields to update.")

    want = bool(patch["bidirectional_sync_enabled"])
    if want and not rd.get("reverse_flow_id"):
        raise HTTPException(
            status_code=400,
            detail="Create a reverse leg first (POST .../bidirectional/reverse-leg) before enabling bidirectional sync.",
        )

    now = datetime.utcnow()
    if want:
        await database.execute(
            """
            UPDATE migration_flows
            SET bidirectional_sync_enabled = TRUE,
                incremental_sync_enabled = TRUE,
                updated_at = :now
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": uid, "now": now},
        )
        await database.execute(
            """
            UPDATE migration_flows
            SET incremental_sync_enabled = TRUE,
                updated_at = :now
            WHERE id = :rid AND user_id = :user_id
            """,
            {"rid": str(rd["reverse_flow_id"]), "user_id": uid, "now": now},
        )
    else:
        await database.execute(
            """
            UPDATE migration_flows
            SET bidirectional_sync_enabled = FALSE,
                updated_at = :now
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": uid, "now": now},
        )
    return {"status": "success", "bidirectional_sync_enabled": want}


@router.post("/flow/{flow_id}/bidirectional/reverse-leg")
async def post_flow_bidirectional_reverse_leg(
    flow_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Create (or replace) the hidden reverse-leg flow linked from this primary flow."""
    logger.info(
        "[REVERSE LEG] API POST /flow/%s/bidirectional/reverse-leg user_id=%s",
        flow_id,
        user_id,
    )
    try:
        out = await create_reverse_leg_flow(flow_id, str(user_id))
    except ValueError as e:
        logger.warning(
            "[REVERSE LEG] failed parent_flow_id=%s: %s",
            flow_id,
            e,
        )
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(
        "[REVERSE LEG] API success parent_flow_id=%s reverse_flow_id=%s",
        flow_id,
        out.get("reverse_flow_id"),
    )
    return out


@router.get("/flow/{flow_id}/incremental-sync/cursor-field-options")
async def get_flow_incremental_cursor_field_options(
    flow_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """
    Date/datetime cursor candidates for incremental sync: Salesforce fields from cached describe,
    SQL columns from sql_extracted_schemas.schema_data for the flow's table_name.
    """
    result = await IncrementalSyncService.get_incremental_cursor_field_options(flow_id, str(user_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    return result


class CloneFlowRequest(BaseModel):
    migration_name: str


@router.post("/flow/{flow_id}/clone")
async def clone_flow(
    flow_id: str,
    body: CloneFlowRequest,
    user_id: UUID = Depends(get_current_user),
):
    """
    Duplicate a flow for the same user: new migration_flows + extracted_schemas + field_mappings rows.
    Reuses org_id and connector references. Schedule is cleared on the copy. Does not run migration.
    """
    new_name = (body.migration_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Flow name cannot be empty.")
    if len(new_name) > 255:
        raise HTTPException(
            status_code=400,
            detail="Flow name is too long (maximum 255 characters).",
        )

    uid = str(user_id)
    dup = await database.fetch_one(
        """
        SELECT id FROM migration_flows
        WHERE user_id = :user_id AND LOWER(TRIM(migration_name)) = LOWER(:name)
        LIMIT 1
        """,
        {"user_id": uid, "name": new_name},
    )
    if dup:
        raise HTTPException(
            status_code=409,
            detail="A flow with this name already exists. Choose a different name.",
        )

    src_flow = await database.fetch_one(
        """
        SELECT org_id, destination_type, migration_rules
        FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": uid},
    )
    if not src_flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    src_flow_d = dict(src_flow)
    src_schema = await database.fetch_one(
        """
        SELECT
            document_type, file_key, openai_file_id, extracted_schema, user_prompt, approved_by_user,
            extracted_schema_type, connector_id, sql_query, table_name
        FROM extracted_schemas
        WHERE migration_flow_id = :flow_id
        LIMIT 1
        """,
        {"flow_id": flow_id},
    )
    if not src_schema:
        raise HTTPException(
            status_code=400,
            detail="This flow has no stored schema and cannot be cloned.",
        )

    src_schema_d = dict(src_schema)
    fm_rows = await database.fetch_all(
        """
        SELECT extracted_field_name, salesforce_object, salesforce_field, lookup_of,
               is_duplicate_check, is_required
        FROM field_mappings
        WHERE migration_flow_id = :flow_id
        ORDER BY created_at
        """,
        {"flow_id": flow_id},
    )

    mr = src_flow_d.get("migration_rules")
    if isinstance(mr, str):
        try:
            mr = json.loads(mr) if mr.strip() else {}
        except json.JSONDecodeError:
            mr = {}
    migration_rules_json = _migration_rules_dict_to_db_value(mr if isinstance(mr, dict) else None)

    ext_raw = src_schema_d.get("extracted_schema")
    if isinstance(ext_raw, dict):
        extracted_schema_json = json.dumps(ext_raw)
    elif isinstance(ext_raw, str):
        extracted_schema_json = ext_raw
    else:
        extracted_schema_json = json.dumps(ext_raw) if ext_raw is not None else "{}"

    dest_type = (src_flow_d.get("destination_type") or "salesforce")
    sql_q = src_schema_d.get("sql_query")
    if sql_q is not None and not isinstance(sql_q, str):
        sql_q = str(sql_q)
    conn_id = src_schema_d.get("connector_id")
    if conn_id is not None:
        conn_id = str(conn_id)

    new_flow_id = str(uuid4())
    new_schema_id = str(uuid4())
    now = datetime.utcnow()

    async with database.transaction():
        await database.execute(
            """
            INSERT INTO migration_flows (
                id, user_id, org_id, migration_name, destination_type, migration_rules,
                created_at, updated_at,
                schedule_enabled, schedule_frequency, schedule_config, next_run_at, last_run_at, schedule_timezone
            ) VALUES (
                :id, :user_id, :org_id, :name, :destination_type, :migration_rules,
                :created_at, :updated_at,
                FALSE, NULL, NULL, NULL, NULL, NULL
            )
            """,
            {
                "id": new_flow_id,
                "user_id": uid,
                "org_id": src_flow_d.get("org_id"),
                "name": new_name,
                "destination_type": dest_type,
                "migration_rules": migration_rules_json,
                "created_at": now,
                "updated_at": now,
            },
        )

        await database.execute(
            """
            INSERT INTO extracted_schemas (
                id, user_id, document_type, file_key, openai_file_id, extracted_schema, user_prompt,
                approved_by_user, destination_type, created_at, migration_flow_id,
                extracted_schema_type, connector_id, sql_query, table_name
            ) VALUES (
                :id, :user_id, :doc_type, :file_key, :file_id, :schema, :user_prompt,
                :approved, :destination_type, :created_at, :flow_id,
                :extracted_schema_type, :connector_id, :sql_query, :table_name
            )
            """,
            {
                "id": new_schema_id,
                "user_id": uid,
                "doc_type": src_schema_d.get("document_type"),
                "file_key": src_schema_d.get("file_key"),
                "file_id": src_schema_d.get("openai_file_id"),
                "schema": extracted_schema_json,
                "user_prompt": src_schema_d.get("user_prompt"),
                "approved": True
                if src_schema_d.get("approved_by_user") is None
                else bool(src_schema_d.get("approved_by_user")),
                "destination_type": dest_type,
                "created_at": now,
                "flow_id": new_flow_id,
                "extracted_schema_type": src_schema_d.get("extracted_schema_type") or "file",
                "connector_id": conn_id,
                "sql_query": sql_q,
                "table_name": src_schema_d.get("table_name"),
            },
        )

        for fm in fm_rows or []:
            r = dict(fm)
            await database.execute(
                """
                INSERT INTO field_mappings (
                    id, migration_flow_id, extracted_field_name, salesforce_object, salesforce_field,
                    lookup_of, is_duplicate_check, is_required, created_at
                ) VALUES (
                    :id, :flow_id, :field_name, :object, :field, :lookup,
                    :is_duplicate_check, :is_required, :created_at
                )
                """,
                {
                    "id": str(uuid4()),
                    "flow_id": new_flow_id,
                    "field_name": r.get("extracted_field_name"),
                    "object": r.get("salesforce_object"),
                    "field": r.get("salesforce_field"),
                    "lookup": r.get("lookup_of"),
                    "is_duplicate_check": bool(r.get("is_duplicate_check")),
                    "is_required": bool(r.get("is_required")),
                    "created_at": now,
                },
            )

    return {
        "status": "success",
        "flow_id": new_flow_id,
        "migration_name": new_name,
    }


def _flatten_extracted_schema_source_keys(extracted_schema: Any) -> Set[str]:
    """Collect source field names/paths from stored extracted_schema (nested dicts)."""
    allowed: Set[str] = set()
    if not isinstance(extracted_schema, dict):
        return allowed

    def walk(d: dict, prefix: str = "") -> None:
        for k, v in d.items():
            if str(k).startswith("_"):
                continue
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                walk(v, path)
            else:
                allowed.add(path)
                allowed.add(str(k))

    walk(extracted_schema)
    return allowed


def _parse_gsheets_header_to_columns(header_schema_raw: Any) -> Dict[str, Dict[str, Any]]:
    """Parse gsheets_extracted_schemas.header_schema into column name -> metadata (matches map_to_google_sheets)."""
    columns: Dict[str, Dict[str, Any]] = {}
    if header_schema_raw is None:
        return columns
    if isinstance(header_schema_raw, str):
        try:
            header_schema_raw = json.loads(header_schema_raw)
        except json.JSONDecodeError:
            return columns
    if isinstance(header_schema_raw, dict) and "headers" in header_schema_raw:
        for header in header_schema_raw.get("headers") or []:
            if isinstance(header, dict):
                col_name = header.get("name", "")
                if col_name:
                    columns[str(col_name)] = {
                        "data_type": header.get("type", "text"),
                        "description": header.get("description", ""),
                    }
    elif isinstance(header_schema_raw, dict):
        for col_name, col_type in header_schema_raw.items():
            if str(col_name).startswith("_"):
                continue
            if isinstance(col_type, dict):
                columns[str(col_name)] = {
                    "data_type": col_type.get("type", "text"),
                    "description": "",
                }
            else:
                columns[str(col_name)] = {
                    "data_type": str(col_type) if col_type else "text",
                    "description": "",
                }
    return columns


def _sql_schema_data_to_columns(schema_data_raw: Any) -> Dict[str, Dict[str, Any]]:
    """Parse sql_extracted_schemas.schema_data into column name -> metadata."""
    columns: Dict[str, Dict[str, Any]] = {}
    if schema_data_raw is None:
        return columns
    if isinstance(schema_data_raw, str):
        try:
            schema_data_raw = json.loads(schema_data_raw)
        except json.JSONDecodeError:
            return columns
    if isinstance(schema_data_raw, dict) and "columns" in schema_data_raw:
        for col in schema_data_raw.get("columns") or []:
            if isinstance(col, dict):
                col_name = col.get("column_name", "")
                if col_name:
                    columns[str(col_name)] = {"data_type": col.get("data_type", "VARCHAR")}
    elif isinstance(schema_data_raw, list):
        for col in schema_data_raw:
            if isinstance(col, dict):
                col_name = col.get("column_name", "")
                if col_name:
                    columns[str(col_name)] = {"data_type": col.get("data_type", "VARCHAR")}
    return columns


def _sql_schema_data_to_foreign_keys(schema_data_raw: Any) -> List[Dict[str, Any]]:
    """Parse sql_extracted_schemas.schema_data into normalized foreign key entries."""
    if schema_data_raw is None:
        return []
    raw = schema_data_raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, dict):
        return []
    fks = raw.get("foreign_keys")
    if not isinstance(fks, list):
        return []
    out: List[Dict[str, Any]] = []
    for fk in fks:
        if not isinstance(fk, dict):
            continue
        col = (fk.get("column_name") or fk.get("column") or "").strip()
        rtbl = (fk.get("referenced_table") or fk.get("foreign_table_name") or "").strip()
        rcol = (fk.get("referenced_column") or fk.get("foreign_column_name") or "").strip()
        if not col or not rtbl or not rcol:
            continue
        out.append(
            {
                "constraint_name": (fk.get("constraint_name") or "").strip() or None,
                "column_name": col,
                "referenced_table": rtbl,
                "referenced_column": rcol,
                "on_update": (fk.get("on_update") or "").strip() or None,
                "on_delete": (fk.get("on_delete") or "").strip() or None,
                "position": fk.get("position"),
            }
        )
    return out


async def _sql_foreign_keys_with_runtime_fallback(
    user_id: UUID,
    connector_id: Optional[str],
    table_name: Optional[str],
    schema_data_raw: Any,
) -> List[Dict[str, Any]]:
    """
    Prefer cached schema_data.foreign_keys; if absent, fetch live table schema and read
    connector-provided foreign_keys (best effort).
    """
    cached = _sql_schema_data_to_foreign_keys(schema_data_raw)
    if cached:
        return cached
    cid = (connector_id or "").strip()
    tbl = (table_name or "").strip()
    if not cid or not tbl:
        return []
    try:
        row = await database.fetch_one(
            """
            SELECT connector_type, host, port, database_name, username, password_encrypted, ssl_enabled, additional_config
            FROM sql_connectors
            WHERE id = :cid AND user_id = :uid AND is_active = true
            LIMIT 1
            """,
            {"cid": cid, "uid": str(user_id)},
        )
        if not row:
            return []
        from services.sql_connector_service import SQLConnectorService

        svc = SQLConnectorService()
        cfg = svc.prepare_connector_config(dict(row))
        connector = svc.create_connector_instance(str(dict(row).get("connector_type") or ""), cfg)
        try:
            live_schema = await connector.get_table_schema(tbl)
            return _sql_schema_data_to_foreign_keys(live_schema)
        finally:
            try:
                await connector.close_connection()
            except Exception:
                pass
    except Exception:
        return []


def _salesforce_type_is_incremental_cursor(dtype: str) -> bool:
    t = (dtype or "").strip().lower()
    return t in {"date", "datetime"}


async def _resolve_salesforce_source_org_id(user_id: UUID, connector_ref: str) -> Optional[str]:
    """Map salesforce_tokens.id or org_id string to org_id for metadata queries."""
    uid = str(user_id)
    ref = str(connector_ref).strip()
    if not ref:
        return None
    row = await database.fetch_one(
        """
        SELECT org_id::text AS org_id FROM salesforce_tokens
        WHERE user_id = :user_id AND id::text = :ref
        ORDER BY issued_at DESC
        LIMIT 1
        """,
        {"user_id": uid, "ref": ref},
    )
    if row:
        oid = dict(row).get("org_id")
        if oid:
            return str(oid)
    row2 = await database.fetch_one(
        """
        SELECT org_id::text AS org_id FROM salesforce_tokens
        WHERE user_id = :user_id AND org_id = :ref
        ORDER BY issued_at DESC
        LIMIT 1
        """,
        {"user_id": uid, "ref": ref},
    )
    if row2:
        oid = dict(row2).get("org_id")
        if oid:
            return str(oid)
    return None


async def _merge_connector_source_field_names(
    user_id: UUID,
    ext_type: str,
    connector_id: Optional[str],
    into: Set[str],
    *,
    source_table_or_object: Optional[str] = None,
) -> None:
    """Add source field names from synced connector metadata (Sheets / SQL / Salesforce)."""
    et = (ext_type or "file").lower()
    stoo = (source_table_or_object or "").strip() or None

    if et == "salesforce":
        if not connector_id:
            return
        conn_id = str(connector_id)
        if not stoo:
            return
        org_id = await _resolve_salesforce_source_org_id(user_id, conn_id)
        if not org_id:
            return
        sf_rows = await database.fetch_all(
            """
            SELECT field_api_name FROM salesforce_object_fields
            WHERE user_id = :user_id AND org_id = :org_id
              AND LOWER(object_api_name) = LOWER(:obj)
            ORDER BY field_api_name
            """,
            {"user_id": str(user_id), "org_id": org_id, "obj": stoo},
        )
        for r in sf_rows or []:
            row = dict(r)
            fn = row.get("field_api_name")
            if fn:
                into.add(str(fn))
        return

    if not connector_id:
        return
    conn_id = str(connector_id)
    if et == "gsheets":
        uid = str(user_id)
        g_rows = []

        # Primary: extracted_schemas.connector_id is user_google_spreadsheets.id (save-mapping-flow).
        # gsheets_extracted_schemas rows are keyed by spreadsheet_id, not UGS id.
        ugs_row = None
        try:
            UUID(conn_id)
            ugs_row = await database.fetch_one(
                """
                SELECT spreadsheet_id FROM user_google_spreadsheets
                WHERE user_id = :user_id AND id = :cid
                LIMIT 1
                """,
                {"user_id": uid, "cid": conn_id},
            )
        except (ValueError, TypeError):
            pass

        if ugs_row:
            sid = dict(ugs_row).get("spreadsheet_id")
            if sid:
                g_rows = await database.fetch_all(
                    """
                    SELECT header_schema FROM gsheets_extracted_schemas
                    WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
                    """,
                    {"user_id": uid, "spreadsheet_id": str(sid)},
                )

        # Legacy: connector_id stored as google_sheets_connections.id
        if not g_rows:
            g_rows = await database.fetch_all(
                """
                SELECT header_schema FROM gsheets_extracted_schemas
                WHERE user_id = :user_id AND google_sheets_connection_id = :conn_id
                """,
                {"user_id": uid, "conn_id": conn_id},
            )

        # Fallback: connector_id is the Google spreadsheet_id string
        if not g_rows:
            g_rows = await database.fetch_all(
                """
                SELECT header_schema FROM gsheets_extracted_schemas
                WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
                """,
                {"user_id": uid, "spreadsheet_id": conn_id},
            )

        for row in g_rows or []:
            cols = _parse_gsheets_header_to_columns(row["header_schema"])
            for col_name in cols.keys():
                into.add(col_name)
    elif et == "sql":
        sql_rows = await database.fetch_all(
            """
            SELECT table_name, schema_data FROM sql_extracted_schemas
            WHERE user_id = :user_id AND connector_id = :connector_id
            """,
            {"user_id": str(user_id), "connector_id": conn_id},
        )
        rows_to_use = list(sql_rows or [])
        if stoo and rows_to_use:
            st_lower = stoo.lower()
            filtered = [
                r for r in rows_to_use if str(dict(r).get("table_name") or "").lower() == st_lower
            ]
            if filtered:
                rows_to_use = filtered
        distinct_tables = {
            str(dict(r).get("table_name") or "").strip().lower()
            for r in rows_to_use
            if dict(r).get("table_name")
        }
        multi_table = len(distinct_tables) > 1
        for row in rows_to_use:
            tbl = dict(row)["table_name"]
            cols = _sql_schema_data_to_columns(row["schema_data"])
            for col_name in cols.keys():
                into.add(col_name)
                # Qualified names are only needed when multiple tables are in scope (joins).
                # Single-table flows would duplicate every column as both "email" and "users.email".
                if multi_table:
                    into.add(f"{tbl}.{col_name}")


@router.get("/flow/{flow_id}/mapping-catalog")
async def get_flow_mapping_catalog(flow_id: str, user_id: UUID = Depends(get_current_user)):
    """
    Return source field names and destination schema (objects/sheets/tables + columns/fields)
    from stored extracted schemas and synced metadata — no AI, for the flow mapping editor.
    """
    flow_result = await database.fetch_one(
        """
        SELECT id, org_id, destination_type
        FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not flow_result:
        raise HTTPException(status_code=404, detail="Flow not found")

    schema_result = await database.fetch_one(
        """
        SELECT extracted_schema, extracted_schema_type, connector_id, table_name, sql_query
        FROM extracted_schemas
        WHERE migration_flow_id = :flow_id
        """,
        {"flow_id": flow_id},
    )
    if not schema_result:
        raise HTTPException(status_code=404, detail="No extracted schema for this flow")

    schema_row = dict(schema_result)
    flow_row = dict(flow_result)
    source_meta_table = schema_row.get("table_name")
    source_sql_q = schema_row.get("sql_query")
    if source_sql_q is not None and not isinstance(source_sql_q, str):
        source_sql_q = str(source_sql_q)

    field_mapping_rows = await database.fetch_all(
        """
        SELECT extracted_field_name FROM field_mappings
        WHERE migration_flow_id = :flow_id
        """,
        {"flow_id": flow_id},
    )

    ext_type = (schema_row.get("extracted_schema_type") or "file").lower()
    if ext_type == "file":
        raise HTTPException(
            status_code=400,
            detail="Mapping catalog is not available for file-upload flows.",
        )

    raw_schema = schema_row.get("extracted_schema")
    if isinstance(raw_schema, str):
        extracted_schema = json.loads(raw_schema) if raw_schema else {}
    else:
        extracted_schema = raw_schema or {}

    source_fields: Set[str] = set(_flatten_extracted_schema_source_keys(extracted_schema))
    for fm in field_mapping_rows or []:
        row = dict(fm)
        fn = row.get("extracted_field_name")
        if fn:
            source_fields.add(str(fn))

    conn_id = schema_row.get("connector_id")
    if conn_id is not None:
        conn_id = str(conn_id)
    await _merge_connector_source_field_names(
        user_id,
        ext_type,
        conn_id,
        source_fields,
        source_table_or_object=source_meta_table,
    )

    dest_type = (flow_row.get("destination_type") or "salesforce").lower()
    org_id = flow_row.get("org_id")
    if org_id is not None:
        org_id = str(org_id)

    available_objects: Optional[Dict[str, Any]] = None
    available_sheets: Optional[Dict[str, Any]] = None
    available_tables: Optional[Dict[str, Any]] = None
    source_foreign_keys: Optional[List[Dict[str, Any]]] = None
    source_salesforce_lookups: Optional[List[Dict[str, Any]]] = None

    if dest_type == "salesforce" and org_id:
        sf_rows = await database.fetch_all(
            """
            SELECT object_api_name, field_api_name, data_type, field_description, is_required
            FROM salesforce_object_fields
            WHERE user_id = :user_id AND org_id = :org_id AND is_creatable = TRUE
            ORDER BY object_api_name, field_api_name
            """,
            {"user_id": str(user_id), "org_id": org_id},
        )
        available_objects = {}
        for r in sf_rows or []:
            row = dict(r)
            obj = row["object_api_name"]
            if obj not in available_objects:
                available_objects[obj] = {}
            available_objects[obj][row["field_api_name"]] = {
                "type": row["data_type"] or "",
                "description": row.get("field_description") or "",
                "is_required": row.get("is_required"),
            }
    elif dest_type == "google_sheets" and org_id:
        sh_rows = await database.fetch_all(
            """
            SELECT sheet_name, header_schema FROM gsheets_extracted_schemas
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
            """,
            {"user_id": str(user_id), "spreadsheet_id": org_id},
        )
        available_sheets = {}
        for row in sh_rows or []:
            sn = row["sheet_name"]
            cols = _parse_gsheets_header_to_columns(row["header_schema"])
            if cols:
                available_sheets[sn] = {"columns": cols}
    elif dest_type == "sql" and org_id:
        tbl_rows = await database.fetch_all(
            """
            SELECT table_name, schema_data FROM sql_extracted_schemas
            WHERE user_id = :user_id AND connector_id = :connector_id
            """,
            {"user_id": str(user_id), "connector_id": org_id},
        )
        available_tables = {}
        for row in tbl_rows or []:
            tbl = row["table_name"]
            sd = row["schema_data"]
            cols = _sql_schema_data_to_columns(sd)
            fks = await _sql_foreign_keys_with_runtime_fallback(
                user_id, org_id, str(tbl), sd
            )
            if cols:
                available_tables[tbl] = {"columns": cols, "foreign_keys": fks}

    # Source-side relationship metadata (for SQL sources) so mapping UI/analysis can reason about lookup_of.
    if ext_type == "sql":
        source_foreign_keys = []
        if conn_id and source_meta_table:
            src_schema_row = await database.fetch_one(
                """
                SELECT schema_data FROM sql_extracted_schemas
                WHERE user_id = :uid AND connector_id = :cid
                  AND LOWER(TRIM(table_name::text)) = LOWER(TRIM(:tbl))
                ORDER BY extracted_at DESC NULLS LAST
                LIMIT 1
                """,
                {"uid": str(user_id), "cid": conn_id, "tbl": source_meta_table},
            )
            src_sd = dict(src_schema_row).get("schema_data") if src_schema_row else None
            source_foreign_keys = await _sql_foreign_keys_with_runtime_fallback(
                user_id, conn_id, source_meta_table, src_sd
            )

    # Source-side lookup fields (Salesforce → … flows) for lookup_of suggestions: BaseObject.LookupFieldApiName.
    if ext_type == "salesforce":
        source_salesforce_lookups = []
        if conn_id and source_meta_table:
            org_id_sf_src = await _resolve_salesforce_source_org_id(user_id, str(conn_id))
            if org_id_sf_src:
                lk_rows = await database.fetch_all(
                    """
                    SELECT field_api_name, lookup_object_api_name, relationship_name
                    FROM salesforce_object_fields
                    WHERE user_id = :user_id AND org_id = :org_id
                      AND LOWER(TRIM(object_api_name::text)) = LOWER(TRIM(:obj))
                      AND COALESCE(is_lookup, false) = true
                      AND lookup_object_api_name IS NOT NULL
                      AND TRIM(lookup_object_api_name::text) != ''
                    ORDER BY field_api_name
                    """,
                    {
                        "user_id": str(user_id),
                        "org_id": org_id_sf_src,
                        "obj": str(source_meta_table).strip(),
                    },
                )
                for r in lk_rows or []:
                    d = dict(r)
                    source_salesforce_lookups.append(
                        {
                            "field_api_name": d.get("field_api_name"),
                            "lookup_object_api_name": d.get("lookup_object_api_name"),
                            "relationship_name": d.get("relationship_name"),
                        }
                    )

    return {
        "status": "success",
        "source_fields": sorted(source_fields, key=lambda x: x.lower()),
        "available_objects": available_objects,
        "available_sheets": available_sheets,
        "available_tables": available_tables,
        "source_foreign_keys": source_foreign_keys,
        "source_salesforce_lookups": source_salesforce_lookups,
        "source_sql_query": source_sql_q,
        "source_object_or_table": source_meta_table if isinstance(source_meta_table, str) else None,
    }


async def _resolve_gsheets_schema_connector_id(user_id: UUID, ref: str) -> str:
    """
    Map UI value (spreadsheet_id or user_google_spreadsheets row UUID) to the id stored in
    extracted_schemas.connector_id (user_google_spreadsheets.id).
    """
    ref = (ref or "").strip()
    if not ref:
        raise HTTPException(status_code=400, detail="Google Sheets source id is required")
    try:
        u = UUID(ref)
        row = await database.fetch_one(
            """
            SELECT id FROM user_google_spreadsheets
            WHERE user_id = :user_id AND id = :row_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "row_id": str(u)},
        )
        if row:
            return str(row["id"])
    except (ValueError, TypeError):
        pass
    row = await database.fetch_one(
        """
        SELECT id FROM user_google_spreadsheets
        WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
        ORDER BY last_accessed_at DESC NULLS LAST
        LIMIT 1
        """,
        {"user_id": str(user_id), "spreadsheet_id": ref},
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Google Sheet not found for this account",
        )
    return str(row["id"])


async def _resolve_flow_source_connector_id(user_id: UUID, source_type: str, source_id: str) -> str:
    st = (source_type or "file").lower()
    sid = (source_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="source_id is required")
    if st == "sql":
        row = await database.fetch_one(
            """
            SELECT id FROM sql_connectors
            WHERE user_id = :user_id AND id = :id
            LIMIT 1
            """,
            {"user_id": str(user_id), "id": sid},
        )
        if not row:
            raise HTTPException(status_code=404, detail="SQL connector not found")
        return str(row["id"])
    if st == "gsheets":
        return await _resolve_gsheets_schema_connector_id(user_id, sid)
    if st == "salesforce":
        try:
            tu = UUID(sid)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid Salesforce connection id")
        row = await database.fetch_one(
            """
            SELECT id FROM salesforce_tokens
            WHERE user_id = :user_id AND id = :token_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "token_id": str(tu)},
        )
        if not row:
            raise HTTPException(status_code=404, detail="Salesforce connection not found")
        return str(row["id"])
    raise HTTPException(status_code=400, detail="Cannot update source for this flow type")


async def _resolve_flow_destination_org_id(user_id: UUID, destination_type: str, destination_id: str) -> str:
    dt = (destination_type or "salesforce").lower()
    did = (destination_id or "").strip()
    if not did:
        raise HTTPException(status_code=400, detail="destination_id is required")
    if dt == "salesforce":
        try:
            tu = UUID(did)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid Salesforce destination id")
        row = await database.fetch_one(
            """
            SELECT org_id FROM salesforce_tokens
            WHERE user_id = :user_id AND id = :token_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "token_id": str(tu)},
        )
        if not row:
            raise HTTPException(
                status_code=404,
                detail="Salesforce org not found for this connection",
            )
        rd = dict(row)
        oid = rd.get("org_id")
        if oid is None or (isinstance(oid, str) and not oid.strip()):
            raise HTTPException(
                status_code=404,
                detail="Salesforce org not found for this connection",
            )
        return str(oid)
    if dt == "google_sheets":
        row = await database.fetch_one(
            """
            SELECT spreadsheet_id FROM user_google_spreadsheets
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "spreadsheet_id": did},
        )
        if not row:
            raise HTTPException(
                status_code=404,
                detail="Google Sheet not found for this account",
            )
        return str(row["spreadsheet_id"])
    if dt == "sql":
        row = await database.fetch_one(
            """
            SELECT id FROM sql_connectors
            WHERE user_id = :user_id AND id = :id
            LIMIT 1
            """,
            {"user_id": str(user_id), "id": did},
        )
        if not row:
            raise HTTPException(status_code=404, detail="SQL connector not found")
        return str(row["id"])
    raise HTTPException(status_code=400, detail="Unsupported destination type")


async def _validate_soql_against_synced_salesforce_fields(
    user_id: UUID,
    org_id: str,
    soql: str,
) -> List[str]:
    """Ensure SOQL field references exist in salesforce_object_fields for this org."""
    from utils.soql_parse import parse_soql_object_and_field_candidates
    from utils.soql_salesforce_field_validation import (
        validate_soql_field_paths_against_synced_metadata,
    )

    obj, candidates = parse_soql_object_and_field_candidates(soql)
    if not obj or not candidates:
        return []

    rows = await database.fetch_all(
        """
        SELECT field_api_name FROM salesforce_object_fields
        WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
        LIMIT 1
        """,
        {"user_id": str(user_id), "org_id": org_id, "obj": obj},
    )
    if not rows:
        return [
            f"No synced field metadata for Salesforce object '{obj}' in this org. "
            "Open Connections, select this Salesforce org, and sync the source so fields are refreshed."
        ]

    missing = await validate_soql_field_paths_against_synced_metadata(
        user_id, org_id, obj, candidates
    )
    if not missing:
        return []
    preview = ", ".join(missing[:8])
    if len(missing) > 8:
        preview += ", …"
    return [
        f"SOQL references field(s) not found in synced metadata for {obj}: {preview}. "
        "Open Connections, select this Salesforce org, and sync the source so fields are refreshed."
    ]


async def _sql_query_smoke_test(user_id: str, connector_id: str, sql_query: Optional[str]) -> Optional[Dict[str, Any]]:
    """Execute a limited query on the connector; return None if OK, else a dict with message + optional AI fields."""
    from services.error_explanation_service import explain_error

    if not sql_query or not str(sql_query).strip():
        return {
            "message": "No SQL query is saved for this flow; cannot verify extraction on the new database.",
        }
    try:
        from services.sql_connector_service import SQLConnectorService

        svc = SQLConnectorService()
        data = await svc.get_connector_by_id(connector_id, str(user_id))
        if not data or not data.get("connector"):
            return {"message": "SQL connector not found or inactive."}
        conn_dict = dict(data["connector"])
        cfg = svc.prepare_connector_config(conn_dict)
        inst = svc.create_connector_instance(conn_dict["connector_type"], cfg)
        q = str(sql_query).strip().rstrip(";")
        if not re.search(r"\blimit\s+\d+", q, re.IGNORECASE):
            q = f"{q} LIMIT 1"
        try:
            await inst.execute_query(q)
        finally:
            await inst.close_connection()
        return None
    except Exception as e:
        raw = str(e)
        short = raw[:450]
        msg = f"SQL query failed on the new connection: {short}"
        explained = await explain_error(
            raw,
            "sql",
            context={
                "operation": "LIMIT 1 smoke test",
                "user_query": str(sql_query)[:500],
                "error_type": type(e).__name__,
            },
        )
        out: Dict[str, Any] = {"message": msg}
        if explained.get("explanation"):
            out["explanation"] = explained["explanation"]
        if explained.get("solution"):
            out["solution"] = explained["solution"]
        return out


async def _soql_smoke_test(user_id: str, token_id: str, soql: str) -> Optional[Dict[str, Any]]:
    """Run SOQL with LIMIT 1 against the token's org; return None if OK, else error dict."""
    from services.error_explanation_service import explain_error
    from handlers.salesforce_handler import _execute_salesforce_query
    from utils.soql_parse import parse_soql_object_and_field_candidates
    from utils.salesforceapimigration import get_valid_salesforce_token

    try:
        row = await database.fetch_one(
            """
            SELECT org_id FROM salesforce_tokens
            WHERE user_id = :user_id AND id = :token_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "token_id": token_id},
        )
        if not row:
            return {"message": "Salesforce connection not found."}
        org_id = dict(row).get("org_id")
        if not org_id:
            return {"message": "Salesforce org is missing for this connection."}
        creds = await get_valid_salesforce_token(str(user_id), org_id=str(org_id))
        q = str(soql).strip()
        if not re.search(r"\blimit\s+\d+", q, re.IGNORECASE):
            q = f"{q} LIMIT 1"
        await _execute_salesforce_query(
            creds["access_token"],
            creds["instance_url"],
            q,
        )
        return None
    except Exception as e:
        raw = str(e)
        short = raw[:450]
        msg = f"SOQL failed against the new Salesforce org: {short}"
        obj_guess = None
        try:
            obj_guess, _ = parse_soql_object_and_field_candidates(soql)
        except Exception:
            pass
        ctx: Dict[str, Any] = {
            "operation": "LIMIT 1 SOQL smoke test",
            "user_query": str(soql)[:500],
            "error_type": type(e).__name__,
        }
        if obj_guess:
            ctx["object_name"] = obj_guess
        explained = await explain_error(raw, "salesforce", context=ctx)
        out: Dict[str, Any] = {"message": msg}
        if explained.get("explanation"):
            out["explanation"] = explained["explanation"]
        if explained.get("solution"):
            out["solution"] = explained["solution"]
        return out


def _http_400_from_query_smoke_error(smoke: Dict[str, Any]) -> HTTPException:
    """
    Map _sql_query_smoke_test / _soql_smoke_test output (message + optional explain_error fields)
    into a FastAPI detail the UI can show in plain language.
    """
    msg = (smoke.get("message") or "").strip() or "Query validation failed."
    expl = (smoke.get("explanation") or "").strip() or None
    sol = (smoke.get("solution") or "").strip() or None
    if expl or sol:
        return HTTPException(
            status_code=400,
            detail={
                "message": expl or msg,
                "technical": msg if expl else None,
                "solution": sol,
            },
        )
    return HTTPException(status_code=400, detail=msg)


def _append_validation_exec_error(
    errors: List[str],
    error_details: List[Dict[str, Any]],
    err: Optional[Dict[str, Any]],
) -> None:
    if not err or not err.get("message"):
        return
    errors.append(str(err["message"]))
    if err.get("explanation") or err.get("solution"):
        error_details.append(
            {
                "message": str(err["message"]),
                "explanation": err.get("explanation"),
                "solution": err.get("solution"),
            }
        )


async def _gsheets_synced_header_names_for_tab(
    user_id: UUID,
    connector_ref: str,
    sheet_tab_name: str,
) -> Tuple[Optional[str], Set[str]]:
    """
    Column names from gsheets_extracted_schemas.header_schema for this spreadsheet + tab (same row
    shape as destination Google Sheets validation). Returns (error_message, names): if error_message
    is set, callers should surface it; otherwise names is the set of header `name` values to validate against.
    """
    st = (sheet_tab_name or "").strip()
    if not st:
        return ("Cannot validate Google Sheets source: sheet tab name is missing.", set())

    uid = str(user_id)
    cref = (connector_ref or "").strip()
    if not cref:
        return ("Cannot validate Google Sheets source: connector is missing.", set())

    spreadsheet_id: Optional[str] = None
    try:
        UUID(str(cref))
        ugs_row = await database.fetch_one(
            """
            SELECT spreadsheet_id FROM user_google_spreadsheets
            WHERE user_id = :user_id AND id = :cid
            LIMIT 1
            """,
            {"user_id": uid, "cid": cref},
        )
        if ugs_row:
            sid = dict(ugs_row).get("spreadsheet_id")
            if sid:
                spreadsheet_id = str(sid)
    except (ValueError, TypeError):
        pass
    if not spreadsheet_id:
        spreadsheet_id = cref

    grow = await database.fetch_one(
        """
        SELECT header_schema FROM gsheets_extracted_schemas
        WHERE user_id = :user_id AND spreadsheet_id = :sid
          AND sheet_name = :sn
        LIMIT 1
        """,
        {"user_id": uid, "sid": spreadsheet_id, "sn": st},
    )
    if not grow:
        return (
            f"We could not load column headers for Google Sheet tab \"{st}\" for this spreadsheet. "
            "On the Connections page, open this spreadsheet so headers sync, then try saving again.",
            set(),
        )

    cols = _parse_gsheets_header_to_columns(dict(grow).get("header_schema"))
    names = set(cols.keys())
    if not names:
        return (
            f"Column headers for sheet tab \"{st}\" are missing or invalid in our records. "
            "On the Connections page, refresh or re-open this spreadsheet, then try again.",
            set(),
        )
    return (None, names)


async def _validate_flow_connection_changes(
    user_id: UUID,
    flow_id: str,
    schema_data: Dict[str, Any],
    flow_data: Dict[str, Any],
    ext_type: str,
    dest_type: str,
    update_source: bool,
    update_dest: bool,
    resolved_connector: Optional[str],
    resolved_org: Optional[str],
) -> Dict[str, Any]:
    """
    Validate existing field mappings + synced metadata + SQL/SOQL smoke tests when connections change.
    For Google Sheets sources, mapped source fields must appear in gsheets_extracted_schemas.header_schema
    for the selected tab. Returns {"errors": [...], "error_details": [...]} — error_details holds optional explanation/solution for execution failures.
    """
    errors: List[str] = []
    error_details: List[Dict[str, Any]] = []
    fm_rows = await database.fetch_all(
        """
        SELECT extracted_field_name, salesforce_object, salesforce_field
        FROM field_mappings
        WHERE migration_flow_id = :flow_id
        ORDER BY created_at
        """,
        {"flow_id": flow_id},
    )
    if not fm_rows:
        return {
            "errors": ["This flow has no field mappings; add mappings before changing connections."],
            "error_details": [],
        }

    raw_schema = schema_data.get("extracted_schema")
    if isinstance(raw_schema, str):
        try:
            extracted_schema = json.loads(raw_schema)
        except json.JSONDecodeError:
            extracted_schema = {}
    else:
        extracted_schema = raw_schema or {}

    eff_connector = (
        str(resolved_connector) if update_source and resolved_connector else str(schema_data.get("connector_id") or "")
    )
    eff_org = str(resolved_org) if update_dest and resolved_org else str(flow_data.get("org_id") or "")
    eff_table = schema_data.get("table_name")
    if update_source and ext_type == "gsheets" and resolved_connector:
        tab = await _lookup_gsheets_sheet_tab_name(user_id, resolved_connector)
        if tab:
            eff_table = tab

    if update_source:
        # Google Sheets: only validate mapped source fields against gsheets_extracted_schemas.header_schema
        # for the selected tab (see _gsheets_synced_header_names_for_tab). The generic allowed + merge
        # loop would seed mapped names and mislead; SQL/Salesforce keep merge + extra checks below.
        if ext_type == "gsheets":
            if not (eff_connector or "").strip():
                errors.append("Cannot validate Google Sheets source: connector is missing.")
            else:
                tab_label = (eff_table or "").strip() or "selected sheet"
                hdr_err, header_names = await _gsheets_synced_header_names_for_tab(
                    user_id, eff_connector, (eff_table or "").strip()
                )
                if hdr_err:
                    errors.append(hdr_err)
                else:
                    for r in fm_rows or []:
                        rr = dict(r)
                        src = (rr.get("extracted_field_name") or "").strip()
                        if not src:
                            continue
                        if src not in header_names:
                            errors.append(
                                f"This flow still expects source column \"{src}\", but that column is not a header on "
                                f"Google Sheet tab \"{tab_label}\" for the source you selected. "
                                "On the Connections page, open the spreadsheet to sync the latest headers, or choose "
                                "another sheet or tab whose columns match this flow."
                            )
        else:
            allowed: Set[str] = set(_flatten_extracted_schema_source_keys(extracted_schema))
            for r in fm_rows or []:
                rr = dict(r)
                fn = rr.get("extracted_field_name")
                if fn:
                    allowed.add(str(fn))
            await _merge_connector_source_field_names(
                user_id,
                ext_type,
                eff_connector or None,
                allowed,
                source_table_or_object=eff_table,
            )
            for r in fm_rows or []:
                rr = dict(r)
                src = (rr.get("extracted_field_name") or "").strip()
                if not src:
                    continue
                if src not in allowed:
                    errors.append(
                        f"Mapped source column \"{src}\" is not available on the selected source connection "
                        "(it may be missing from synced metadata or the saved schema). "
                        "Refresh the source on the Connections page, or choose a different source that provides this column."
                    )

            sql_source_mapping_failed = False
            if ext_type == "sql" and eff_connector:
                # field_mappings.salesforce_object / salesforce_field store the *destination* target
                # (Salesforce object.api for SF dest, SQL table.column for SQL dest). For source validation
                # against sql_extracted_schemas we must use the flow's source table (extracted_schemas.table_name)
                # and source-side keys (extracted_field_name), not Contact/Account-style destination objects.
                src_tbl = (eff_table or "").strip()
                if src_tbl:
                    srow = await database.fetch_one(
                        """
                        SELECT schema_data FROM sql_extracted_schemas
                        WHERE user_id = :user_id AND connector_id = :cid
                          AND LOWER(table_name::text) = LOWER(:tbl)
                        LIMIT 1
                        """,
                        {"user_id": str(user_id), "cid": eff_connector, "tbl": src_tbl},
                    )
                    if not srow:
                        errors.append(
                            f"Source table '{src_tbl}' was not found in schema cache for the selected SQL connection. "
                            "Sync or refresh schema for this connector on the Connections page."
                        )
                        sql_source_mapping_failed = True
                    else:
                        cols = _sql_schema_data_to_columns(dict(srow).get("schema_data"))
                        col_lower = {str(k).lower(): k for k in cols}
                        for r in fm_rows or []:
                            rr = dict(r)
                            src_col = (rr.get("extracted_field_name") or "").strip()
                            if not src_col:
                                continue
                            if src_col.lower() not in col_lower:
                                errors.append(
                                    f"Source column '{src_col}' was not found for table '{src_tbl}' in synced SQL schema. "
                                    "Refresh schema for this connector on the Connections page."
                                )
                                sql_source_mapping_failed = True

                sq_sql = schema_data.get("sql_query")
                if sq_sql is not None and not isinstance(sq_sql, str):
                    sq_sql = str(sq_sql)
                if not sql_source_mapping_failed:
                    err = await _sql_query_smoke_test(str(user_id), eff_connector, sq_sql)
                    _append_validation_exec_error(errors, error_details, err)

            if ext_type == "salesforce" and eff_connector:
                sq_sf = schema_data.get("sql_query")
                if sq_sf is not None and not isinstance(sq_sf, str):
                    sq_sf = str(sq_sf)
                if not sq_sf or not str(sq_sf).strip():
                    errors.append("No SOQL query is stored for this flow; cannot verify Salesforce extraction.")
                else:
                    src_org = await _resolve_salesforce_source_org_id(user_id, eff_connector)
                    meta_errs: List[str] = []
                    if src_org:
                        meta_errs = await _validate_soql_against_synced_salesforce_fields(
                            user_id, src_org, str(sq_sf)
                        )
                    for me in meta_errs:
                        errors.append(me)
                    if not meta_errs:
                        err = await _soql_smoke_test(str(user_id), eff_connector, str(sq_sf))
                        _append_validation_exec_error(errors, error_details, err)

    if update_dest:
        if dest_type == "salesforce" and eff_org:
            for r in fm_rows or []:
                rr = dict(r)
                obj = (rr.get("salesforce_object") or "").strip()
                fld = (rr.get("salesforce_field") or "").strip()
                if not obj or not fld:
                    continue
                ok = await database.fetch_one(
                    """
                    SELECT 1 FROM salesforce_object_fields
                    WHERE user_id = :user_id AND org_id = :org_id
                      AND object_api_name = :obj AND field_api_name = :field
                    LIMIT 1
                    """,
                    {
                        "user_id": str(user_id),
                        "org_id": eff_org,
                        "obj": obj,
                        "field": fld,
                    },
                )
                if not ok:
                    errors.append(
                        f"Destination field {obj}.{fld} is not available in the selected Salesforce org."
                    )
        elif dest_type == "sql" and eff_org:
            for r in fm_rows or []:
                rr = dict(r)
                tbl = (rr.get("salesforce_object") or "").strip()
                fld = (rr.get("salesforce_field") or "").strip()
                if not tbl or not fld:
                    continue
                srow = await database.fetch_one(
                    """
                    SELECT schema_data FROM sql_extracted_schemas
                    WHERE user_id = :user_id AND connector_id = :cid
                      AND LOWER(table_name::text) = LOWER(:tbl)
                    LIMIT 1
                    """,
                    {"user_id": str(user_id), "cid": eff_org, "tbl": tbl},
                )
                if not srow:
                    errors.append(
                        f"Destination table '{tbl}' was not found in schema cache for the selected SQL connection."
                    )
                    continue
                cols = _sql_schema_data_to_columns(dict(srow).get("schema_data"))
                if fld not in cols:
                    errors.append(
                        f"Destination column '{fld}' not found for table '{tbl}' on the selected SQL connection."
                    )
        elif dest_type == "google_sheets" and eff_org:
            for r in fm_rows or []:
                rr = dict(r)
                sheet_name = (rr.get("salesforce_object") or "").strip()
                fld = (rr.get("salesforce_field") or "").strip()
                if not sheet_name or not fld:
                    continue
                grow = await database.fetch_one(
                    """
                    SELECT header_schema FROM gsheets_extracted_schemas
                    WHERE user_id = :user_id AND spreadsheet_id = :sid
                      AND sheet_name = :sn
                    LIMIT 1
                    """,
                    {"user_id": str(user_id), "sid": eff_org, "sn": sheet_name},
                )
                if not grow:
                    errors.append(
                        f"Destination sheet '{sheet_name}' was not found in synced metadata for this spreadsheet."
                    )
                    continue
                cols = _parse_gsheets_header_to_columns(dict(grow).get("header_schema"))
                if fld not in cols:
                    errors.append(
                        f"Destination column '{fld}' was not found on sheet '{sheet_name}' in synced metadata."
                    )

    # Dedupe while preserving order
    seen: Set[str] = set()
    out: List[str] = []
    for e in errors:
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    seen_detail_msg: Set[str] = set()
    detail_out: List[Dict[str, Any]] = []
    for d in error_details:
        m = d.get("message")
        if m and m not in seen_detail_msg:
            seen_detail_msg.add(str(m))
            detail_out.append(d)
    return {"errors": out, "error_details": detail_out}


def _source_field_allowed_for_migration_rules(name: str, allowed: Set[str]) -> bool:
    fn = (name or "").strip()
    if not fn:
        return True
    if fn in allowed:
        return True
    fl = fn.lower()
    for a in allowed:
        if isinstance(a, str) and a.lower() == fl:
            return True
    return False


def _normalize_extraction_transformation_rules(tr: Any) -> Optional[Dict[str, Any]]:
    """Coerce legacy list-shaped extraction transformation_rules to { \"fields\": [...] }."""
    if tr is None:
        return None
    if isinstance(tr, list):
        return {"fields": list(tr)}
    if isinstance(tr, dict):
        fields = tr.get("fields")
        if isinstance(fields, list):
            return tr
        return None
    return None


def _migration_rules_has_actionable_source_refs(migration_rules: Any) -> bool:
    """True if rules reference source field names we can validate against the current source."""
    if not isinstance(migration_rules, dict):
        return False
    ex = migration_rules.get("extraction_rules")
    if isinstance(ex, dict):
        fr = ex.get("filter_rules")
        if isinstance(fr, dict):
            conds = fr.get("conditions")
            if isinstance(conds, list) and len(conds) > 0:
                return True
        dr = ex.get("deduplication_rules")
        if isinstance(dr, dict):
            fields = dr.get("fields")
            if isinstance(fields, list) and len(fields) > 0:
                return True
        tr_norm = _normalize_extraction_transformation_rules(ex.get("transformation_rules"))
        if tr_norm:
            fields = tr_norm.get("fields")
            if isinstance(fields, list) and len(fields) > 0:
                return True
        dc = ex.get("data_cleanup_rules")
        if isinstance(dc, dict) and len(dc) > 0:
            return True
    from utils.migration_rules_utils import get_mapping_transformation

    tr2 = get_mapping_transformation(migration_rules)
    if isinstance(tr2, dict):
        fields = tr2.get("fields")
        if isinstance(fields, list) and len(fields) > 0:
            return True
    return False


def _issue_migration_rule(
    issues: List[Dict[str, Any]],
    *,
    code: str,
    message: str,
    section: str,
    issue_kind: str,
    field: Optional[str] = None,
    rule_id: Optional[str] = None,
    action: Optional[str] = None,
    operation_type: Optional[str] = None,
    rule_row: Optional[Dict[str, Any]] = None,
    condition_index: Optional[int] = None,
    condition_snapshot: Optional[Dict[str, Any]] = None,
    operator: Optional[str] = None,
) -> None:
    row: Dict[str, Any] = {
        "code": code,
        "message": message,
        "section": section,
        "issue_kind": issue_kind,
    }
    if field is not None:
        row["field"] = field
    if rule_id:
        row["rule_id"] = rule_id
    if action:
        row["action"] = action
    if operator:
        row["operator"] = operator
    if operation_type:
        row["operation_type"] = operation_type
    if rule_row is not None:
        row["rule_row"] = rule_row
    if condition_index is not None:
        row["condition_index"] = condition_index
    if condition_snapshot is not None:
        row["condition_snapshot"] = condition_snapshot
    issues.append(row)


def _scan_rule_transformation_fields_for_source_issues(
    fields: Any,
    allowed: Set[str],
    section_label: str,
    issues: List[Dict[str, Any]],
    *,
    issue_kind: str,
    operation_type: str,
) -> None:
    if not isinstance(fields, list):
        return
    for item in fields:
        if not isinstance(item, dict):
            continue
        rule_id = item.get("rule_id")
        rid = str(rule_id) if rule_id else None
        action_raw = item.get("action")
        action = str(action_raw or "").lower()
        if action in (
            "exclude",
            "keep",
            "drop",
            "merge",
            "combine",
        ) or action.startswith("split_") or action in ("split", "split_address", "prepend", "append"):
            names: List[str] = []
            if action in ("merge", "combine"):
                from utils.rule_accumulation import _merge_source_fields_from_rule

                names = _merge_source_fields_from_rule(item)
            elif action in ("prepend", "append"):
                sf = item.get("source_field") or item.get("field")
                if isinstance(sf, str) and sf.strip():
                    names = [sf.strip()]
                else:
                    srcs = item.get("source_fields")
                    if isinstance(srcs, list) and len(srcs) == 1:
                        x = srcs[0]
                        if x is not None and str(x).strip():
                            names = [str(x).strip()]
            else:
                sf = item.get("source_field")
                if isinstance(sf, list):
                    names = [str(x).strip() for x in sf if x is not None and str(x).strip()]
                elif sf is not None and str(sf).strip():
                    names = [str(sf).strip()]
            for n in names:
                if not _source_field_allowed_for_migration_rules(n, allowed):
                    _issue_migration_rule(
                        issues,
                        code="unknown_source_field",
                        message=(
                            f"{section_label}: source field '{n}' is not on the current source (stored schema "
                            "and synced metadata). Update or remove this rule."
                        ),
                        section=section_label,
                        issue_kind=issue_kind,
                        field=n,
                        rule_id=rid,
                        action=str(action_raw) if action_raw is not None else None,
                        operation_type=operation_type,
                        rule_row=dict(item),
                    )


def _collect_migration_rules_source_field_issues(
    migration_rules: Dict[str, Any],
    allowed: Set[str],
) -> List[Dict[str, Any]]:
    from utils.migration_rules_utils import get_mapping_transformation

    issues: List[Dict[str, Any]] = []
    ex = migration_rules.get("extraction_rules")
    if isinstance(ex, dict):
        fr = ex.get("filter_rules")
        if isinstance(fr, dict):
            conds = fr.get("conditions")
            if isinstance(conds, list):
                for i, cond in enumerate(conds):
                    if not isinstance(cond, dict):
                        continue
                    fn = cond.get("field")
                    if fn is None or not str(fn).strip():
                        continue
                    n = str(fn).strip()
                    if not _source_field_allowed_for_migration_rules(n, allowed):
                        op = str(cond.get("operator") or "")
                        _issue_migration_rule(
                            issues,
                            code="unknown_source_field",
                            message=f"Filter (condition {i + 1}): field '{n}' is not on the current source.",
                            section="filter_rules",
                            issue_kind="filter",
                            field=n,
                            condition_index=i,
                            condition_snapshot=dict(cond),
                            operator=op or None,
                        )
        dr = ex.get("deduplication_rules")
        if isinstance(dr, dict):
            fields = dr.get("fields")
            if isinstance(fields, list):
                for n_raw in fields:
                    if n_raw is None or not str(n_raw).strip():
                        continue
                    n = str(n_raw).strip()
                    if not _source_field_allowed_for_migration_rules(n, allowed):
                        _issue_migration_rule(
                            issues,
                            code="unknown_source_field",
                            message=f"Deduplication: field '{n}' is not on the current source.",
                            section="deduplication_rules",
                            issue_kind="deduplication",
                            field=n,
                        )
        tr_norm = _normalize_extraction_transformation_rules(ex.get("transformation_rules"))
        _scan_rule_transformation_fields_for_source_issues(
            tr_norm.get("fields") if isinstance(tr_norm, dict) else None,
            allowed,
            "Extraction field rules",
            issues,
            issue_kind="extraction_transformation",
            operation_type="extraction",
        )
        dc = ex.get("data_cleanup_rules")
        if isinstance(dc, dict):
            for field_key in dc.keys():
                n = str(field_key).strip() if field_key is not None else ""
                if not n:
                    continue
                if not _source_field_allowed_for_migration_rules(n, allowed):
                    _issue_migration_rule(
                        issues,
                        code="unknown_source_field",
                        message=(
                            f"Data cleanup: field '{n}' is not on the current source (stored schema "
                            "and synced metadata). Update or remove this cleanup rule."
                        ),
                        section="data_cleanup_rules",
                        issue_kind="data_cleanup",
                        field=n,
                    )
    tr_top = get_mapping_transformation(migration_rules)
    if isinstance(tr_top, dict):
        _scan_rule_transformation_fields_for_source_issues(
            tr_top.get("fields"),
            allowed,
            "Mapping field rules",
            issues,
            issue_kind="mapping_transformation",
            operation_type="mapping",
        )
    return issues


async def _allowed_source_fields_for_flow_migration_validation(
    user_id: UUID,
    flow_id: str,
) -> Tuple[Optional[str], str, Set[str]]:
    """
    Returns (error_message, source_kind, allowed_field_names).
    error_message set => HTTP error; source_kind is 'file' | 'connector' for response metadata.
    """
    flow_result = await database.fetch_one(
        """
        SELECT id FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not flow_result:
        return ("Flow not found", "none", set())

    schema_result = await database.fetch_one(
        """
        SELECT extracted_schema, extracted_schema_type, connector_id, table_name
        FROM extracted_schemas
        WHERE migration_flow_id = :flow_id
        """,
        {"flow_id": flow_id},
    )
    if not schema_result:
        return ("No extracted schema for this flow", "none", set())

    schema_row = dict(schema_result)
    ext_type = (schema_row.get("extracted_schema_type") or "file").lower()
    if ext_type == "file":
        return (None, "file", set())

    field_mapping_rows = await database.fetch_all(
        """
        SELECT extracted_field_name FROM field_mappings
        WHERE migration_flow_id = :flow_id
        """,
        {"flow_id": flow_id},
    )

    raw_schema = schema_row.get("extracted_schema")
    if isinstance(raw_schema, str):
        try:
            extracted_schema = json.loads(raw_schema) if raw_schema else {}
        except json.JSONDecodeError:
            extracted_schema = {}
    else:
        extracted_schema = raw_schema or {}

    source_fields: Set[str] = set(_flatten_extracted_schema_source_keys(extracted_schema))
    for fm in field_mapping_rows or []:
        row = dict(fm)
        fn = row.get("extracted_field_name")
        if fn:
            source_fields.add(str(fn))

    conn_id = schema_row.get("connector_id")
    if conn_id is not None:
        conn_id = str(conn_id)
    meta_table = schema_row.get("table_name")
    await _merge_connector_source_field_names(
        user_id,
        ext_type,
        conn_id,
        source_fields,
        source_table_or_object=meta_table if isinstance(meta_table, str) else None,
    )

    return (None, "connector", source_fields)


class UpdateFlowConnectionsRequest(BaseModel):
    """Wire ids from the edit UI: SQL connector id, Sheets spreadsheet_id, Salesforce token_id."""

    source_id: Optional[str] = None
    destination_id: Optional[str] = None


def _nonempty_str(s: Optional[str]) -> bool:
    return s is not None and str(s).strip() != ""


@router.patch("/flow/{flow_id}/connections")
async def update_flow_connections(
    flow_id: str,
    body: UpdateFlowConnectionsRequest,
    user_id: UUID = Depends(get_current_user),
):
    """
    Update source (extracted_schemas.connector_id) and/or destination (migration_flows.org_id)
    for a non-file flow. Types are unchanged; ids must match existing destination/source kinds.
    For gsheets source, extracted_schemas.table_name is set to the tab name (sheet_name).
    """
    if not _nonempty_str(body.source_id) and not _nonempty_str(body.destination_id):
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of source_id or destination_id",
        )

    flow_row = await database.fetch_one(
        """
        SELECT id, org_id, destination_type, incremental_cursor_field
        FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not flow_row:
        raise HTTPException(status_code=404, detail="Flow not found")

    schema_row = await database.fetch_one(
        """
        SELECT extracted_schema_type, connector_id, extracted_schema, table_name, sql_query
        FROM extracted_schemas
        WHERE migration_flow_id = :flow_id
        """,
        {"flow_id": flow_id},
    )
    if not schema_row:
        raise HTTPException(status_code=400, detail="No extracted schema for this flow")

    # databases.Record does not support .get(); normalize to dict
    schema_data = dict(schema_row)
    ext_type = (schema_data.get("extracted_schema_type") or "file").lower()
    if ext_type == "file":
        raise HTTPException(
            status_code=400,
            detail="Connection editing is not available for file-upload flows",
        )

    flow_data = dict(flow_row)
    dest_type = (flow_data.get("destination_type") or "salesforce").lower()

    update_source = _nonempty_str(body.source_id)
    update_dest = _nonempty_str(body.destination_id)

    resolved_connector: Optional[str] = None
    resolved_org: Optional[str] = None

    if update_source:
        resolved_connector = await _resolve_flow_source_connector_id(
            user_id, ext_type, str(body.source_id).strip()
        )
    if update_dest:
        resolved_org = await _resolve_flow_destination_org_id(
            user_id, dest_type, str(body.destination_id).strip()
        )

    cur_connector = schema_data.get("connector_id")
    if cur_connector is not None:
        cur_connector = str(cur_connector)
    cur_org = flow_data.get("org_id")
    if cur_org is not None:
        cur_org = str(cur_org)

    changed = False
    if update_source and resolved_connector != cur_connector:
        changed = True
    if update_dest and resolved_org != cur_org:
        changed = True

    if not changed:
        return {
            "status": "success",
            "unchanged": True,
            "flow_id": flow_id,
        }

    val_result = await _validate_flow_connection_changes(
        user_id,
        flow_id,
        schema_data,
        flow_data,
        ext_type,
        dest_type,
        update_source,
        update_dest,
        resolved_connector,
        resolved_org,
    )
    if val_result.get("errors"):
        detail_body: Dict[str, Any] = {
            "status": "error",
            "errors": val_result["errors"],
            "message": "Connection change validation failed. Fix the issues below or update field mappings.",
        }
        if val_result.get("error_details"):
            detail_body["error_details"] = val_result["error_details"]
        raise HTTPException(status_code=400, detail=detail_body)

    now = datetime.utcnow()
    async with database.transaction():
        if update_dest and resolved_org != cur_org:
            await database.execute(
                """
                UPDATE migration_flows
                SET org_id = :org_id, updated_at = :updated_at
                WHERE id = :flow_id AND user_id = :user_id
                """,
                {
                    "org_id": resolved_org,
                    "updated_at": now,
                    "flow_id": flow_id,
                    "user_id": str(user_id),
                },
            )
        if update_source and resolved_connector != cur_connector:
            if ext_type == "gsheets":
                sheet_label = await _lookup_gsheets_sheet_tab_name(user_id, resolved_connector)
                if sheet_label is not None:
                    await database.execute(
                        """
                        UPDATE extracted_schemas
                        SET connector_id = :connector_id, table_name = :table_name
                        WHERE migration_flow_id = :flow_id
                        """,
                        {
                            "connector_id": resolved_connector,
                            "table_name": sheet_label,
                            "flow_id": flow_id,
                        },
                    )
                else:
                    await database.execute(
                        """
                        UPDATE extracted_schemas
                        SET connector_id = :connector_id
                        WHERE migration_flow_id = :flow_id
                        """,
                        {"connector_id": resolved_connector, "flow_id": flow_id},
                    )
            else:
                await database.execute(
                    """
                    UPDATE extracted_schemas
                    SET connector_id = :connector_id
                    WHERE migration_flow_id = :flow_id
                    """,
                    {"connector_id": resolved_connector, "flow_id": flow_id},
                )

        # Source-only changes update extracted_schemas only; still bump flow row for "last updated".
        source_changed = update_source and resolved_connector != cur_connector
        dest_changed = update_dest and resolved_org != cur_org
        if source_changed and ext_type == "salesforce":
            source_object = (schema_data.get("table_name") or "").strip()
            token_row = await database.fetch_one(
                """
                SELECT org_id::text AS org_id
                FROM salesforce_tokens
                WHERE id::text = :tid AND user_id = :uid
                LIMIT 1
                """,
                {"tid": str(resolved_connector), "uid": str(user_id)},
            )
            src_org = str(dict(token_row).get("org_id") or "").strip() if token_row else ""
            if src_org and source_object:
                current_cursor = (flow_data.get("incremental_cursor_field") or "").strip()
                resolved_cursor: Optional[str] = None
                if current_cursor:
                    cur_row = await database.fetch_one(
                        """
                        SELECT field_api_name, data_type
                        FROM salesforce_object_fields
                        WHERE user_id = :uid AND org_id = :org AND object_api_name = :obj
                          AND LOWER(field_api_name) = LOWER(:field)
                        LIMIT 1
                        """,
                        {
                            "uid": str(user_id),
                            "org": src_org,
                            "obj": source_object,
                            "field": current_cursor,
                        },
                    )
                    if cur_row:
                        cdict = dict(cur_row)
                        c_api = (cdict.get("field_api_name") or "").strip()
                        c_type = (cdict.get("data_type") or "").strip()
                        if c_api and _salesforce_type_is_incremental_cursor(c_type):
                            resolved_cursor = c_api
                if not resolved_cursor:
                    lm_row = await database.fetch_one(
                        """
                        SELECT field_api_name
                        FROM salesforce_object_fields
                        WHERE user_id = :uid AND org_id = :org AND object_api_name = :obj
                          AND LOWER(field_api_name) = LOWER('LastModifiedDate')
                        LIMIT 1
                        """,
                        {"uid": str(user_id), "org": src_org, "obj": source_object},
                    )
                    if lm_row:
                        resolved_cursor = (dict(lm_row).get("field_api_name") or "").strip() or None
                if not resolved_cursor:
                    resolved_cursor = "LastModifiedDate"
                await database.execute(
                    """
                    UPDATE migration_flows
                    SET incremental_cursor_field = :cursor_field, updated_at = :updated_at
                    WHERE id = :flow_id AND user_id = :user_id
                    """,
                    {
                        "cursor_field": resolved_cursor,
                        "updated_at": now,
                        "flow_id": flow_id,
                        "user_id": str(user_id),
                    },
                )
        if source_changed and not dest_changed:
            await database.execute(
                """
                UPDATE migration_flows
                SET updated_at = :updated_at
                WHERE id = :flow_id AND user_id = :user_id
                """,
                {"updated_at": now, "flow_id": flow_id, "user_id": str(user_id)},
            )

    return {
        "status": "success",
        "unchanged": False,
        "flow_id": flow_id,
    }


def _deep_copy_migration_rules(mr: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(mr))


def _migration_rules_dict_to_db_value(mr: Optional[Dict[str, Any]]) -> Optional[str]:
    if mr is None or (isinstance(mr, dict) and len(mr) == 0):
        return None
    from utils.migration_rules_utils import normalize_migration_rules_for_save

    normalized = normalize_migration_rules_for_save(mr) or {}
    return json.dumps(normalized) if normalized else None


def _apply_migration_rules_removals(
    migration_rules: Dict[str, Any],
    *,
    remove_extraction_transformation_rule_ids: Optional[List[str]] = None,
    remove_mapping_transformation_rule_ids: Optional[List[str]] = None,
    remove_filter_condition_indices: Optional[List[int]] = None,
    remove_deduplication_fields: Optional[List[str]] = None,
    remove_data_cleanup_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    mr = _deep_copy_migration_rules(migration_rules)
    rid_ex = {str(x) for x in (remove_extraction_transformation_rule_ids or []) if x}
    rid_map = {str(x) for x in (remove_mapping_transformation_rule_ids or []) if x}

    if rid_ex:
        ex = mr.get("extraction_rules")
        if isinstance(ex, dict):
            tr_raw = ex.get("transformation_rules")
            tr_norm = _normalize_extraction_transformation_rules(tr_raw)
            if tr_norm and isinstance(tr_norm.get("fields"), list):
                new_fields = [
                    f
                    for f in tr_norm["fields"]
                    if not (isinstance(f, dict) and str(f.get("rule_id") or "") in rid_ex)
                ]
                ex["transformation_rules"] = {"fields": new_fields}

    if rid_map:
        from utils.migration_rules_utils import (
            MAPPING_TRANSFORMATION_KEY,
            LEGACY_MAPPING_TRANSFORMATION_KEY,
            get_mapping_transformation,
        )

        tr_top = get_mapping_transformation(mr)
        if isinstance(tr_top, dict) and isinstance(tr_top.get("fields"), list):
            tr_top["fields"] = [
                f
                for f in tr_top["fields"]
                if not (isinstance(f, dict) and str(f.get("rule_id") or "") in rid_map)
            ]
            mr[MAPPING_TRANSFORMATION_KEY] = tr_top
            mr.pop(LEGACY_MAPPING_TRANSFORMATION_KEY, None)

    if remove_filter_condition_indices:
        idxs = sorted({int(i) for i in remove_filter_condition_indices if i is not None}, reverse=True)
        ex = mr.get("extraction_rules")
        if isinstance(ex, dict):
            fr = ex.get("filter_rules")
            if isinstance(fr, dict) and isinstance(fr.get("conditions"), list):
                conds = fr["conditions"]
                for i in idxs:
                    if 0 <= i < len(conds):
                        conds.pop(i)

    if remove_deduplication_fields:
        to_remove = {str(x).strip() for x in remove_deduplication_fields if x is not None and str(x).strip()}
        ex = mr.get("extraction_rules")
        if isinstance(ex, dict):
            dr = ex.get("deduplication_rules")
            if isinstance(dr, dict) and isinstance(dr.get("fields"), list):
                dr["fields"] = [f for f in dr["fields"] if str(f).strip() not in to_remove]

    if remove_data_cleanup_fields:
        keys_remove = {str(x).strip() for x in remove_data_cleanup_fields if x is not None and str(x).strip()}
        ex = mr.get("extraction_rules")
        if isinstance(ex, dict):
            dc = ex.get("data_cleanup_rules")
            if isinstance(dc, dict) and keys_remove:
                for k in list(dc.keys()):
                    if str(k).strip() in keys_remove:
                        del dc[k]
                if not dc:
                    ex.pop("data_cleanup_rules", None)

    return mr


def _build_migration_rules_editor_snapshot(migration_rules: Any) -> Dict[str, Any]:
    """Subset of migration_rules for the flow-detail / editor UI (JSON-serializable copies)."""
    if not isinstance(migration_rules, dict):
        migration_rules = {}
    mr = migration_rules
    pref = mr.get("missing_field_preference")
    if not isinstance(pref, dict):
        pref = None

    ex = mr.get("extraction_rules")
    if not isinstance(ex, dict):
        ex = {}
    tr_norm = _normalize_extraction_transformation_rules(ex.get("transformation_rules"))
    fields_ex: List[Any] = []
    if isinstance(tr_norm, dict) and isinstance(tr_norm.get("fields"), list):
        fields_ex = list(tr_norm["fields"])

    dc = ex.get("data_cleanup_rules")
    data_cleanup_out: Optional[Dict[str, Any]] = None
    if isinstance(dc, dict) and dc:
        data_cleanup_out = dict(dc)

    fr = ex.get("filter_rules")
    filter_out: Optional[Dict[str, Any]] = None
    if isinstance(fr, dict) and fr:
        logic = fr.get("logic", "AND")
        conds = fr.get("conditions")
        if isinstance(conds, list) and len(conds) > 0:
            filter_out = {"logic": logic, "conditions": list(conds)}
        elif fr.get("logic") or (isinstance(conds, list) and len(conds) == 0):
            filter_out = {"logic": logic, "conditions": list(conds) if isinstance(conds, list) else []}

    dr = ex.get("deduplication_rules")
    dedup_out: Optional[Dict[str, Any]] = None
    if isinstance(dr, dict) and dr:
        dedup_out = dict(dr)

    from utils.migration_rules_utils import get_mapping_transformation

    tr_top = get_mapping_transformation(mr)
    fields_map: List[Any] = []
    if isinstance(tr_top, dict) and isinstance(tr_top.get("fields"), list):
        fields_map = list(tr_top["fields"])

    return {
        "missing_field_preference": pref,
        "extraction_transformation_fields": fields_ex,
        "mapping_transformation_fields": fields_map,
        "filter_rules": filter_out,
        "deduplication_rules": dedup_out,
        "data_cleanup_rules": data_cleanup_out,
    }


class MissingFieldPreferencePayload(BaseModel):
    option: str
    placeholderValue: Optional[str] = None


def _normalize_missing_field_preference_payload(body: MissingFieldPreferencePayload) -> Dict[str, Any]:
    opt = (body.option or "").strip().lower()
    if opt not in ("skip", "placeholder"):
        raise HTTPException(
            status_code=400,
            detail="missing_field_preference.option must be 'skip' or 'placeholder'",
        )
    if opt == "placeholder":
        pv = (body.placeholderValue if body.placeholderValue is not None else "").strip()
        if not pv:
            raise HTTPException(
                status_code=400,
                detail="missing_field_preference.placeholderValue is required when option is 'placeholder'",
            )
        return {"option": "placeholder", "placeholderValue": pv}
    return {"option": "skip", "placeholderValue": (body.placeholderValue or "Unknown")}


class PatchMigrationRulesRequest(BaseModel):
    """Clear all, keep only missing_field_preference, remove blocks, and/or update missing_field_preference."""

    clear: bool = False
    clear_except_missing_field_preference: bool = False
    missing_field_preference: Optional[MissingFieldPreferencePayload] = None
    remove_extraction_transformation_rule_ids: Optional[List[str]] = None
    remove_mapping_transformation_rule_ids: Optional[List[str]] = None
    remove_filter_condition_indices: Optional[List[int]] = None
    remove_deduplication_fields: Optional[List[str]] = None
    remove_data_cleanup_fields: Optional[List[str]] = None


@router.get("/flow/{flow_id}/migration-rules/validate")
async def validate_flow_migration_rules(
    flow_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """
    Check migration_rules (filters, dedupe, data cleanup keys, extraction/mapping transforms) against
    source fields available for this flow. Returns structured issues for the UI.
    """
    flow_row = await database.fetch_one(
        """
        SELECT migration_rules FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not flow_row:
        raise HTTPException(status_code=404, detail="Flow not found")

    mr_raw = dict(flow_row).get("migration_rules")
    migration_rules: Dict[str, Any] = {}
    if mr_raw is not None:
        if isinstance(mr_raw, str):
            try:
                migration_rules = json.loads(mr_raw) if mr_raw.strip() else {}
            except json.JSONDecodeError:
                migration_rules = {}
        elif isinstance(mr_raw, dict):
            migration_rules = mr_raw

    snapshot = _build_migration_rules_editor_snapshot(migration_rules)

    err, source_kind, allowed = await _allowed_source_fields_for_flow_migration_validation(user_id, flow_id)
    if err:
        raise HTTPException(status_code=400, detail=err)

    if source_kind == "file":
        return {
            "status": "success",
            "skipped": "file_source",
            "valid": True,
            "issues": [],
            "snapshot": snapshot,
            "message": "Migration rules are not validated for file-upload flows.",
        }

    if not _migration_rules_has_actionable_source_refs(migration_rules):
        return {
            "status": "success",
            "skipped": "no_actionable_rules",
            "valid": True,
            "issues": [],
            "snapshot": snapshot,
            "message": "No filter, deduplication, data cleanup, or field rules to validate against source fields.",
        }

    issues = _collect_migration_rules_source_field_issues(migration_rules, allowed)
    return {
        "status": "success",
        "skipped": None,
        "valid": len(issues) == 0,
        "issues": issues,
        "snapshot": snapshot,
        "allowed_source_field_count": len(allowed),
    }


@router.patch("/flow/{flow_id}/migration-rules")
async def patch_flow_migration_rules(
    flow_id: str,
    body: PatchMigrationRulesRequest,
    user_id: UUID = Depends(get_current_user),
):
    """
    Update migration_rules: full clear (NULL), keep only missing_field_preference, or remove
    specific extraction/mapping field rules (by rule_id), filter conditions (by index), dedupe fields,
    or data_cleanup_rules keys (source field names).
    """
    has_removal = bool(
        (body.remove_extraction_transformation_rule_ids or [])
        or (body.remove_mapping_transformation_rule_ids or [])
        or (body.remove_filter_condition_indices or [])
        or (body.remove_deduplication_fields or [])
        or (body.remove_data_cleanup_fields or [])
    )
    has_pref_update = body.missing_field_preference is not None

    if body.clear and (body.clear_except_missing_field_preference or has_removal or has_pref_update):
        raise HTTPException(status_code=400, detail="clear cannot be combined with other migration rule updates.")
    if body.clear_except_missing_field_preference and (has_removal or has_pref_update):
        raise HTTPException(
            status_code=400,
            detail="clear_except_missing_field_preference cannot be combined with removals or preference updates.",
        )

    row = await database.fetch_one(
        """
        SELECT migration_rules FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Flow not found")

    mr_raw = dict(row).get("migration_rules")
    migration_rules: Dict[str, Any] = {}
    if mr_raw is not None:
        if isinstance(mr_raw, str):
            try:
                migration_rules = json.loads(mr_raw) if mr_raw.strip() else {}
            except json.JSONDecodeError:
                migration_rules = {}
        elif isinstance(mr_raw, dict):
            migration_rules = mr_raw

    now = datetime.utcnow()

    if body.clear:
        await database.execute(
            """
            UPDATE migration_flows
            SET migration_rules = NULL, updated_at = :updated_at
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": str(user_id), "updated_at": now},
        )
        return {"status": "success", "flow_id": flow_id, "cleared": True}

    if body.clear_except_missing_field_preference:
        pref = migration_rules.get("missing_field_preference") if isinstance(migration_rules, dict) else None
        new_mr: Optional[Dict[str, Any]] = None
        if isinstance(pref, dict) and pref:
            new_mr = {"missing_field_preference": pref}
        val = _migration_rules_dict_to_db_value(new_mr)
        await database.execute(
            """
            UPDATE migration_flows
            SET migration_rules = :mr, updated_at = :updated_at
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": str(user_id), "updated_at": now, "mr": val},
        )
        return {"status": "success", "flow_id": flow_id, "cleared_except_preference": True}

    if not has_removal and not has_pref_update:
        raise HTTPException(
            status_code=400,
            detail="Provide removals and/or missing_field_preference, or use clear / clear_except_missing_field_preference.",
        )

    updated = dict(migration_rules) if isinstance(migration_rules, dict) else {}
    if has_pref_update and body.missing_field_preference is not None:
        updated["missing_field_preference"] = _normalize_missing_field_preference_payload(
            body.missing_field_preference
        )
    if has_removal:
        updated = _apply_migration_rules_removals(
            updated,
            remove_extraction_transformation_rule_ids=body.remove_extraction_transformation_rule_ids,
            remove_mapping_transformation_rule_ids=body.remove_mapping_transformation_rule_ids,
            remove_filter_condition_indices=body.remove_filter_condition_indices,
            remove_deduplication_fields=body.remove_deduplication_fields,
            remove_data_cleanup_fields=body.remove_data_cleanup_fields,
        )
    val = _migration_rules_dict_to_db_value(updated)
    await database.execute(
        """
        UPDATE migration_flows
        SET migration_rules = :mr, updated_at = :updated_at
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id), "updated_at": now, "mr": val},
    )
    return {"status": "success", "flow_id": flow_id, "updated": True}


class FieldMappingUpdateItem(BaseModel):
    extracted_field_name: str
    salesforce_object: str
    salesforce_field: str
    lookup_of: Optional[str] = None
    is_duplicate_check: bool = False
    is_required: bool = False


class UpdateFieldMappingsRequest(BaseModel):
    mappings: List[FieldMappingUpdateItem]
    # Optional: persisted to extracted_schemas.sql_query only when source is sql or salesforce
    sql_query: Optional[str] = None


@router.patch("/flow/{flow_id}/field-mappings")
async def update_flow_field_mappings(
    flow_id: str,
    body: UpdateFieldMappingsRequest,
    user_id: UUID = Depends(get_current_user),
):
    """
    Replace all field mappings for a flow. Only allowed for non-file source flows
    (SQL, Google Sheets, Salesforce extraction). Validates source field names against
    stored extracted_schema and destination fields for Salesforce.

    For **SQL** and **Salesforce** sources, before persisting:
    1. **Structure** — `validate_select_query_for_mapping` on the submitted query and again on the
       synced SELECT list (single SELECT … FROM …, non-empty list, etc.).
    2. **SOQL-only** — `validate_soql_from_clause_for_mapping` for Salesforce extraction (stricter FROM).
    3. **Execution** — same LIMIT‑1 smoke tests as connection-change validation:
       `_sql_query_smoke_test` (SQL connector) or `_soql_smoke_test` (Salesforce token); save only if OK.

    The stored query's SELECT list is synced to mapped source fields (add/remove columns) before steps 2–3.
    """
    flow_row = await database.fetch_one(
        """
        SELECT id, org_id, destination_type
        FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not flow_row:
        raise HTTPException(status_code=404, detail="Flow not found")

    flow_data = dict(flow_row)

    schema_row = await database.fetch_one(
        """
        SELECT extracted_schema, extracted_schema_type, connector_id, table_name, sql_query
        FROM extracted_schemas
        WHERE migration_flow_id = :flow_id
        """,
        {"flow_id": flow_id},
    )
    if not schema_row:
        raise HTTPException(status_code=400, detail="No extracted schema for this flow")

    schema_data = dict(schema_row)

    ext_type = (schema_data.get("extracted_schema_type") or "file").lower()
    if ext_type == "file":
        raise HTTPException(
            status_code=400,
            detail="Editing field mappings is not available for file-upload flows.",
        )

    raw_schema = schema_data.get("extracted_schema")
    if isinstance(raw_schema, str):
        extracted_schema = json.loads(raw_schema)
    else:
        extracted_schema = raw_schema or {}

    allowed_source = _flatten_extracted_schema_source_keys(extracted_schema)

    existing_rows = await database.fetch_all(
        """
        SELECT extracted_field_name FROM field_mappings
        WHERE migration_flow_id = :flow_id
        """,
        {"flow_id": flow_id},
    )
    for r in existing_rows or []:
        fn = r["extracted_field_name"]
        if fn:
            allowed_source.add(fn)

    sch_conn = schema_data.get("connector_id")
    if sch_conn is not None:
        sch_conn = str(sch_conn)
    await _merge_connector_source_field_names(
        user_id,
        ext_type,
        sch_conn,
        allowed_source,
        source_table_or_object=schema_data.get("table_name"),
    )

    dest_type = (flow_data.get("destination_type") or "salesforce").lower()
    org_id = flow_data.get("org_id")

    if not body.mappings:
        raise HTTPException(status_code=400, detail="At least one field mapping is required.")

    for idx, m in enumerate(body.mappings):
        src = (m.extracted_field_name or "").strip()
        obj = (m.salesforce_object or "").strip()
        fld = (m.salesforce_field or "").strip()
        if not src or not obj or not fld:
            raise HTTPException(
                status_code=400,
                detail=f"Mapping row {idx + 1}: source field, destination target, and destination field are required.",
            )
        if src not in allowed_source:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown source field '{src}'. It must appear in the flow's extracted schema "
                    "or in the source connector's synced column metadata."
                ),
            )
        if dest_type == "salesforce" and org_id:
            ok = await database.fetch_one(
                """
                SELECT 1 FROM salesforce_object_fields
                WHERE user_id = :user_id AND org_id = :org_id
                  AND object_api_name = :obj AND field_api_name = :field
                LIMIT 1
                """,
                {
                    "user_id": str(user_id),
                    "org_id": org_id,
                    "obj": obj,
                    "field": fld,
                },
            )
            if not ok:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid Salesforce mapping: {obj}.{fld} is not available for this org.",
                )

    seen_src: Set[str] = set()
    for idx, m in enumerate(body.mappings):
        sk = (m.extracted_field_name or "").strip().lower()
        if sk in seen_src:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate source field in mapping row {idx + 1}. Each source field can only appear once.",
            )
        seen_src.add(sk)

    final_sql_to_store: Optional[str] = None
    if ext_type in ("sql", "salesforce"):
        if not sch_conn:
            raise HTTPException(
                status_code=400,
                detail="Source connection is missing for this flow; cannot validate the query.",
            )
        effective_sql = body.sql_query if body.sql_query is not None else schema_data.get("sql_query")
        effective_sql = str(effective_sql or "").strip()
        if not effective_sql:
            raise HTTPException(
                status_code=400,
                detail="A source SQL or SOQL query is required to save mappings for this flow.",
            )
        struct_err = validate_select_query_for_mapping(effective_sql)
        if struct_err:
            raise HTTPException(status_code=400, detail=struct_err)
        if ext_type == "salesforce":
            soql_from_err = validate_soql_from_clause_for_mapping(effective_sql)
            if soql_from_err:
                raise HTTPException(status_code=400, detail=soql_from_err)
        # SQL extract: sync SELECT to mapped *source* keys (extracted_field_name), i.e. columns or
        # aliases that appear on extracted rows. salesforce_field holds the *destination* API/column
        # name (SF FirstName, SQL dest column, etc.) — using it here wrongly rewrote SQL with SF names.
        # Salesforce extract: build items from mappings — relationship paths + aliases for lookup rows.
        if ext_type == "sql":
            source_names = [(m.extracted_field_name or "").strip() for m in body.mappings]
            new_sql, sync_err = sync_select_query_with_source_fields(
                effective_sql, source_names
            )
        elif org_id:
            _, from_obj = split_outer_select_clause(effective_sql)
            if not from_obj:
                raise HTTPException(
                    status_code=400,
                    detail="Could not parse the Salesforce object name from the query's FROM clause.",
                )
            select_items, build_err = await build_salesforce_mapping_select_items(
                str(user_id), org_id, from_obj, body.mappings
            )
            if build_err:
                raise HTTPException(status_code=400, detail=build_err)
            new_sql, sync_err = sync_select_query_with_field_items(
                effective_sql, select_items
            )
        else:
            # Salesforce SELECT sync must use org metadata (relationship paths, canonical API names).
            # sync_select_query_with_source_fields matches dotted SOQL by last segment only and breaks
            # keys like AccountName vs Name — do not fall back.
            raise HTTPException(
                status_code=400,
                detail=(
                    "This flow has no Salesforce org on the migration record (org_id). "
                    "Attach or select a Salesforce org, then save field mappings so SOQL can be built "
                    "from synced field metadata (including relationship paths)."
                ),
            )
        if sync_err:
            raise HTTPException(status_code=400, detail=sync_err)
        struct_err_synced = validate_select_query_for_mapping(new_sql)
        if struct_err_synced:
            raise HTTPException(status_code=400, detail=struct_err_synced)
        if ext_type == "salesforce":
            soql_from_err2 = validate_soql_from_clause_for_mapping(new_sql)
            if soql_from_err2:
                raise HTTPException(status_code=400, detail=soql_from_err2)
        if ext_type == "sql":
            smoke = await _sql_query_smoke_test(str(user_id), str(sch_conn), new_sql)
        else:
            smoke = await _soql_smoke_test(str(user_id), str(sch_conn), new_sql)
        if smoke:
            raise _http_400_from_query_smoke_error(smoke)
        final_sql_to_store = new_sql

    async with database.transaction():
        await database.execute(
            "DELETE FROM field_mappings WHERE migration_flow_id = :flow_id",
            {"flow_id": flow_id},
        )
        now = datetime.utcnow()
        for m in body.mappings:
            await database.execute(
                """
                INSERT INTO field_mappings (
                  id, migration_flow_id, extracted_field_name, salesforce_object, salesforce_field,
                  lookup_of, is_duplicate_check, is_required, created_at
                ) VALUES (
                  :id, :flow_id, :field_name, :object, :field, :lookup, :is_duplicate_check, :is_required, :created_at
                )
                """,
                {
                    "id": str(uuid4()),
                    "flow_id": flow_id,
                    "field_name": m.extracted_field_name.strip(),
                    "object": m.salesforce_object.strip(),
                    "field": m.salesforce_field.strip(),
                    "lookup": m.lookup_of,
                    "is_duplicate_check": m.is_duplicate_check,
                    "is_required": m.is_required,
                    "created_at": now,
                },
            )

        if ext_type in ("sql", "salesforce") and final_sql_to_store is not None:
            await database.execute(
                """
                UPDATE extracted_schemas
                SET sql_query = :sql_query
                WHERE migration_flow_id = :flow_id
                """,
                {"sql_query": final_sql_to_store, "flow_id": flow_id},
            )

    updated = await database.fetch_all(
        """
        SELECT id, extracted_field_name, salesforce_object, salesforce_field, lookup_of,
               is_duplicate_check, is_required, created_at
        FROM field_mappings
        WHERE migration_flow_id = :flow_id
        ORDER BY created_at
        """,
        {"flow_id": flow_id},
    )
    out = []
    for fm in updated or []:
        row = dict(fm)
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat()
        out.append(row)

    resp: Dict[str, Any] = {"status": "success", "field_mappings": out}
    if ext_type in ("sql", "salesforce") and final_sql_to_store is not None:
        resp["sql_query"] = final_sql_to_store
    return resp


@router.delete("/flow/{flow_id}")
async def delete_flow(flow_id: str, user_id: UUID = Depends(get_current_user)):
    """
    Delete a migration flow and all associated data (extracted schema, field mappings)
    """
    try:
        # First, verify the flow exists and belongs to the user
        flow_result = await database.fetch_one(
            """
            SELECT id, migration_name FROM migration_flows 
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": str(user_id)}
        )
        
        if not flow_result:
            raise HTTPException(status_code=404, detail="Flow not found or access denied")

        await prepare_delete_flow_bidirectional(flow_id, str(user_id))
        
        # Delete in the correct order to respect foreign key constraints
        # 1. Delete field mappings first
        await database.execute(
            "DELETE FROM field_mappings WHERE migration_flow_id = :flow_id",
            {"flow_id": flow_id}
        )
        
        # 2. Delete extracted schema
        await database.execute(
            "DELETE FROM extracted_schemas WHERE migration_flow_id = :flow_id",
            {"flow_id": flow_id}
        )
        
        # 3. Delete the migration flow
        await database.execute(
            "DELETE FROM migration_flows WHERE id = :flow_id AND user_id = :user_id",
            {"flow_id": flow_id, "user_id": str(user_id)},
        )
        
        return {
            "status": "success", 
            "message": f"Flow '{flow_result['migration_name']}' and all associated data deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete flow")


class TransformDataRequest(BaseModel):
    job_id: str  # Original extraction job_id
    mapping: dict  # Field mapping from frontend (from map-fields endpoint response)
    transformation_rules: Optional[dict] = {"fields": []}  # Transformation rules for field splitting
    destination: Optional[str] = "salesforce"  # Destination type: "salesforce", "google_sheets", "sql"
    flow_id: Optional[str] = None  # Optional: Migration flow_id (only when running saved flow)
    org_id: Optional[str] = None  # Optional: Salesforce org_id for metadata lookup


@router.post("/transform-data")
async def transform_data(
    payload: TransformDataRequest,
    user_id: UUID = Depends(get_current_user)
):
    """
    Transform extracted data based on destination field mappings and schema.
    Creates version 3 (transformed data) in extraction_results.
    
    Accepts mapping from frontend (from map-fields endpoint response).
    flow_id is optional and only used when running a saved flow.
    """
    try:
        from services.extraction_results_service import ExtractionResultsService
        
        # Step 1: Load original extraction data (version 1 or 2)
        extraction_result = await ExtractionResultsService.get_extraction_result(
            payload.job_id, str(user_id)
        )
        
        if not extraction_result:
            raise HTTPException(
                status_code=404,
                detail=f"Extraction result not found for job_id: {payload.job_id}"
            )
        
        extracted_data = extraction_result.get("extracted_data", [])
        if not extracted_data:
            raise HTTPException(
                status_code=400,
                detail="No data found in extraction result"
            )

        # Transformation is included; no credit check

        # Step 2: Use mapping from frontend payload
        mapping = payload.mapping
        if not mapping:
            raise HTTPException(
                status_code=400,
                detail="Mapping is required"
            )
        
        # Step 3: Get org_id for metadata lookup
        org_id = payload.org_id
        
        # If flow_id is provided, we can get org_id and destination_type from flow
        flow_result = None
        if payload.flow_id:
            flow_result = await database.fetch_one(
                """
                SELECT org_id, destination_type
                FROM migration_flows
                WHERE id = :flow_id AND user_id = :user_id
                """,
                {"flow_id": payload.flow_id, "user_id": str(user_id)}
            )
            
            if flow_result and flow_result.get("destination_type") == "salesforce":
                org_id = flow_result.get("org_id") or org_id
        
        # Step 4: Transform data (shared with migrate auto-transform path)
        transformation_rules = payload.transformation_rules or {"fields": []}
        
        # Determine destination: use from payload, or from flow, or infer from mapping structure
        destination = payload.destination or "salesforce"  # Use destination from payload (from map-fields response)
        
        # Override with flow destination if flow_id is provided (flow takes precedence)
        if flow_result:
            destination = flow_result.get("destination_type", destination)
        
        # If destination still not set, infer from mapping structure as fallback
        if not destination or destination == "salesforce":
            sample_mapping = next(iter(mapping.values())) if mapping else None
            if isinstance(sample_mapping, list):
                # Array case (split fields) - check first item
                sample_mapping = sample_mapping[0] if sample_mapping else {}
            
            if isinstance(sample_mapping, dict):
                if "object" in sample_mapping and "field" in sample_mapping:
                    destination = "salesforce"
                elif "column" in sample_mapping and "sheet" in sample_mapping:
                    destination = "google_sheets"
                elif "column" in sample_mapping and "table" in sample_mapping:
                    destination = "sql"

        from services.migration.mapping_transform_job import ensure_mapping_transform_job

        transform_result = await ensure_mapping_transform_job(
            user_id=str(user_id),
            source_job_id=payload.job_id,
            mapping=mapping,
            transformation_rules=transformation_rules,
            destination=destination,
            org_id=org_id,
            conversation_id=extraction_result.get("conversation_id"),
            extraction_result=extraction_result,
        )
        transformed_data = transform_result.transformed_data or extracted_data
        transformation_metadata = transform_result.transformation_metadata or {}
        transformed_job_id = transform_result.job_id
        
        return {
            "status": "success",
            "transformed_job_id": transformed_job_id,
            "transformed_data": transformed_data,
            "transformation_metadata": transformation_metadata,
            "record_count": len(transformed_data),
            "extraction_version": 3
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error transforming data: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to transform data: {str(e)}"
        )


@router.get("/extracted-schema/{file_key}")
async def get_extracted_schema_by_file_key(file_key: str, user_id: UUID = Depends(get_current_user)):
    """
    Get extracted schema data by file_key
    """
    try:
        # Get the extracted schema for the given file_key
        schema_result = await database.fetch_one(
            """
            SELECT 
                id, document_type, file_key, openai_file_id, extracted_schema, user_prompt, approved_by_user, created_at
            FROM extracted_schemas
            WHERE file_key = :file_key AND user_id = :user_id
            """,
            {"file_key": file_key, "user_id": str(user_id)}
        )
        
        if not schema_result:
            raise HTTPException(status_code=404, detail="Extracted schema not found")
        
        # Convert datetime to ISO string for JSON serialization
        schema_data = dict(schema_result)
        if schema_data.get('created_at'):
            schema_data['created_at'] = schema_data['created_at'].isoformat()
        
        return {
            "status": "success",
            "extracted_schema": schema_data["extracted_schema"],
            "document_type": schema_data["document_type"],
            "user_prompt": schema_data["user_prompt"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get extracted schema")