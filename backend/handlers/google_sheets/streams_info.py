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

from handlers.google_sheets.ai_prompts import classify_google_sheets_info_request

# In-memory storage for pending update confirmations
# In production, this should be in Redis or database with TTL
pending_google_sheets_updates = {}


def _coerce_ai_categorization_dict(raw: Any) -> Dict[str, Any]:
    """Parse gsheets_extracted_schemas.ai_categorization; JSON null → empty dict."""
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    else:
        parsed = raw
    return parsed if isinstance(parsed, dict) else {}


async def handle_basic_spreadsheet_info(
    sheet_details: dict,
    google_sheets_connection: dict,
    user_id: str,
    *,
    prompt: Optional[str] = None,
):
    """
    Handle basic spreadsheet information requests (structure, columns, record count, data types)
    Uses database first for cached info, falls back to API for record count
    """
    async def generate():
        try:
            from services.google_sheets_service import GoogleSheetsService
            from db.connection import database
            from services.google_sheets.response_summary import build_describe_completion

            spreadsheet_name = sheet_details["spreadsheet_name"]
            sheet_name = sheet_details.get("sheet_name") or "Sheet1"
            account_email = google_sheets_connection.get("google_account_email")
            record_count: Optional[int] = None
            column_types: Dict[str, str] = {}
            document_type: Optional[str] = None
            document_description: Optional[str] = None

            from services.planning.connector_info_context import (
                build_connector_info_enrichment,
                resolve_google_sheets_connector,
            )

            gs_connector = await resolve_google_sheets_connector(
                str(user_id),
                connection_id=sheet_details.get("google_sheets_connection_id")
                or google_sheets_connection.get("id"),
                spreadsheet_id=sheet_details.get("spreadsheet_id"),
                spreadsheet_name=spreadsheet_name,
            )
            if gs_connector:
                entity_key = f"{sheet_details.get('spreadsheet_id')}::{sheet_name}"
                enrichment, info_meta = await build_connector_info_enrichment(
                    str(user_id),
                    gs_connector,
                    prompt=prompt,
                    focus_entity_keys=[entity_key] if sheet_details.get("spreadsheet_id") else None,
                )
                if enrichment.strip():
                    yield enrichment + "\n"
                sem = (info_meta.get("semantics_map") or {}).get(entity_key)
                if sem and isinstance(sem, dict):
                    primary = sem.get("primary") if isinstance(sem.get("primary"), dict) else {}
                    if not document_type:
                        document_type = primary.get("category") or sem.get("document_type")
                    if not document_description:
                        document_description = primary.get("description") or sem.get(
                            "document_description"
                        )
            
            # Initialize Google Sheets service
            sheets_service = GoogleSheetsService(
                access_token=google_sheets_connection['access_token'],
                refresh_token=google_sheets_connection['refresh_token'],
                expires_at=google_sheets_connection['expires_at']
            )
            
            # Try to get cached info from database first
            categorization = await database.fetch_one(
                """
                SELECT header_schema, ai_categorization FROM gsheets_extracted_schemas 
                WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id AND sheet_name = :sheet_name
                """,
                {
                    "user_id": str(user_id),
                    "spreadsheet_id": sheet_details['spreadsheet_id'],
                    "sheet_name": sheet_name,
                }
            )
            
            # Try to use cached headers and data types from database
            headers = []
            cached_data_types = {}
            
            if categorization:
                try:
                    header_schema_raw = categorization['header_schema']
                    if header_schema_raw:
                        header_schema = json.loads(header_schema_raw) if isinstance(header_schema_raw, str) else header_schema_raw
                        
                        # Parse based on schema structure
                        if isinstance(header_schema, dict) and 'headers' in header_schema:
                            # New format: {"headers": [{"name": "First name", "type": "string", ...}, ...]}
                            headers = [h['name'] for h in header_schema['headers'] if isinstance(h, dict) and 'name' in h]
                            # Also extract data types
                            for h in header_schema['headers']:
                                if isinstance(h, dict) and 'name' in h and 'type' in h:
                                    cached_data_types[h['name']] = h['type']
                        elif isinstance(header_schema, dict):
                            # Old format: {"First name": "string", "last name": "string", ...}
                            headers = list(header_schema.keys())
                            cached_data_types = header_schema
                        elif isinstance(header_schema, list):
                            # List format: ["First name", "last name", ...]
                            headers = header_schema
                        else:
                            headers = []
                        
                except (KeyError, TypeError, json.JSONDecodeError) as e:
                    print(f"⚠️ Could not parse cached headers: {e}")
                    headers = []
                    cached_data_types = {}
            
            if not headers:
                # No cache - read from API
                header_result = await sheets_service.read_spreadsheet(
                    sheet_details['spreadsheet_id'], 
                    range_name='A1:ZZ1',
                    sheet_name=sheet_name,
                )
                
                if header_result.get('success') and header_result.get('headers'):
                    headers = [h for h in header_result['headers'] if h and str(h).strip()]
            
            if headers:
                sample_result = await sheets_service.read_spreadsheet(
                    sheet_details["spreadsheet_id"],
                    range_name="A2:ZZ10000",
                    sheet_name=sheet_name,
                )

                if sample_result.get("success") and sample_result.get("data"):
                    all_data_rows = sample_result["data"]
                    record_count = len(
                        [
                            row
                            for row in all_data_rows
                            if any(str(v).strip() for v in row.values())
                        ]
                    )

                    if cached_data_types:
                        for col in headers:
                            col_type = cached_data_types.get(col, "unknown")
                            column_types[col] = (
                                col_type.capitalize() if col_type else "Unknown"
                            )
                    elif sample_result["data"]:
                        sample_row = sample_result["data"][0]
                        if not isinstance(sample_row, dict):
                            sample_row = {}
                        for header in headers:
                            value = sample_row.get(header, "")
                            if not value:
                                continue
                            if "@" in str(value):
                                column_types[header] = "Email"
                            elif (
                                any(char.isdigit() for char in str(value))
                                and len(str(value)) >= 10
                            ):
                                column_types[header] = "Phone"
                            elif "/" in str(value) or "-" in str(value):
                                column_types[header] = "Date"
                            else:
                                column_types[header] = "Text"

            if categorization:
                try:
                    ai_cat = _coerce_ai_categorization_dict(
                        categorization["ai_categorization"]
                    )
                    document_type = ai_cat.get("document_type")
                    document_description = ai_cat.get("document_description")
                except (KeyError, TypeError, json.JSONDecodeError) as e:
                    print(f"⚠️ Could not parse AI categorization: {e}")

            for chunk in build_describe_completion(
                spreadsheet_name,
                sheet_name,
                account_email=account_email,
                headers=headers,
                record_count=record_count,
                column_types=column_types or None,
                document_type=document_type,
                document_description=document_description,
            ):
                yield chunk

        except Exception as e:
            import traceback

            print(f"❌ Error in basic info handler: {str(e)}")
            print(f"Traceback: {traceback.format_exc()}")
            from services.google_sheets.response_summary import build_error_response

            for chunk in build_error_response(
                "Could not analyze spreadsheet",
                str(e),
                hints=["Re-sync on Connections", "Check Google account access"],
            ):
                yield chunk
    
    return generate()


async def handle_specific_record_search(prompt: str, sheet_details: dict, google_sheets_connection: dict, user_id: str):
    """
    Handle specific record search requests using AI-powered search
    Finds and returns matching records based on user's search criteria
    """
    async def generate():
        try:
            from services.google_sheets_service import GoogleSheetsService
            
            yield f"**Searching in Spreadsheet: {sheet_details['spreadsheet_name']}**\n\n"
            yield f"**Sheet:** {sheet_details['sheet_name']}\n"
            yield f"**Search Query:** {prompt}\n\n"
            yield f"**Reading spreadsheet data...**\n\n"
            
            # Initialize Google Sheets service
            sheets_service = GoogleSheetsService(
                access_token=google_sheets_connection['access_token'],
                refresh_token=google_sheets_connection['refresh_token'],
                expires_at=google_sheets_connection['expires_at']
            )
            
            # Read ALL data for search (up to 10,000 rows)
            result = await sheets_service.read_spreadsheet(
                sheet_details['spreadsheet_id'], 
                range_name='A1:ZZ10000',
                sheet_name=sheet_details['sheet_name']
            )
            
            if not result.get('success'):
                yield f"**Error reading spreadsheet:** {result.get('error', 'Unknown error')}\n\n"
                return
            
            headers = result.get('headers', [])
            all_records = result.get('data', [])
            
            if not all_records:
                yield f"**No data found** in this spreadsheet.\n\n"
                return
            
            yield f"**Total Records:** {len(all_records)}\n\n"
            
            # Use AI-powered search
            search_result = await sheets_service.search_records_with_ai(
                spreadsheet_id=sheet_details['spreadsheet_id'],
                search_query=prompt,
                headers=headers,
                all_records=all_records,
                max_results=10
            )
            
            if not search_result.get('success'):
                yield f"**Search failed:** {search_result.get('error', 'Unknown error')}\n\n"
                return
            
            matching_records = search_result.get('matching_records', [])
            search_criteria = search_result.get('search_criteria', {})
            
            if not matching_records:
                yield f"**No matching records found**\n\n"
                yield f"**Search Criteria:**\n"
                yield f"• Fields searched: {', '.join(search_criteria.get('search_fields', []))}\n"
                yield f"• Search terms: {', '.join(search_criteria.get('search_terms', []))}\n\n"
                yield f"**Try:**\n"
                yield f"• Using different search terms\n"
                yield f"• Checking spelling\n"
                yield f"• Broadening your search criteria\n\n"
                return
            
            # Show matching records
            yield f"**Found {len(matching_records)} matching record(s):**\n\n"
            
            # Display each matching record
            for i, record in enumerate(matching_records, 1):
                yield f"**Record {i}:**\n"
                for key, value in record.items():
                    if value and str(value).strip():
                        yield f"  • **{key}:** {value}\n"
                yield "\n"
            
            # # Show summary (commented out for now)
            # yield f"**Search Summary:**\n"
            # yield f"• **Total records in sheet:** {len(all_records)}\n"
            # yield f"• **Matching records:** {len(matching_records)}\n"
            # yield f"• **Search explanation:** {search_criteria.get('explanation', 'No explanation available')}\n\n"
            
            yield f"**Next Steps:**\n"
            yield f"• To extract all data: \"Extract data from {sheet_details['spreadsheet_name']}\"\n"
            yield f"• To refine search: Provide more specific criteria\n\n"
            
        except Exception as e:
            print(f"Error in specific search handler: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            yield f"**Error searching records:** {str(e)}\n\n"
            yield f"Please try again or contact support if the issue persists."
    
    return generate()


async def handle_google_sheets_info_stream(
    prompt: str,
    user_id: str,
    conversation_history: list = None,
    *,
    workbook_name: Optional[str] = None,
    sheet_name: Optional[str] = None,
):
    """
    Handle Google Sheets information requests with intelligent routing.
    User wants INFORMATION ABOUT a Google Spreadsheet OR to SEARCH for specific records.
    
    Flow:
    1. Check Google Sheets connection
    2. Detect spreadsheet details from prompt
    3. Classify request type (basic_info vs specific_search)
    4. Route to appropriate handler
    """
    try:
        from db.connection import database
        
        # 1. Check if user has Google Sheets connection and get valid token (refreshes if expired)
        from utils.google_sheets_auth import get_token_for_detection, user_has_google_sheets_connection

        if not await user_has_google_sheets_connection(user_id):
            async def generate():
                yield "**Google Sheets Connection Required**\n\n"
                yield "I'd love to help you analyze your Google Spreadsheet! However, I need you to connect your Google Sheets account first.\n\n"
                yield "**Here's how to connect:**\n"
                yield "1. Go to the **Connections** page\n"
                yield "2. Click on **Google Sheets**\n"
                yield "3. Authorize Archian AI to access your spreadsheets\n\n"
                yield "Once connected, come back and I'll help you analyze the data!"
            return generate()

        # 2. Resolve spreadsheet from catalog-bound name (planner) or URL in prompt
        from services.google_sheets.context_resolver import resolve_spreadsheet_context

        sheet_details = await resolve_spreadsheet_context(
            prompt,
            user_id,
            conversation_history,
            source_name=workbook_name,
            source_sheet_name=sheet_name,
        )
        sheet_details = sheet_details.to_detection_dict()

        if sheet_details and sheet_details.get("error") == "ambiguous_spreadsheet_name":
            async def generate():
                yield f"**Which workbook did you mean?**\n\n"
                yield f"{sheet_details.get('message', '')}\n\n"
            return generate()

        google_sheets_connection = await get_token_for_detection(user_id, sheet_details)
        
        # Check if it's a file not created by Archian AI
        if sheet_details and sheet_details.get("error") == "not_created_by_edison":
            async def generate():
                yield f"**⚠️ Cannot Access This Spreadsheet**\n\n"
                yield f"I can only access spreadsheets **created through Archian AI** due to Google's security policies.\n\n"
                yield f"**This spreadsheet was created outside of Archian AI**, so I don't have permission to access it.\n\n"
                yield f"**✅ How to Fix This:**\n\n"
                yield f"1. Ask me: *\"Create a spreadsheet called [Your Name]\"*\n"
                yield f"2. I'll create it and give you the URL\n"
                yield f"3. Copy your data from the original spreadsheet into the new one\n"
                yield f"4. ✅ Now you can use it!\n\n"
                yield f"*Note: Even if you own the original spreadsheet, I can't access it because you didn't create it through Archian AI.*\n\n"
            return generate()
        
        # Check if it's a shared file limitation error
        if sheet_details and sheet_details.get("error") == "shared_file_limitation":
            async def generate():
                yield f"**Spreadsheet Access Limitation**\n\n"
                yield f"**⚠️ Cannot Access This Spreadsheet**\n\n"
                yield f"I can only access spreadsheets **created through Archian AI** due to Google's security policies.\n\n"
                yield f"**This spreadsheet was created outside of Archian AI**, so I don't have permission to access it.\n\n"
                yield f"**✅ How to Fix This:**\n\n"
                yield f"1. Ask me: *\"Create a spreadsheet called [Your Name]\"*\n"
                yield f"2. I'll create it and give you the URL\n"
                yield f"3. Copy your data from the original spreadsheet into the new one\n"
                yield f"4. ✅ Now you can use it!\n\n"
                yield f"*Note: Even if you own the original spreadsheet, I can't access it because you didn't create it through Archian AI.*\n\n"
            return generate()
        
        if not sheet_details or not sheet_details.get('found', False):
            async def generate():
                yield f"**Google Sheets Analysis**\n\n"
                yield f"I'd love to help you analyze your Google Spreadsheet!\n\n"
                yield f"**Your Connected Account:** {google_sheets_connection['google_account_email']}\n\n"
                
                # Fetch user's accessible spreadsheets
                user_spreadsheets = await database.fetch_all(
                    """
                    SELECT DISTINCT spreadsheet_id, spreadsheet_name, sheet_name
                    FROM user_google_spreadsheets 
                    WHERE user_id = :user_id
                    ORDER BY spreadsheet_name, sheet_name
                    """,
                    {"user_id": str(user_id)}
                )
                
                if user_spreadsheets:
                    yield "**Your Accessible Spreadsheets:**\n"
                    for sheet in user_spreadsheets:
                        yield f"• **{sheet['spreadsheet_name']}** (Sheet: {sheet['sheet_name']})\n"
                    yield "\n**Please specify which spreadsheet you'd like me to analyze:**\n"
                    yield "• Mention the spreadsheet name\n"
                    yield "• Or provide the Google Sheets URL\n\n"
                    yield "**Example:** \"Give me information about the Archian Test spreadsheet\""
                else:
                    yield "**No spreadsheets found.** Please connect a Google Spreadsheet first by providing its URL in the chat."
            return generate()
        
        # 3. Classify request type (basic_info vs specific_search)
        classification = await classify_google_sheets_info_request(prompt, conversation_history)
        request_type = classification.get('request_type', 'basic_info')

        # Architect chat is free; no credit deduction

        # 4. Route to appropriate handler
        if request_type == 'specific_search':
            return await handle_specific_record_search(prompt, sheet_details, google_sheets_connection, user_id)
        else:
            return await handle_basic_spreadsheet_info(
                sheet_details, google_sheets_connection, user_id, prompt=prompt
            )
        
    except Exception as e:
        print(f"❌ Error in Google Sheets info handler: {e}")
        import traceback
        traceback.print_exc()
        
        error_message = str(e)
        async def generate():
            yield f"**Error:** {error_message}\n\n"
            yield f"Please try again or contact support if the issue persists."
        return generate()


