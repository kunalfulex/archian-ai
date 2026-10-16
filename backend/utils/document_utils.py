"""
Document utility functions to avoid circular imports
"""
from uuid import UUID
from db.connection import database
import json
from typing import Optional

from db.extracted_schema import _parse_gsheets_header_to_columns
from services.labeling.catalog_helpers import fetch_semantics_map, merge_catalog_entry


async def get_dynamic_document_type_categories(
    user_id: UUID, 
    destination: str = "salesforce",
    destination_name: Optional[str] = None
) -> list[str]:
    """
    Get dynamic categories/schemas based on destination type.
    
    Args:
        user_id: User ID
        destination: Target destination ("salesforce", "google_sheets", "sql")
        destination_name: Optional specific destination name (org name, spreadsheet name, connector name)
        
    Returns:
        List of category dictionaries with structure appropriate for the destination
    """
    
    if destination == "salesforce":
        # Step 1: Get org_id from salesforce_tokens
        # If destination_name is provided, filter by org_display_name
        if destination_name and destination_name != "unknown":
            org_record = await database.fetch_one(
                """
                SELECT id::text AS connector_id, org_id
                FROM salesforce_tokens
                WHERE user_id = :user_id
                  AND LOWER(TRIM(org_display_name)) = LOWER(TRIM(:destination_name))
                ORDER BY is_default DESC NULLS LAST, issued_at DESC
                LIMIT 1
                """,
                values={"user_id": str(user_id), "destination_name": destination_name}
            )
            if not org_record:
                org_record = await database.fetch_one(
                    """
                    SELECT id::text AS connector_id, org_id
                    FROM salesforce_tokens
                    WHERE user_id = :user_id AND is_default = true
                    ORDER BY issued_at DESC
                    LIMIT 1
                    """,
                    values={"user_id": str(user_id)}
                )
        else:
            org_record = await database.fetch_one(
                """
                SELECT id::text AS connector_id, org_id
                FROM salesforce_tokens
                WHERE user_id = :user_id AND is_default = true
                ORDER BY issued_at DESC
                LIMIT 1
                """,
                values={"user_id": str(user_id)}
            )
        if not org_record:
            org_record = await database.fetch_one(
                """
                SELECT id::text AS connector_id, org_id
                FROM salesforce_tokens
                WHERE user_id = :user_id
                ORDER BY issued_at DESC
                LIMIT 1
                """,
                values={"user_id": str(user_id)}
            )

        if not org_record:
            print("No Salesforce org_id found for user")
            return []

        org_id = org_record["org_id"]
        connector_id = org_record["connector_id"]

        # Step 2: Use org_id to fetch categories from salesforce_objects
        query = """
            SELECT object_api_name, category, category_description
            FROM salesforce_objects
            WHERE category IS NOT NULL
              AND org_id = :org_id
              AND user_id = :user_id
            ORDER BY category;
        """
        records = await database.fetch_all(query, values={"org_id": org_id, "user_id": str(user_id)})

        semantics_map = await fetch_semantics_map(
            user_id=str(user_id),
            connector_type="salesforce",
            connector_instance_id=str(connector_id),
        )

        categories = []
        for r in records:
            if not r["category"]:
                continue
            obj_name = r["object_api_name"]
            entry = {
                "object_name": obj_name,
                "category": r["category"],
                "description": r["category_description"],
            }
            categories.append(
                merge_catalog_entry(entry, semantics=semantics_map.get(obj_name))
            )

        return categories
    
    elif destination == "google_sheets":
        # Fetch Google Sheets schemas from gsheets_extracted_schemas
        try:
            # If destination_name is provided, filter by spreadsheet name (case-insensitive matching)
            if destination_name and destination_name != "unknown":
                query = """
                    SELECT spreadsheet_id, spreadsheet_name, sheet_name, header_schema, ai_categorization,
                           google_sheets_connection_id
                    FROM gsheets_extracted_schemas
                    WHERE user_id = :user_id
                      AND LOWER(TRIM(spreadsheet_name)) = LOWER(TRIM(:spreadsheet_name))
                    ORDER BY extracted_at DESC
                """
                records = await database.fetch_all(
                    query, 
                    values={"user_id": str(user_id), "spreadsheet_name": destination_name}
                )

                if not records:
                    query = """
                        SELECT spreadsheet_id, spreadsheet_name, sheet_name, header_schema, ai_categorization,
                               google_sheets_connection_id
                        FROM gsheets_extracted_schemas
                        WHERE user_id = :user_id
                        ORDER BY extracted_at DESC
                        LIMIT 20
                    """
                    records = await database.fetch_all(query, values={"user_id": str(user_id)})
                
                if not records:
                    print(f"   ❌ No Google Sheets records found")
                    return []

            else:
                # Get all spreadsheets for this user
                query = """
                    SELECT spreadsheet_id, spreadsheet_name, sheet_name, header_schema, ai_categorization,
                           google_sheets_connection_id
                    FROM gsheets_extracted_schemas
                    WHERE user_id = :user_id
                    ORDER BY extracted_at DESC
                    LIMIT 20
                """
                records = await database.fetch_all(query, values={"user_id": str(user_id)})
            
            # Parse and return categories
            categories = []
            semantics_cache: dict = {}
            for r in records:
                try:
                    row = dict(r)
                    cols = _parse_gsheets_header_to_columns(row.get("header_schema"))
                    fields = list(cols.keys())

                    conn_id = str(row.get("google_sheets_connection_id") or "")
                    sid = row.get("spreadsheet_id")
                    sheet_name = row.get("sheet_name")
                    entity_key = f"{sid}::{sheet_name}" if sid and sheet_name else ""
                    semantics = None
                    if conn_id and entity_key:
                        if conn_id not in semantics_cache:
                            semantics_cache[conn_id] = await fetch_semantics_map(
                                user_id=str(user_id),
                                connector_type="google_sheets",
                                connector_instance_id=conn_id,
                            )
                        semantics = semantics_cache[conn_id].get(entity_key)

                    entry = {
                        "spreadsheet_name": row["spreadsheet_name"],
                        "category": "Spreadsheet",
                        "description": f"Data from {row['spreadsheet_name']} - {row['sheet_name']}",
                        "fields": fields,
                    }
                    categories.append(merge_catalog_entry(entry, semantics=semantics))
                except Exception as e:
                    print(f"Warning: Could not parse Google Sheets schema: {e}")
                    continue
            
            return categories
            
        except Exception as e:
            print(f"Error fetching Google Sheets categories: {e}")
            return []
    
    elif destination == "sql":
        # Fetch SQL schemas from sql_extracted_schemas
        try:
            # If destination_name is provided, filter by connector name (case-insensitive matching)
            if destination_name and destination_name != "unknown":
                # First get connector_id from connector name
                connector = await database.fetch_one(
                    """
                    SELECT id FROM sql_connectors
                    WHERE user_id = :user_id AND LOWER(TRIM(name)) = LOWER(TRIM(:connector_name))
                    LIMIT 1
                    """,
                    values={"user_id": str(user_id), "connector_name": destination_name}
                )
                
                if not connector:
                    print(f"No SQL connector found with name: {destination_name}")
                    return []
                
                query = """
                    SELECT 
                        ses.table_name, 
                        ses.schema_data, 
                        ses.ai_categorization,
                        ses.connector_id,
                        sc.name AS connector_name
                    FROM sql_extracted_schemas ses
                    JOIN sql_connectors sc 
                        ON sc.id = ses.connector_id
                    WHERE ses.user_id = :user_id
                      AND ses.connector_id = :connector_id
                    ORDER BY ses.extracted_at DESC
                """
                records = await database.fetch_all(
                    query, 
                    values={"user_id": str(user_id), "connector_id": connector["id"]}
                )
                connector_id_str = str(connector["id"])
                semantics_map = await fetch_semantics_map(
                    user_id=str(user_id),
                    connector_type="sql",
                    connector_instance_id=connector_id_str,
                )
            else:
                # Get all SQL schemas for this user
                query = """
                    SELECT 
                        ses.table_name, 
                        ses.schema_data, 
                        ses.ai_categorization, 
                        ses.connector_id, 
                        sc.name AS connector_name
                    FROM sql_extracted_schemas ses
                    JOIN sql_connectors sc 
                        ON sc.id = ses.connector_id
                    WHERE ses.user_id = :user_id
                    ORDER BY ses.extracted_at DESC
                    LIMIT 20
                """
                records = await database.fetch_all(query, values={"user_id": str(user_id)})

            categories = []
            semantics_cache: dict = {}
            if destination_name and destination_name != "unknown":
                semantics_cache[connector_id_str] = semantics_map

            for r in records:
                try:
                    schema_data = json.loads(r["schema_data"]) if r["schema_data"] else {}
                    
                    # Extract field names from schema_data
                    fields = []
                    if isinstance(schema_data, dict):
                        cols = schema_data.get("columns")
                        if isinstance(cols, list):
                            for col in cols:
                                if isinstance(col, dict):
                                    name = col.get("column_name") or col.get("name")
                                    if name:
                                        fields.append(name)
                                elif isinstance(col, str):
                                    fields.append(col)
                        else:
                            fields = [k for k in schema_data.keys() if k != "columns" and k != "foreign_keys"]
                    elif isinstance(schema_data, list):
                        fields = [field for field in schema_data if isinstance(field, str)]

                    table_name = r["table_name"]
                    cid = str(r.get("connector_id") or "")
                    row_semantics = None
                    if cid:
                        if cid not in semantics_cache:
                            semantics_cache[cid] = await fetch_semantics_map(
                                user_id=str(user_id),
                                connector_type="sql",
                                connector_instance_id=cid,
                            )
                        row_semantics = semantics_cache[cid].get(table_name)

                    entry = {
                        "table_name": table_name,
                        "category": "Database Table",
                        "description": f"Data from table {table_name}",
                        "fields": fields,
                    }
                    connector_name = r.get("connector_name")
                    if connector_name:
                        entry["connector_name"] = connector_name

                    categories.append(
                        merge_catalog_entry(entry, semantics=row_semantics)
                    )
                except Exception as e:
                    print(f"Warning: Could not parse SQL schema: {e}")
                    continue
            
            return categories
            
        except Exception as e:
            print(f"Error fetching SQL categories: {e}")
            return []
    
    else:
        # Unknown destination
        print(f"Warning: Unknown destination type: {destination}")
        return []
