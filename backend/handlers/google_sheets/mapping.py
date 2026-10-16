"""
Google Sheets Handler Module

This module contains all Google Sheets related functions for:
- Data extraction from Google Sheets
- Information analysis of Google Sheets
- Spreadsheet detection and metadata handling
- Background processing for Google Sheets operations
"""

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from googleapiclient.errors import HttpError

from utils.extraction_sse_payload import extraction_setup_progress_sse_line


def _db_row_dict(row: Any) -> Dict[str, Any]:
    """databases.Record rows support row[key] but not .get()."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _ensure_sheet_ai_categorization(
    sheet: Dict[str, Any],
    *,
    destination_name: Optional[str],
    document_type: Optional[str],
    document_description: Optional[str],
) -> None:
    """Fill missing/invalid ai_categorization so workbook matching does not warn on null JSON."""
    raw = sheet.get("ai_categorization")
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                return
        except (json.JSONDecodeError, TypeError):
            pass
    sheet["ai_categorization"] = json.dumps(
        {
            "document_type": document_type or destination_name or "Pending",
            "document_description": document_description or destination_name or "",
        }
    )


async def _load_user_spreadsheet_catalog_rows(user_id) -> List[Dict[str, Any]]:
    """Workbook/tab rows for fuzzy name resolution (same catalog as planner)."""
    from db.connection import database

    rows = await database.fetch_all(
        """
        SELECT DISTINCT
            ugs.spreadsheet_id,
            ugs.spreadsheet_name,
            ugs.sheet_name,
            ugs.sheet_id
        FROM user_google_spreadsheets ugs
        JOIN google_sheets_connections gsc
          ON ugs.google_sheets_connection_id = gsc.id
        WHERE gsc.user_id = CAST(:user_id AS uuid)
        ORDER BY ugs.spreadsheet_name, ugs.sheet_name
        """,
        {"user_id": str(user_id)},
    )
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        rd = _db_row_dict(row)
        if rd.get("spreadsheet_id") and rd.get("spreadsheet_name"):
            out.append(rd)
    if out:
        return out
    schema_rows = await database.fetch_all(
        """
        SELECT DISTINCT
            ges.spreadsheet_id,
            ges.spreadsheet_name,
            ges.sheet_name
        FROM gsheets_extracted_schemas ges
        JOIN google_sheets_connections gsc
          ON ges.google_sheets_connection_id = gsc.id
        WHERE ges.user_id = CAST(:user_id AS uuid)
          AND COALESCE(ges.is_deleted, FALSE) = FALSE
        ORDER BY ges.spreadsheet_name, ges.sheet_name
        """,
        {"user_id": str(user_id)},
    )
    for row in schema_rows or []:
        rd = _db_row_dict(row)
        if rd.get("spreadsheet_id") and rd.get("spreadsheet_name"):
            out.append(rd)
    return out


def _fuzzy_resolve_spreadsheet_by_name(
    destination_name: str,
    catalog_rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Exact catalog name match only — classifier/PLAN_SCOPE must supply the workbook label."""
    if not destination_name or not catalog_rows:
        return None
    target = destination_name.strip().lower()
    for row in catalog_rows:
        sn = (row.get("spreadsheet_name") or "").strip()
        if sn and sn.lower() == target:
            return row
    return None


# In-memory storage for pending update confirmations
# In production, this should be in Redis or database with TTL
pending_google_sheets_updates = {}


async def map_to_google_sheets(
    flat_data: dict,
    document_type: str,
    document_description: str,
    destination_name: str,
    user_id,
    mapping_context_summary: Optional[str] = None,
    destination_sheet_name: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
    google_sheets_connection_id: Optional[str] = None,
    *,
    plan_pinned_entity: bool = False,
):
    """
    Map extracted data fields to Google Sheets columns.
    
    Args:
        flat_data: First record from extracted data (dict with field names and values)
        document_type: Category/document type
        document_description: Description of the document
        destination_name: Spreadsheet name
        user_id: User ID (UUID)
        
    Returns:
        dict: Mapping result with structure:
            {
                "destination": "google_sheets",
                "destination_name": str,
                "spreadsheet_id": str,
                "mapping": dict,  # Field to column mappings
                "available_sheets": dict  # Available sheets and columns
            }
    """
    try:
        from db.connection import database
        import json
        from utils.document_category_match import (
            apply_reconcile_to_tuple,
            normalize_doc_label,
            reconcile_document_category,
        )

        pinned_sheet = (destination_sheet_name or "").strip()
        if not plan_pinned_entity and not pinned_sheet:
            reconcile = await reconcile_document_category(
                user_id,
                "google_sheets",
                destination_name,
                document_type,
                document_description,
                flat_data,
            )
            document_type, document_description = apply_reconcile_to_tuple(
                document_type, document_description, reconcile
            )
            print(
                f"[MAP_GSHEETS] reconcile tier={reconcile.match_tier} "
                f"type={document_type!r} dest={destination_name!r}"
            )

        # Step 1: Find spreadsheet by destination_name and/or ai_categorization
        # Filter by spreadsheet_name directly in SQL if destination_name is provided
        # Get sheet_id from user_google_spreadsheets table (not in gsheets_extracted_schemas)
        # Use case-insensitive comparison by normalizing both sides with LOWER()
        conn_clause = ""
        query_values: Dict[str, Any] = {
            "user_id": str(user_id),
            "spreadsheet_name": destination_name,
            "spreadsheet_id": spreadsheet_id,
        }
        if google_sheets_connection_id:
            conn_clause = (
                " AND u.google_sheets_connection_id = CAST(:google_sheets_connection_id AS uuid)"
            )
            query_values["google_sheets_connection_id"] = str(google_sheets_connection_id)

        spreadsheets = await database.fetch_all(
            f"""
            SELECT 
                s.spreadsheet_id,
                s.spreadsheet_name,
                s.sheet_name,
                u.sheet_id,
                s.header_schema,
                s.ai_categorization
            FROM gsheets_extracted_schemas s
            INNER JOIN user_google_spreadsheets u 
                ON s.spreadsheet_id = u.spreadsheet_id 
                AND s.user_id = u.user_id
                AND s.sheet_name = u.sheet_name
            WHERE s.user_id = CAST(:user_id AS uuid)
                AND (
                    CAST(:spreadsheet_id AS varchar) IS NULL
                    OR s.spreadsheet_id = CAST(:spreadsheet_id AS varchar)
                )
                AND (
                    CAST(:spreadsheet_name AS varchar) IS NULL
                    OR LOWER(TRIM(s.spreadsheet_name)) = LOWER(TRIM(CAST(:spreadsheet_name AS varchar)))
                )
                {conn_clause}
            """,
            values=query_values,
        )

        spreadsheets = [_db_row_dict(s) for s in (spreadsheets or [])]
        for sheet in spreadsheets:
            _ensure_sheet_ai_categorization(
                sheet,
                destination_name=destination_name,
                document_type=document_type,
                document_description=document_description,
            )

        if not spreadsheets and destination_name:
            header_names = list((flat_data or {}).keys())
            header_schema_json = json.dumps(
                {
                    "headers": [
                        {"name": name, "type": "string", "position": idx}
                        for idx, name in enumerate(header_names)
                        if str(name).strip()
                    ]
                }
            )
            ugs_rows = await database.fetch_all(
                f"""
                SELECT spreadsheet_id, spreadsheet_name, sheet_name, sheet_id
                FROM user_google_spreadsheets
                WHERE user_id = CAST(:user_id AS uuid)
                  AND (
                    (
                      CAST(:spreadsheet_id AS varchar) IS NOT NULL
                      AND spreadsheet_id = CAST(:spreadsheet_id AS varchar)
                    )
                    OR (
                      CAST(:spreadsheet_id AS varchar) IS NULL
                      AND LOWER(TRIM(spreadsheet_name)) = LOWER(
                        TRIM(CAST(:spreadsheet_name AS varchar))
                      )
                    )
                  )
                  {conn_clause.replace('u.google_sheets_connection_id', 'google_sheets_connection_id')}
                """,
                values=query_values,
            )
            for row in ugs_rows or []:
                rd = _db_row_dict(row)
                spreadsheets.append(
                    {
                        "spreadsheet_id": rd.get("spreadsheet_id"),
                        "spreadsheet_name": rd.get("spreadsheet_name"),
                        "sheet_name": rd.get("sheet_name"),
                        "sheet_id": rd.get("sheet_id"),
                        "header_schema": header_schema_json,
                        "ai_categorization": json.dumps(
                            {
                                "document_type": document_type or "Pending",
                                "document_description": document_description
                                or destination_name,
                            }
                        ),
                    }
                )

        if not spreadsheets and destination_name and not spreadsheet_id:
            catalog_rows = await _load_user_spreadsheet_catalog_rows(user_id)
            fuzzy = _fuzzy_resolve_spreadsheet_by_name(destination_name, catalog_rows)
            if fuzzy:
                resolved_id = fuzzy.get("spreadsheet_id")
                resolved_name = fuzzy.get("spreadsheet_name")
                print(
                    f"[MAP_GSHEETS] Fuzzy workbook match: {destination_name!r} → "
                    f"{resolved_name!r} ({resolved_id})"
                )
                destination_name = resolved_name
                spreadsheet_id = resolved_id
                spreadsheets = await database.fetch_all(
                    """
                    SELECT
                        s.spreadsheet_id,
                        s.spreadsheet_name,
                        s.sheet_name,
                        u.sheet_id,
                        s.header_schema,
                        s.ai_categorization
                    FROM gsheets_extracted_schemas s
                    INNER JOIN user_google_spreadsheets u
                        ON s.spreadsheet_id = u.spreadsheet_id
                        AND s.user_id = u.user_id
                        AND s.sheet_name = u.sheet_name
                    WHERE s.user_id = CAST(:user_id AS uuid)
                      AND s.spreadsheet_id = CAST(:spreadsheet_id AS varchar)
                    """,
                    values={
                        "user_id": str(user_id),
                        "spreadsheet_id": spreadsheet_id,
                    },
                )
                spreadsheets = [_db_row_dict(s) for s in (spreadsheets or [])]
                for sheet in spreadsheets:
                    _ensure_sheet_ai_categorization(
                        sheet,
                        destination_name=destination_name,
                        document_type=document_type,
                        document_description=document_description,
                    )
                if not spreadsheets:
                    header_names = list((flat_data or {}).keys())
                    header_schema_json = json.dumps(
                        {
                            "headers": [
                                {"name": name, "type": "string", "position": idx}
                                for idx, name in enumerate(header_names)
                                if str(name).strip()
                            ]
                        }
                    )
                    for row in catalog_rows:
                        if row.get("spreadsheet_id") != spreadsheet_id:
                            continue
                        spreadsheets.append(
                            {
                                "spreadsheet_id": row.get("spreadsheet_id"),
                                "spreadsheet_name": row.get("spreadsheet_name"),
                                "sheet_name": row.get("sheet_name"),
                                "sheet_id": row.get("sheet_id"),
                                "header_schema": header_schema_json,
                                "ai_categorization": json.dumps(
                                    {
                                        "document_type": document_type or "Pending",
                                        "document_description": document_description
                                        or destination_name,
                                    }
                                ),
                            }
                        )

        print(f"spreadsheets: {len(spreadsheets)} row(s)")
        if not spreadsheets:
            if destination_name:
                raise ValueError(f"No spreadsheet found with name: {destination_name}")
            else:
                raise ValueError(f"No Google Spreadsheets found for user")
        
        # Step 2: Prefer sheets whose ai_categorization matches reconciled labels
        dt_norm = normalize_doc_label(document_type)
        dd_norm = normalize_doc_label(document_description)
        matching_sheets = []
        type_only_sheets = []
        for sheet in spreadsheets:
            try:
                raw_cat = sheet.get("ai_categorization")
                if raw_cat:
                    ai_cat = json.loads(raw_cat) if isinstance(raw_cat, str) else raw_cat
                else:
                    ai_cat = {}
                if not isinstance(ai_cat, dict):
                    ai_cat = {}
                category = normalize_doc_label(ai_cat.get("document_type"))
                category_desc = normalize_doc_label(ai_cat.get("document_description"))

                if category == dt_norm and category_desc == dd_norm and dt_norm:
                    matching_sheets.append(sheet)
                elif category == dt_norm and dt_norm:
                    type_only_sheets.append(sheet)
            except (json.JSONDecodeError, AttributeError, TypeError) as e:
                print(f"⚠️ Warning: Could not parse ai_categorization for sheet {sheet.get('sheet_name')}: {e}")
                continue

        if not matching_sheets and type_only_sheets:
            matching_sheets = type_only_sheets

        # Classifier named the destination workbook — use it even if source extraction labels differ
        if not matching_sheets and destination_name and spreadsheets:
            print(
                f"[MAP_GSHEETS] Workbook fallback: using {destination_name!r} "
                f"({len(spreadsheets)} sheet row(s))"
            )
            matching_sheets = list(spreadsheets)

        if not matching_sheets:
            from utils.mapping_user_messages import mapping_destination_clarification_message

            raise ValueError(
                mapping_destination_clarification_message(
                    destination_name=destination_name,
                    platform="google_sheets",
                    document_type=document_type,
                )
            )

        if destination_sheet_name:
            tab_norm = destination_sheet_name.strip().lower()
            tab_matches = [
                s
                for s in matching_sheets
                if (s.get("sheet_name") or "").strip().lower() == tab_norm
            ]
            if tab_matches:
                matching_sheets = tab_matches
        
        # Group sheets by spreadsheet_id (one spreadsheet can have multiple sheets)
        spreadsheet_groups = {}
        for sheet in matching_sheets:
            spreadsheet_id = sheet["spreadsheet_id"]
            if spreadsheet_id not in spreadsheet_groups:
                spreadsheet_groups[spreadsheet_id] = {
                    "spreadsheet_id": spreadsheet_id,
                    "spreadsheet_name": sheet["spreadsheet_name"],
                    "sheets": []
                }
            spreadsheet_groups[spreadsheet_id]["sheets"].append(sheet)
        
        # Use the first matching spreadsheet (or the one specified by destination_name)
        primary_spreadsheet = list(spreadsheet_groups.values())[0]
        spreadsheet_id = primary_spreadsheet["spreadsheet_id"]
        spreadsheet_name = primary_spreadsheet["spreadsheet_name"]
        
        # Step 4: Build schema structure for all sheets in the spreadsheet
        sheets_structure = {}
        for sheet in primary_spreadsheet["sheets"]:
            sheet_name = sheet["sheet_name"]
            try:
                header_schema_raw = json.loads(sheet["header_schema"]) if isinstance(sheet["header_schema"], str) else sheet["header_schema"]
                
                # Handle different header_schema formats
                columns = {}
                
                # Format 1: {"headers": [{"name": "First Name", "type": "string", "position": 0}, ...]}
                if isinstance(header_schema_raw, dict) and "headers" in header_schema_raw:
                    for header in header_schema_raw["headers"]:
                        if isinstance(header, dict):
                            col_name = header.get("name", "")
                            if col_name:
                                columns[col_name] = {
                                    "data_type": header.get("type", "text"),
                                    "description": header.get("description", ""),
                                    "position": header.get("position", 0)
                                }
                
                # Format 2: [{"column": "...", "data_type": "..."}, ...]
                elif isinstance(header_schema_raw, list):
                    for header in header_schema_raw:
                        if isinstance(header, dict):
                            col_name = header.get("column", "") or header.get("name", "")
                            if col_name:
                                columns[col_name] = {
                                    "data_type": header.get("data_type", "") or header.get("type", "text"),
                                    "description": header.get("description", "")
                                }
                        elif isinstance(header, str):
                            columns[header] = {
                                "data_type": "text",
                                "description": ""
                            }
                
                # Format 3: {"First Name": "string", "Last Name": "string", ...}
                elif isinstance(header_schema_raw, dict):
                    for col_name, col_type in header_schema_raw.items():
                        columns[col_name] = {
                            "data_type": col_type if isinstance(col_type, str) else "text",
                            "description": ""
                        }
                
                if columns:
                    sheets_structure[sheet_name] = {
                        "sheet_id": sheet["sheet_id"],
                        "columns": columns
                    }
            except (json.JSONDecodeError, AttributeError) as e:
                print(f"⚠️ Warning: Could not parse header_schema for sheet {sheet_name}: {e}")
                continue
        
        if not sheets_structure:
            raise ValueError(f"No valid sheet schemas found in spreadsheet: {spreadsheet_name}")
        
        # Mapping build is free; no credit deduction

        # Step 5: Call AI to create intelligent mapping
        mapping = await call_google_sheets_mapping_agent(
            flat_data=flat_data,
            document_type=document_type,
            spreadsheet_name=spreadsheet_name,
            sheets_structure=sheets_structure,
            mapping_context_summary=mapping_context_summary
        )

        from utils.mapping_destination_validate import (
            filter_google_sheets_mapping_to_destination_schema,
            normalize_gsheets_mapping_source_keys,
        )
        from utils.gsheets_mapping_helpers import resolve_gsheets_primary_sheet
        from utils.mapping_transform_context import source_fields_from_flat_data

        primary_sheet = resolve_gsheets_primary_sheet(
            sheets_structure,
            destination_sheet_name=destination_sheet_name,
        )
        mapping = normalize_gsheets_mapping_source_keys(
            mapping,
            flat_data,
            sheets_structure,
            default_sheet=primary_sheet,
        )
        mapping = filter_google_sheets_mapping_to_destination_schema(
            mapping,
            sheets_structure,
            default_sheet=primary_sheet,
        )
        
        # Step 6: Use real data for transformation analysis (no masking)
        # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
        sample_data = [flat_data]
        
        # Step 7: Analyze if transformations are needed (e.g., field splitting)
        transformation_analysis = await analyze_google_sheets_mapping_transformations(
            mapping=mapping,
            flat_data=flat_data,
            sheets_structure=sheets_structure,
            sample_data=sample_data,
            primary_sheet=primary_sheet,
        )
        
        transformation_rules = transformation_analysis.get("transformation_rules", {"fields": []})
        enhanced_mapping_entries = transformation_analysis.get("enhanced_mapping", {})
        mapping_audit_payload = transformation_analysis.get("mapping_audit")
        
        # Step 8: Update mapping with enhanced entries
        # Handle both splitting (arrays) and combining (updated mapping entries with flags)
        if enhanced_mapping_entries:
            print(f"🔧 Updating {len(enhanced_mapping_entries)} mapping entries with transformations")
            for key, value in enhanced_mapping_entries.items():
                # Check if this is a split field (array) or combined field (dict with flags)
                if isinstance(value, list):
                    # Split field: Replace single object with array
                    # Ensure each item in array has is_split_field flag
                    for item in value:
                        if isinstance(item, dict):
                            item["is_split_field"] = True
                    # Replace the original mapping entry with the array
                    mapping[key] = value
                    print(f"   ✓ Updated '{key}' to array with {len(value)} split field mappings")
                elif isinstance(value, dict):
                    # Combined field: Update mapping entry with combine flags
                    # The value should have is_combined_field, combine_group, combine_order
                    if value.get("is_combined_field"):
                        mapping[key] = value
                        print(f"   ✓ Updated '{key}' as combined field (group: {value.get('combine_group')}, order: {value.get('combine_order')})")
                    else:
                        # Regular mapping update (shouldn't happen, but handle gracefully)
                        mapping[key] = value
                        print(f"   ✓ Updated '{key}' mapping")
                else:
                    print(f"   ⚠️ Warning: enhanced_mapping entry '{key}' has unexpected type {type(value)}, skipping")

        mapping = filter_google_sheets_mapping_to_destination_schema(
            mapping,
            sheets_structure,
            default_sheet=primary_sheet,
        )

        from services.planning.flow_steps import apply_mapping_transformation_to_config

        mapping = apply_mapping_transformation_to_config(
            mapping,
            transformation_rules,
            destination="google_sheets",
        )
        
        # Step 9: Return mapping result (mapping_transformation = panel splits/combines)
        from utils.migration_rules_utils import dual_publish_mapping_transform_api

        gs_result = {
            "destination": "google_sheets",
            "destination_name": spreadsheet_name,
            "spreadsheet_id": spreadsheet_id,
            "document_type": document_type,
            "document_description": document_description,
            "mapping": mapping,
            "available_sheets": sheets_structure,
            "source_fields": source_fields_from_flat_data(flat_data if isinstance(flat_data, dict) else {}),
            "mapping_audit": mapping_audit_payload,
        }
        dual_publish_mapping_transform_api(gs_result, transformation_rules)
        rule_count = len((transformation_rules or {}).get("fields") or [])
        print(
            f"🔧 [MAP_GSHEETS_TRANSFORM] mapping_keys={len(mapping)} "
            f"rules={rule_count} enhanced_entries={len(enhanced_mapping_entries or {})}"
        )
        return gs_result
        
    except Exception as e:
        print(f"❌ Error in map_to_google_sheets: {e}")
        import traceback
        traceback.print_exc()
        raise


async def analyze_google_sheets_mapping_transformations(
    mapping: dict,
    flat_data: dict,
    sheets_structure: dict,
    sample_data: list,
    *,
    primary_sheet: Optional[str] = None,
) -> dict:
    """
    Analyze the mapping to determine if field transformations are needed (e.g., name splitting).
    Returns transformation_rules and enhanced_mapping with new split field entries for Google Sheets.
    """
    mapping_audit_dict = None
    try:
        from services.architect_agent import async_openai_call
        from utils.openai_helpers import parse_openai_json_response
        import json

        from utils.mapping_audit import (
            audit_mapping_structure,
            audit_result_to_dict,
            resolve_post_map_transforms,
        )
        from utils.mapping_transform_context import classify_gsheets_destination_name_shape

        from utils.gsheets_mapping_helpers import resolve_gsheets_primary_sheet

        dest_name_shape = classify_gsheets_destination_name_shape(sheets_structure or {})
        base_sheet = resolve_gsheets_primary_sheet(
            sheets_structure or {},
            destination_sheet_name=primary_sheet,
        ) or (next(iter(dest_name_shape.keys()), None) if dest_name_shape else None)
        mapping_audit_dict = audit_result_to_dict(
            audit_mapping_structure(
                mapping=mapping or {},
                flat_data=flat_data or {},
                destination_type="google_sheets",
                base_container=str(base_sheet) if base_sheet else None,
                destination_name_shape=dest_name_shape,
            )
        )
        resolved = resolve_post_map_transforms(
            flat_data=flat_data or {},
            mapping=mapping or {},
            destination_name_shape=dest_name_shape,
            destination_type="google_sheets",
            base_container=str(base_sheet) if base_sheet else None,
            sample_data=sample_data,
        )
        if resolved.get("skip_transform_llm") and resolved.get("enhanced_mapping"):
            fields = (resolved.get("transformation_rules") or {}).get("fields") or []
            action = (fields[0].get("action") if fields else "") or ""
            if str(action).startswith("split"):
                print("🔧 [MAP_GS_TRANSFORM] Using programmatic compound name → first/last split (no transform LLM)")
            else:
                print("🔧 [MAP_GS_TRANSFORM] Using programmatic first+last → Name combine (no transform LLM)")
            return {
                "transformation_rules": resolved.get("transformation_rules") or {"fields": []},
                "enhanced_mapping": resolved.get("enhanced_mapping") or {},
                "mapping_audit": resolved.get("mapping_audit"),
            }
        if resolved.get("skip_transform_llm"):
            print("🔧 [MAP_GS_TRANSFORM] Skipping transform LLM — destination fields already aligned")
            return {
                "transformation_rules": {"fields": []},
                "enhanced_mapping": {},
                "mapping_audit": resolved.get("mapping_audit"),
            }
        
        # Build prompt for transformation analysis
        mapping_json = json.dumps(mapping, indent=2)
        sample_data_json = json.dumps(sample_data, indent=2)
        sheets_structure_json = json.dumps(sheets_structure, indent=2)
        
        # Check if multiple fields map to same column
        column_to_fields = {}
        for field_name, mapping_info in mapping.items():
            if isinstance(mapping_info, dict):
                column = mapping_info.get("column")
                if column:
                    if column not in column_to_fields:
                        column_to_fields[column] = []
                    column_to_fields[column].append(field_name)

        from utils.mapping_transform_context import (
            build_transform_llm_context_section,
            classify_gsheets_destination_name_shape,
        )

        dest_name_shape = classify_gsheets_destination_name_shape(sheets_structure)
        transform_context_section = build_transform_llm_context_section(
            flat_data=flat_data or {},
            mapping=mapping or {},
            destination_name_shape=dest_name_shape,
            destination_type="google_sheets",
        )
        
        prompt = f"""You are Archian AI, analyzing a field mapping to determine if transformations are needed for Google Sheets.

{transform_context_section}

📋 **Current Mapping:**
{mapping_json}

📦 **Sample Source Data:**
{sample_data_json}

📚 **Destination Google Sheets Structure:**
{sheets_structure_json}

🎯 **Your Task:**
Analyze the mapping and determine if any transformations are needed:
1. **SPLITTING**: One source field → Multiple destination columns
2. **COMBINING**: Multiple source fields → One destination column

**🚨 CRITICAL - DO NOT SPLIT IF ALREADY SPLIT (combining may still be required):**
Before suggesting any split, check if the source data is ALREADY split:

1. **Name Fields - Check Source Field Names:**
   - If source already has separate fields like: "first_name", "last_name", "firstName", "lastName", "First Name", "Last Name", "fname", "lname"
   - AND they map to "First Name" and "Last Name" destination columns respectively
   - Then DO NOT suggest splitting - the data is already correctly split
   - **But if destination only has a generic Name/Full Name column (see context above), DO combine those source fields into that column** — even if they were omitted from Current Mapping

2. **Address Fields - Check Source Field Names:**
   - If source already has separate fields like: "street", "city", "state", "postal_code", "address_line_1", "address_line_2"
   - AND they map to Street, City, State, PostalCode destination columns respectively
   - Then DO NOT suggest splitting - the data is already correctly split

**Key Analysis Points (ONLY when source is NOT already split):**

1. **Name Field Splitting - ONLY when source contains COMPOUND names**: 
   - If a source field (e.g., "Contact Name", "Full Name", "Name", "Employee Name") is mapped to a SINGLE column (e.g., `"Last Name"`)
   - AND the source field name suggests it contains FULL/COMPOUND names (not already split)
   - AND the sample data value contains multiple words separated by spaces (e.g., "John Doe", "Jane Smith", "Mary Ann Johnson")
   - AND the destination sheet has "First Name", "Last Name", "Middle Name" columns available
   - THEN it SHOULD be split into multiple columns (First Name + Last Name)
   - Example: "Contact Name" with value "John Doe" → split into "First Name"="John", "Last Name"="Doe"

2. **Address Field Splitting - ONLY when source contains COMPOUND addresses**:
   - If a source field (e.g., "Address", "Full Address", "Mailing Address") contains full addresses (e.g., "123 Main St, City, State, ZIP")
   - AND the source field name suggests it contains FULL/COMPOUND addresses (not already split)
   - AND the sample data value contains multiple parts separated by commas or spaces
   - AND destination sheet has Street, City, State, PostalCode, Country columns
   - THEN it SHOULD be split

**3. Field Combining - When source has SEPARATE fields but destination expects COMBINED column**:
   **🚨 CRITICAL: Check the mapping JSON above - look for multiple source fields with the SAME "column" value!**
   
   **How to Detect Combining:**
   1. Look at the mapping JSON structure in the "Current Mapping" section above
   2. Find ALL source fields that have the SAME `"column"` value
   3. If 2 or more source fields map to the SAME destination column, they MUST be combined
   4. Examples of what to look for (field names can be ANY format - "first_name", "First Name", "firstName", etc.):
      ```json
      {{
        "first_name": {{
          "column": "Full Name",
          "sheet": "Sheet1",
          "is_duplicate_check": false
        }},
        "last_name": {{
          "column": "Full Name",
          "sheet": "Sheet1",
          "is_duplicate_check": false
        }}
      }}
      ```
      OR
      ```json
      {{
        "First Name": {{
          "column": "Name",
          "sheet": "Sheet1",
          "is_duplicate_check": false
        }},
        "Last Name": {{
          "column": "Name",
          "sheet": "Sheet1",
          "is_duplicate_check": false
        }}
      }}
      ```
      → In BOTH cases, multiple fields map to the SAME column → YOU MUST COMBINE THEM!
   
   **What to Return for Combining (MANDATORY if detected):**
   - **transformation_rules.fields[]**: MUST include a rule with:
     * `"action": "combine"`
     * `"source_fields": ["First Name", "Last Name"]` (array of EXACT source field names from mapping keys - use the exact keys as they appear!)
     * `"output_field": "Name"` (the EXACT destination column name from the mapping)
     * `"separator": " "` (space to join them)
   
   - **enhanced_mapping**: MUST include ALL source fields that should be combined (copy ALL original properties including "value", "sheet", "is_duplicate_check", etc.):
     * "First Name": {{"column": "Name", "sheet": "Sheet1", "value": "...", "is_duplicate_check": false, "is_combined_field": true, "combine_group": "name", "combine_order": 0}}
     * "Last Name": {{"column": "Name", "sheet": "Sheet1", "value": "...", "is_duplicate_check": false, "is_combined_field": true, "combine_group": "name", "combine_order": 1}}
   
   **CRITICAL**: Use the EXACT field names from the mapping keys (they might be "First Name", "first_name", "firstName", etc. - use them exactly as they appear!)
   
   **Combining Detection Rules:**
   - ✅ DO combine: If multiple source fields have the SAME `"column"` value in the mapping (this is the PRIMARY indicator!)
   - ✅ DO combine: Source has "first_name" and "last_name" both mapping to same "Full Name" destination column
   - ✅ DO combine: Source has separate fields but destination only has "Name" or "Full Name" column
   - ✅ DO combine: Source has "street", "city", "state", "postal_code" but destination has single "Address" column
   - ❌ DO NOT combine: If destination has separate "First Name" and "Last Name" columns AND source fields map to different columns (use splitting instead)
   - ❌ DO NOT combine: If source already has a combined "Full Name" field
   - ❌ DO NOT combine: If source fields map to DIFFERENT destination columns (they should stay separate)

**Summary Rules:**
- ✅ DO split: Source field names like "Contact Name", "Full Name", "Address", "Mailing Address" with compound values
- ❌ DO NOT split: Source field names like "first_name", "last_name", "street", "city" - they're already split!
- ❌ DO NOT split: If source already has separate fields mapping to different destination columns (e.g., "first_name" → "First Name", "last_name" → "Last Name")
- ❌ DO NOT split: If the sample data value is a single word (e.g., "Doe" only, not "John Doe")
- ✅ DO combine: Multiple source fields (e.g., "first_name", "last_name") mapping to same destination column (e.g., "Full Name")
- ❌ DO NOT combine: If destination has separate columns available (e.g., "First Name", "Last Name") - use splitting instead

**Output Format:**
Return a JSON object with two keys:
1. `transformation_rules`: Transformation rules following the format below
2. `enhanced_mapping`: Mapping entries with the SAME key as original, but value is an ARRAY of mapping objects when splitting is needed

**IMPORTANT - Enhanced Mapping Structure:**
- Use the EXACT same source field key from the original mapping
- **For Splitting**: If a field needs to be split, replace its single mapping object with an ARRAY of mapping objects
  - Each object in the array should have `"is_split_field": true` flag
- **For Combining**: If fields need to be combined, include ALL source fields in enhanced_mapping with combine flags
  - Each field should have `"is_combined_field": true`, `"combine_group"`, and `"combine_order"`
- **If NO transformations are needed (no splitting AND no combining), return empty object `{{}}`**
- **If you detect combining, you MUST include it in enhanced_mapping - do NOT return empty!**

**Transformation Rules Format:**
```json
{{
  "transformation_rules": {{
    "fields": [
      {{
        "source_field": "Contact Name",
        "action": "split_2",
        "split_char": " ",
        "output_fields": ["First Name", "Last Name"]
      }},
      {{
        "source_fields": ["first_name", "last_name"],
        "action": "combine",
        "separator": " ",
        "output_field": "Full Name"
      }}
    ]
  }},
  "enhanced_mapping": {{
    "Contact Name": [
      {{
        "column": "First Name",
        "sheet": "Sheet1",
        "position": 0,
        "is_duplicate_check": false,
        "is_split_field": true
      }},
      {{
        "column": "Last Name",
        "sheet": "Sheet1",
        "position": 1,
        "is_duplicate_check": true,
        "is_split_field": true
      }}
    ],
    "first_name": {{
      "column": "Full Name",
      "sheet": "Sheet1",
      "position": 0,
      "is_duplicate_check": false,
      "is_combined_field": true,
      "combine_group": "name",
      "combine_order": 0
    }},
    "last_name": {{
      "column": "Full Name",
      "sheet": "Sheet1",
      "position": 0,
      "is_duplicate_check": false,
      "is_combined_field": true,
      "combine_group": "name",
      "combine_order": 1
    }}
  }}
}}
```

**Key Rules for Enhanced Mapping:**
- **For Splitting**: Use the EXACT source field key from current mapping (e.g., "Contact Name", not "Contact Name (First Name)")
  - Value must be an ARRAY when splitting (even if splitting into 2 columns, it's still an array)
  - Include `"is_split_field": true` in EACH mapping object within the array
  - Copy all properties from original mapping (like `sheet`, `is_duplicate_check`, etc.) to each array item
  
- **For Combining**: Update the mapping entries for EACH source field that should be combined
  - Use the EXACT source field keys from current mapping (e.g., "first_name", "last_name")
  - Each source field mapping should point to the SAME destination column
  - Add `"is_combined_field": true` to each mapping entry
  - Add `"combine_group": "name"` (or similar identifier) to group fields that should be combined together
  - Add `"combine_order": 0, 1, 2...` to specify the order in which fields should be combined
  - The destination column should be the same for all fields in the combine_group
  - Use `column` (not `field`) for Google Sheets column names
  - Include `position` for column ordering
  
- **If no transformations are needed, return empty enhanced_mapping object `{{}}`**
- **Only include fields in enhanced_mapping that NEED transformation (splitting or combining)**

**Actions Available:**
- `split_2`: Split into 2 parts (e.g., First Name, Last Name)
- `split_3`: Split into 3 parts (e.g., First Name, Middle Name, Last Name)
- `split_address`: Split address into components (Street, City, State, PostalCode, Country)
- `combine`: Combine multiple source fields into one destination column (e.g., first_name + last_name → Full Name)

**Important Rules:**
- Only suggest splitting if the destination sheet has the component columns available (check sheets_structure)
- Use exact column names from sheets_structure
- If no transformations are needed, return empty arrays: `{{"transformation_rules": {{"fields": []}}, "enhanced_mapping": {{}}}}`
- Use exact field names from the mapping keys as `source_field` in transformation_rules

Return ONLY valid JSON, no markdown or explanations.
"""
        
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.GOOGLE_SHEETS_MAPPING,
            messages=[
                {
                    "role": "system",
                    "content": "You are Archian AI, specializing in data transformation analysis for Google Sheets field mappings."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.0
        )
        
        reply = response.choices[0].message.content
        result = parse_openai_json_response(reply)
        
        # Ensure we have the expected structure
        transformation_rules = result.get("transformation_rules", {"fields": []})
        enhanced_mapping = result.get("enhanced_mapping", {})

        from utils.mapping_transform_context import apply_post_llm_transform_reconciliation

        reconciled = apply_post_llm_transform_reconciliation(
            mapping=mapping or {},
            flat_data=flat_data or {},
            destination_name_shape=dest_name_shape,
            destination_type="google_sheets",
            transformation_rules=transformation_rules,
            enhanced_mapping=enhanced_mapping,
            sample_data=sample_data,
            base_container=str(base_sheet) if base_sheet else None,
        )
        transformation_rules = reconciled["transformation_rules"]
        enhanced_mapping = reconciled.get("enhanced_mapping") or {}

        return {
            "transformation_rules": transformation_rules,
            "enhanced_mapping": enhanced_mapping,
            "mapping_audit": mapping_audit_dict,
        }
        
    except Exception as e:
        print(f"⚠️ Error analyzing Google Sheets transformations: {e}")
        import traceback
        traceback.print_exc()
        # Return empty on error - mapping will work without transformations
        return {
            "transformation_rules": {"fields": []},
            "enhanced_mapping": {},
            "mapping_audit": mapping_audit_dict,
        }


async def call_google_sheets_mapping_agent(flat_data: dict, document_type: str, spreadsheet_name: str, sheets_structure: dict, mapping_context_summary: Optional[str] = None):
    """
    Use AI to intelligently map extracted fields to Google Sheets columns.
    
    Args:
        flat_data: First record from extracted data
        document_type: Category/document type
        spreadsheet_name: Name of the spreadsheet
        sheets_structure: Dictionary of sheets and their columns
        
    Returns:
        dict: Mapping of extracted fields to Google Sheets columns
    """
    try:
        import json
        import asyncio
        
        # Use real data directly for mapping analysis (no masking)
        # Data is only sent for analysis, not stored, and data sharing with OpenAI is disabled
        
        # Determine primary sheet (first sheet with most columns, or first sheet)
        primary_sheet = max(sheets_structure.items(), key=lambda x: len(x[1]["columns"]))[0] if sheets_structure else "Sheet1"
        
        # Build example mapping
        field_names = list(flat_data.keys())
        first_field = field_names[0] if len(field_names) > 0 else "email"
        second_field = field_names[1] if len(field_names) > 1 else "name"
        
        example_mapping = json.dumps({
            first_field: {
                "sheet": primary_sheet,
                "column": "Email",
                "value": flat_data.get(first_field, ""),
                "is_duplicate_check": True
            },
            second_field: {
                "sheet": primary_sheet,
                "column": "Full Name",
                "value": flat_data.get(second_field, ""),
                "is_duplicate_check": False
            }
        }, indent=2)
        
        # Escape curly braces for f-string
        example_mapping_safe = example_mapping.replace("{", "{{").replace("}", "}}")
        
        mapping_context_section = ""
        if mapping_context_summary:
            mapping_context_section = f"""
📝 **User's Extraction Request Context:**
{mapping_context_summary}

Use this context to:
- Prioritize sheets/columns mentioned by the user
- Understand the user's intent and requirements
- Make more accurate mapping decisions
"""
        prompt = f"""
You are a Google Sheets data mapping expert. Map extracted data fields to the most appropriate Google Sheets columns.

✅ **DATA USAGE**: The data below contains real field names and values for mapping analysis.

📄 Document Type: {document_type}
📊 Spreadsheet: {spreadsheet_name}
{mapping_context_section}

🚨 **CRITICAL FIELD NAME REQUIREMENTS**:
- Each key in the final JSON must be the EXACT FIELD NAME from the extracted data
- Use field names exactly as they appear in the sample data
- Do NOT rename or transform field names

📋 Available Sheets and Columns:
{json.dumps(sheets_structure, indent=2)}

🔍 **Duplicate Check Tagging** (Important for Updates):
For every field in the final mapping:
1. Determine if the field should be used to find existing rows before updating:
   - If yes → add `"is_duplicate_check": true`
   - If no → add `"is_duplicate_check": false`

2. Best practices for duplicate checking:
   - Prefer unique identifiers: Email, Phone, ID numbers, Employee ID
   - Avoid non-unique fields: Notes, Description, Address
   - For person data: Email > Phone > Full Name
   - For company data: Company Name, Tax ID, Account Number

3. Your output JSON **must include `is_duplicate_check` for every field**.

✅ Example Output Format:
{example_mapping_safe}

📦 Extracted Data for Mapping:
{json.dumps(flat_data, indent=2)}

🎯 Mapping Rules:
1. **JSON keys MUST be exact extraction source field names** from the sample record (e.g. `email`, `phone`, `first_name`) — NEVER use destination sheet column names like `Email` or `Mobile Number` as keys.
2. **Only include a source field when the destination sheet has a clearly matching column** (by name or obvious synonym).
3. **If no suitable destination column exists, omit that source field entirely** — do NOT map it to an unrelated column.
4. **Exception — name parts → single Name column:** When the destination has a generic **Name** / **Full Name** column but NOT separate first/last columns, map each source first-name and last-name field to that same **Name** column (the transform step will combine them).
5. If multiple sheets exist, choose the most appropriate sheet for each field.
6. Prioritize exact name matches, then semantic similarity among available columns only.
7. Include the `is_duplicate_check` flag for every field you include.
8. Return ONLY valid JSON, no markdown, no explanations.

Return the mapping as a single flat JSON object.
"""

        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.GOOGLE_SHEETS_MAPPING,
            messages=[
                {"role": "system", "content": "You are a Google Sheets data mapping expert. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        
        # Parse response using centralized JSON parsing function
        response_text = response.choices[0].message.content.strip()
        
        from utils.openai_helpers import parse_openai_json_response
        try:
            mapping = parse_openai_json_response(response_text)
            print(f"✅ Google Sheets mapping created: {len(mapping)} fields mapped")
            return mapping
        except (ValueError, json.JSONDecodeError) as e:
            print(f"❌ Error parsing Google Sheets mapping JSON response: {e}")
            print(f"⚠️ Response (first 200 chars): {response_text[:200]}")
            raise
        
    except Exception as e:
        print(f"❌ Error in call_google_sheets_mapping_agent: {e}")
        import traceback
        traceback.print_exc()
        raise