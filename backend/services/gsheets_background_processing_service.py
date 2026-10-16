"""
Google Sheets Background Processing Service

This service handles background analysis and categorization of Google Spreadsheets,
mirroring the functionality of SQL background processing.
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from db.connection import database
from services.entitlements import get_entitlements
from services.gsheets_ai_categorization_service import GoogleSheetsAICategorizationService
from services.google_sheets_service import GoogleSheetsService, GoogleSheetsAuthError
from services.error_explanation_service import explain_error
from utils.email_utils import send_spreadsheet_sync_complete_email
from utils.google_sheets_errors import is_google_sheets_reauth_error


class GoogleSheetsBackgroundProcessingService:
    """Service for background processing of Google Sheets analysis"""
    
    def __init__(self):
        self.ai_service = GoogleSheetsAICategorizationService()
    
    async def process_spreadsheet_analysis(
        self, 
        google_sheets_connection_id: str,
        spreadsheet_id: str,
        spreadsheet_name: str,
        user_id: str, 
        user_email: str
    ):
        """
        Background job to analyze all sheets in a spreadsheet and categorize them
        Similar to SQL process_database_analysis()
        
        Args:
            google_sheets_connection_id: Google Sheets connection UUID
            spreadsheet_id: Google Spreadsheet ID
            spreadsheet_name: Human-readable spreadsheet name
            user_id: User UUID
            user_email: User email for notification
        """
        try:
            print(f"📊 Starting spreadsheet analysis: {spreadsheet_name}")
            print(f"   Spreadsheet ID: {spreadsheet_id}")
            print(f"   User ID: {user_id}")
            
            # Get Google Sheets connection details
            connection = await self.get_connection_details(google_sheets_connection_id, user_id)
            if not connection:
                raise Exception("Google Sheets connection not found")

            # Previous schema for delta billing (charge only for new sheets/columns on re-analysis)
            prev_sheet_columns: Dict[str, int] = {}
            try:
                rows = await database.fetch_all(
                    """
                    SELECT sheet_name, header_schema FROM gsheets_extracted_schemas
                    WHERE google_sheets_connection_id = :conn_id AND spreadsheet_id = :spreadsheet_id
                    """,
                    values={"conn_id": google_sheets_connection_id, "spreadsheet_id": spreadsheet_id},
                )
                for row in rows or []:
                    name = (row.get("sheet_name") if isinstance(row, dict) else getattr(row, "sheet_name", None)) or ""
                    schema = row.get("header_schema") if isinstance(row, dict) else getattr(row, "header_schema", None)
                    if isinstance(schema, str):
                        try:
                            schema = json.loads(schema) if schema else {}
                        except Exception:
                            schema = {}
                    headers_list = schema.get("headers", []) if isinstance(schema, dict) else []
                    prev_sheet_columns[name] = len(headers_list) if headers_list else 0
            except Exception as e:
                print(f"⚠️ [Sheets Analysis] Could not load previous schema for delta: {e}")

            # Schema analysis is included; no credit check

            # Create Google Sheets service instance
            sheets_service = GoogleSheetsService(
                access_token=connection['access_token'],
                refresh_token=connection['refresh_token']
            )
            
            # Get spreadsheet metadata
            print(f"📄 Fetching spreadsheet metadata...")
            metadata = await sheets_service.get_spreadsheet_metadata(spreadsheet_id)
            sheets = metadata.get('sheets', [])
            
            total_sheets = len(sheets)
            processed_sheets = 0
            total_columns = 0
            current_sheet_columns: Dict[str, int] = {}  # for delta billing

            print(f"📊 Found {total_sheets} sheet(s) in spreadsheet")

            # Loop through each sheet
            for sheet in sheets:
                try:
                    sheet_name = sheet['properties']['title']
                    print(f"📄 Processing sheet: {sheet_name}")
                    
                    # Get headers (first row)
                    first_row_range = f"'{sheet_name}'!A1:ZZ1"
                    header_result = await sheets_service.read_spreadsheet(spreadsheet_id, first_row_range)
                    
                    if not header_result or not header_result.get('success') or not header_result.get('headers'):
                        print(f"⚠️  No headers found in {sheet_name}, skipping")
                        continue
                    
                    # Filter out empty headers
                    headers = [
                        {
                            "name": str(h).strip(), 
                            "type": "string", 
                            "position": idx
                        } 
                        for idx, h in enumerate(header_result['headers']) 
                        if h and str(h).strip()
                    ]
                    
                    if not headers:
                        print(f"⚠️  No valid headers found in {sheet_name}, skipping")
                        continue

                    col_count = len(headers)
                    total_columns += col_count
                    current_sheet_columns[sheet_name] = col_count
                    print(f"   Found {col_count} headers: {[h['name'] for h in headers]}")
                    
                    # Get sample data (first 10 rows) for better categorization
                    sample_range = f"'{sheet_name}'!A2:ZZ11"
                    sample_result = await sheets_service.read_spreadsheet(spreadsheet_id, sample_range)
                    
                    sample_data = None
                    if sample_result and sample_result.get('success') and sample_result.get('data'):
                        # Convert rows to dict format
                        sample_data = []
                        for row_dict in sample_result['data'][:10]:  # Max 10 rows
                            if row_dict:  # Only add non-empty rows
                                sample_data.append(row_dict)
                        
                        print(f"   Extracted {len(sample_data)} sample rows")
                    
                    # Store schema; labeling runs in background via EntityLabelingService
                    await self.store_sheet_analysis(
                        user_id=user_id,
                        google_sheets_connection_id=google_sheets_connection_id,
                        spreadsheet_id=spreadsheet_id,
                        spreadsheet_name=spreadsheet_name,
                        sheet_name=sheet_name,
                        header_schema={"headers": headers},
                        ai_categorization=None,
                    )

                    from services.labeling.schedule import schedule_entity_labeling_fire_and_forget

                    entity_key = f"{spreadsheet_id}::{sheet_name}"
                    schedule_entity_labeling_fire_and_forget(
                        user_id=user_id,
                        connector_type="google_sheets",
                        connector_ref={"connection_id": google_sheets_connection_id},
                        entity_keys=[entity_key],
                    )
                    
                    processed_sheets += 1
                    print(f"✅ Stored schema and queued labeling for {sheet_name}")
                    
                except Exception as e:
                    print(f"❌ Error processing sheet {sheet_name}: {e}")
                    import traceback
                    print(f"   Traceback: {traceback.format_exc()}")
                    continue

            # Deduct credits only for new sheets/columns (delta) on re-analysis; skip if unlimited
            user_plan = await get_entitlements().get_user_plan(user_id)
            credits_per_month = user_plan.get("credits_per_month") if user_plan else None
            if credits_per_month is not None and credits_per_month != -1:
                from utils.credit_config import compute_connector_schema_credits
                prev_sheets = set(prev_sheet_columns.keys())
                current_sheets = set(current_sheet_columns.keys())
                new_sheets = current_sheets - prev_sheets
                new_entities = len(new_sheets)
                new_fields = sum(current_sheet_columns[s] for s in new_sheets) + sum(
                    max(0, current_sheet_columns[s] - prev_sheet_columns.get(s, 0))
                    for s in (current_sheets & prev_sheets)
                )
                if new_entities > 0 or new_fields > 0:
                    # Schema analysis is free; no credit deduction
                    print(f"✅ [Sheets Analysis] Schema delta: {new_entities} new sheets, {new_fields} new columns (no charge)")
                else:
                    print(f"✅ [Sheets Analysis] No schema delta; no credits charged")

            try:
                from services.labeling.readiness_service import on_metadata_sync_complete

                await on_metadata_sync_complete(
                    user_id=str(user_id),
                    connector_type="google_sheets",
                    connector_ref={"connection_id": str(google_sheets_connection_id)},
                )
            except Exception as readiness_err:
                print(f"⚠️ [Sheets Analysis] Readiness hook failed: {readiness_err}")

            print(f"🎉 Spreadsheet analysis complete: {processed_sheets}/{total_sheets} sheets processed")
            
        except Exception as e:
            print(f"❌ Spreadsheet analysis failed: {e}")
            import traceback
            print(f"❌ Full error traceback: {traceback.format_exc()}")

            reconnect_required = isinstance(e, GoogleSheetsAuthError) or is_google_sheets_reauth_error(e)
            if reconnect_required:
                email_body = (
                    "Please open Archian AI → Connections, disconnect Google Sheets if needed, "
                    "and connect your Google account again so we can access your spreadsheets."
                )
            else:
                raw = str(e)
                explained = await explain_error(
                    raw,
                    "google_sheets",
                    context={"operation": "spreadsheet re-sync and analysis"},
                )
                parts = []
                if explained.get("explanation"):
                    parts.append(explained["explanation"])
                if explained.get("solution"):
                    parts.append("What to try:\n" + explained["solution"])
                email_body = "\n\n".join(parts) if parts else raw

            try:
                await send_spreadsheet_sync_complete_email(
                    user_email,
                    spreadsheet_name,
                    0,
                    error=email_body,
                    reconnect_required=reconnect_required,
                )
            except Exception as email_error:
                print(f"Failed to send error email: {email_error}")
    
    async def get_connection_details(
        self, 
        google_sheets_connection_id: str, 
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get Google Sheets connection details from database"""
        try:
            result = await database.fetch_one(
                """
                SELECT * FROM google_sheets_connections 
                WHERE id = :connection_id AND user_id = :user_id AND is_active = true
                """,
                {
                    "connection_id": google_sheets_connection_id,
                    "user_id": user_id
                }
            )
            return dict(result) if result else None
        except Exception as e:
            print(f"Error fetching connection details: {e}")
            return None
    
    async def store_sheet_analysis(
        self,
        user_id: str,
        google_sheets_connection_id: str,
        spreadsheet_id: str,
        spreadsheet_name: str,
        sheet_name: str,
        header_schema: Dict[str, Any],
        ai_categorization: Dict[str, Any]
    ):
        """
        Store sheet analysis in gsheets_extracted_schemas table
        Similar to SQL store_table_analysis()
        """
        try:
            # Check if sheet already exists
            existing = await database.fetch_one(
                """
                SELECT id FROM gsheets_extracted_schemas 
                WHERE google_sheets_connection_id = :conn_id 
                  AND spreadsheet_id = :spreadsheet_id 
                  AND sheet_name = :sheet_name
                """,
                {
                    "conn_id": google_sheets_connection_id,
                    "spreadsheet_id": spreadsheet_id,
                    "sheet_name": sheet_name
                }
            )
            
            if existing:
                # Update existing record
                print(f"   Updating existing record for {sheet_name}")
                await database.execute(
                    """
                    UPDATE gsheets_extracted_schemas 
                    SET header_schema = :header_schema,
                        ai_categorization = :ai_categorization,
                        spreadsheet_name = :spreadsheet_name,
                        extracted_at = NOW()
                    WHERE id = :id
                    """,
                    {
                        "id": existing["id"],
                        "header_schema": json.dumps(header_schema),
                        "ai_categorization": json.dumps(ai_categorization),
                        "spreadsheet_name": spreadsheet_name
                    }
                )
            else:
                # Insert new record
                print(f"   Creating new record for {sheet_name}")
                await database.execute(
                    """
                    INSERT INTO gsheets_extracted_schemas 
                    (id, user_id, google_sheets_connection_id, spreadsheet_id, spreadsheet_name, 
                     sheet_name, header_schema, ai_categorization, extracted_at)
                    VALUES (:id, :user_id, :conn_id, :spreadsheet_id, :spreadsheet_name,
                            :sheet_name, :header_schema, :ai_categorization, NOW())
                    """,
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": user_id,
                        "conn_id": google_sheets_connection_id,
                        "spreadsheet_id": spreadsheet_id,
                        "spreadsheet_name": spreadsheet_name,
                        "sheet_name": sheet_name,
                        "header_schema": json.dumps(header_schema),
                        "ai_categorization": json.dumps(ai_categorization)
                    }
                )
                
            print(f"   ✅ Stored in database")
            
        except Exception as e:
            print(f"❌ Error storing sheet analysis: {e}")
            import traceback
            print(f"   Traceback: {traceback.format_exc()}")



