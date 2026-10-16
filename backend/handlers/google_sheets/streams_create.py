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
from services.entitlements import get_entitlements

from handlers.google_sheets.background import (
    seed_gsheets_extracted_schema_for_mapping,
    store_google_spreadsheet_metadata,
    update_google_spreadsheet_metadata,
)

# In-memory storage for pending update confirmations
# In production, this should be in Redis or database with TTL
pending_google_sheets_updates = {}


async def handle_google_sheets_update_metadata_stream(prompt: str, user_id: str, conversation_history: list = None):
    """
    Handle updating Google Spreadsheet metadata (e.g., renaming spreadsheet) from user's natural language prompt.
    
    User Examples:
    - "update the name of spreadsheet VC Firms Data to VC Firms Data Tier 2"
    - "rename spreadsheet Customer List to Customer Database"
    - "change spreadsheet name from X to Y"
    """
    async def generate():
        try:
            from db.connection import database
            import json
            import re
            
            # Check Google Sheets connection
            google_sheets_connection = await database.fetch_one(
                """
                SELECT id, access_token, refresh_token, expires_at, google_account_email
                FROM google_sheets_connections 
                WHERE user_id = :user_id AND is_active = true
                ORDER BY created_at DESC
                LIMIT 1
                """,
                {"user_id": user_id}
            )
            
            if not google_sheets_connection:
                yield "**Google Sheets Connection Required**\n\n"
                yield "Please connect your Google account first to update spreadsheet metadata.\n\n"
                return
            
            yield f"**Updating Spreadsheet Metadata**\n\n"
            yield f"**Analyzing your request...**\n\n"
            
            # Use AI to extract spreadsheet name and new name from prompt
            
            extract_prompt = f"""
Extract the current spreadsheet name and new spreadsheet name from the user's request to update/rename a spreadsheet.

User message: "{prompt}"

Return JSON with:
- "current_name": The current/existing spreadsheet name
- "new_name": The new name to rename the spreadsheet to

Examples:
User: "update the name of spreadsheet VC Firms Data to VC Firms Data Tier 2"
Response: {{"current_name": "VC Firms Data", "new_name": "VC Firms Data Tier 2"}}

User: "rename spreadsheet Customer List to Customer Database"
Response: {{"current_name": "Customer List", "new_name": "Customer Database"}}

User: "change spreadsheet name from Sales Report to Q4 Sales Report"
Response: {{"current_name": "Sales Report", "new_name": "Q4 Sales Report"}}

Return ONLY valid JSON, no other text.
"""
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.GOOGLE_SHEETS_DETECTION,
                messages=[{"role": "user", "content": extract_prompt}],
                max_tokens=500,
                temperature=0.1,
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
            from utils.openai_helpers import parse_openai_json_response
            try:
                extraction_result = parse_openai_json_response(ai_response)
            except (ValueError, json.JSONDecodeError):
                    extraction_result = {"current_name": None, "new_name": None}
            
            current_name = extraction_result.get("current_name")
            new_name = extraction_result.get("new_name")
            
            if not current_name or not new_name:
                yield "**Error: Could not extract spreadsheet names**\n\n"
                yield "Please specify both the current spreadsheet name and the new name.\n\n"
                yield "**Examples:**\n"
                yield "• `update the name of spreadsheet VC Firms Data to VC Firms Data Tier 2`\n"
                yield "• `rename spreadsheet Customer List to Customer Database`\n\n"
                return
            
            yield f"**Current Name:** {current_name}\n"
            yield f"**New Name:** {new_name}\n\n"
            yield f"**Finding spreadsheet...**\n\n"
            
            # Find spreadsheet_id from database
            spreadsheet_record = await database.fetch_one(
                """
                SELECT DISTINCT spreadsheet_id, google_sheets_connection_id
                FROM gsheets_extracted_schemas 
                WHERE user_id = :user_id 
                  AND spreadsheet_name = :spreadsheet_name
                LIMIT 1
                """,
                {
                    "user_id": user_id,
                    "spreadsheet_name": current_name
                }
            )
            
            if not spreadsheet_record:
                yield f"**Spreadsheet Not Found**\n\n"
                yield f"Could not find a spreadsheet named '{current_name}' in your account.\n\n"
                yield "**Note:** This feature only works for spreadsheets created through Archian AI.\n\n"
                return
            
            spreadsheet_id = spreadsheet_record['spreadsheet_id']
            google_sheets_connection_id = spreadsheet_record['google_sheets_connection_id']
            
            yield f"**Found spreadsheet!**\n\n"
            yield f"**Updating metadata...**\n\n"
            
            # Update spreadsheet metadata
            update_result = await update_google_spreadsheet_metadata(
                user_id=user_id,
                spreadsheet_id=spreadsheet_id,
                new_title=new_name,
                google_sheets_connection_id=google_sheets_connection_id
            )
            
            if update_result.get('success'):
                # Spreadsheet rename is free; no credit deduction
                yield f"**✅ Spreadsheet renamed successfully!**\n\n"
                yield f"**Old Name:** {current_name}\n"
                yield f"**New Name:** {new_name}\n"
                yield f"**Spreadsheet URL:** [Open in Google Sheets]({update_result.get('spreadsheet_url', '#')})\n\n"
            else:
                error_msg = update_result.get('error', 'Unknown error')
                yield f"**❌ Error updating spreadsheet**\n\n"
                yield f"{error_msg}\n\n"
                
                if update_result.get('permission_denied'):
                    yield "**Note:** This operation only works for spreadsheets created through Archian AI.\n\n"
        
        except Exception as e:
            print(f"❌ Error in handle_google_sheets_update_metadata_stream: {e}")
            import traceback
            traceback.print_exc()
            yield f"**Error updating spreadsheet metadata:** {str(e)}\n\n"
            yield f"Please try again or contact support if the issue persists.\n\n"
    
    return generate()


async def _headers_from_extraction_job(
    extraction_job_id: str,
    user_id: str,
) -> tuple[Optional[List[str]], Optional[Dict[str, Any]]]:
    """Column names from a completed extraction job (for planner create-sheet step)."""
    from services.extraction_results_service import ExtractionResultsService

    row = await ExtractionResultsService.get_extraction_result(extraction_job_id, str(user_id))
    if not row:
        return None, None
    data = row.get("extracted_data") or []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = [k for k in data[0].keys() if k not in ("Attachments", "_attachments", "attachments")]
        return keys, row
    return None, row


async def handle_google_sheets_create_from_prompt_stream(
    prompt: str,
    user_id: str,
    conversation_history: list = None,
    conversation_id: Optional[str] = None,
    intent_state: Optional[Dict] = None,
    *,
    extraction_job_id: Optional[str] = None,
    spreadsheet_title: Optional[str] = None,
    headers: Optional[List[str]] = None,
    use_extraction_headers: bool = False,
    create_contract: Optional[Dict[str, Any]] = None,
    google_sheets_connection_id: Optional[str] = None,
    stream_prose: bool = True,
):
    """
    Handle creating a new Google Spreadsheet from user's natural language prompt.
    
    User Examples:
    - "Create a Google Sheet called 'Customer List' with Name=John Smith, Email=john@example.com"
    - "Make a new spreadsheet 'Sales Leads' with columns: Company, Contact, Email, Status"
    
    Flow:
    1. Check Google Sheets connection
    2. Extract spreadsheet name and data from prompt
    3. Validate plan limits
    4. Create spreadsheet
    5. Store metadata
    6. Return success message
    """
    try:
        print(f"🔍 DEBUG [handle_google_sheets_create_from_prompt_stream] Starting, user_id: {user_id}")
        from db.connection import database
        from services.google_sheets_service import GoogleSheetsService
        
        # Check if user has Google Sheets connection and get valid token (refreshes if expired)
        from utils.google_sheets_auth import get_valid_google_sheets_token, user_has_google_sheets_connection

        if not await user_has_google_sheets_connection(user_id):
            async def generate():
                yield "**No Google Sheets connection found**\n\n"
                yield "Please connect your Google Sheets account first from the Connections page.\n\n"
            return generate()

        contract = dict(create_contract or {})
        conn_id = (
            (google_sheets_connection_id or "").strip()
            or (contract.get("google_sheets_connection_id") or "").strip()
            or (intent_state or {}).get("google_sheets_connection_id")
        )
        google_sheets_connection = await get_valid_google_sheets_token(
            user_id, conn_id or None
        )
        if not google_sheets_connection:
            async def generate():
                yield "**No Google Sheets connection found**\n\n"
                yield "Please connect your Google Sheets account first from the Connections page.\n\n"
            return generate()

        use_extraction_headers = bool(
            use_extraction_headers or contract.get("use_extraction_headers")
        )
        extraction_job_id = (
            extraction_job_id
            or contract.get("extraction_job_id")
        )
        if not spreadsheet_title and contract.get("spreadsheet_title"):
            spreadsheet_title = contract.get("spreadsheet_title")

        planner_headers: Optional[List[str]] = None
        if headers:
            planner_headers = [str(h).strip() for h in headers if str(h or "").strip()]
        if not planner_headers and contract.get("headers"):
            planner_headers = [
                str(h).strip() for h in contract.get("headers") or [] if str(h or "").strip()
            ]
        if use_extraction_headers and extraction_job_id:
            loaded_headers, _ex_row = await _headers_from_extraction_job(
                extraction_job_id, str(user_id)
            )
            if loaded_headers:
                planner_headers = loaded_headers

        planner_driven = bool(
            contract
            or spreadsheet_title
            or use_extraction_headers
            or planner_headers
        )

        extraction_result: Dict[str, Any]
        if planner_driven:
            resolved_title = (spreadsheet_title or "").strip()
            if not resolved_title:
                async def generate():
                    yield (
                        "**Cannot create spreadsheet:** the approved plan is missing "
                        "a spreadsheet title.\n\n"
                    )
                return generate()
            if use_extraction_headers and not planner_headers:
                async def generate():
                    yield (
                        "**Cannot create spreadsheet:** extraction columns are not ready yet. "
                        "Complete the extract step first.\n\n"
                    )
                return generate()
            if not use_extraction_headers and not planner_headers:
                async def generate():
                    yield (
                        "**Cannot create spreadsheet:** column headers are required "
                        "for this create step.\n\n"
                    )
                return generate()

            spreadsheet_name = resolved_title
            only_headers = planner_headers or []
            initial_data: Any = {}
            requested_sheet_name = "Sheet1"
            extraction_result = {
                "spreadsheet_name": spreadsheet_name,
                "headers": only_headers,
                "data": {},
                "sheet_name": requested_sheet_name,
            }
        elif spreadsheet_title and planner_headers:
            spreadsheet_name = spreadsheet_title.strip()
            only_headers = planner_headers
            initial_data: Any = {}
            requested_sheet_name = "Sheet1"
            extraction_result = {
                "spreadsheet_name": spreadsheet_name,
                "headers": planner_headers,
                "data": {},
                "sheet_name": requested_sheet_name,
            }
        else:
            # Extract spreadsheet name and initial data from prompt using AI


            conversation_context = ""
            if conversation_history:
                recent_messages = conversation_history[-5:] if len(conversation_history) > 5 else conversation_history
                conversation_context = "\n\n**Recent Conversation History:**\n"
                for msg in recent_messages:
                    msg_type = msg.get("type", msg.get("role", "user"))
                    msg_text = msg.get("text", "") or msg.get("content", "")
                    if msg_text:
                        role_label = "User" if msg_type in ["user", "User"] else "Assistant"
                        conversation_context += f"{role_label}: {msg_text}\n"

            create_prompt = f"""
Extract spreadsheet name, optional sheet/tab name, and initial data from user's request to CREATE a new spreadsheet.

**Current User Message:**
"{prompt}"
{conversation_context}

**CONTINUATION SCENARIOS (CRITICAL - READ FIRST):**

If the conversation history shows:
- The assistant previously asked for headers/columns for a spreadsheet creation
- The user's current message is a SHORT response with comma-separated field names (e.g., "First Name, Last Name, Email, Status")
- The current message does NOT explicitly mention a spreadsheet name

THEN:
1. Extract the spreadsheet name from the conversation history (from the previous user message mentioning the spreadsheet name, or from the assistant's prompt that included the spreadsheet name)
2. Treat the current message as providing HEADERS ONLY (comma-separated field names without values)
3. Return: {{"spreadsheet_name": "<from conversation history>", "data": {{}}, "headers": ["First Name", "Last Name", "Email", "Status"], "sheet_name": null}}

**Example of Continuation:**
Conversation History:
  User: "create new spreadsheet for me name regus timelogs"
  Assistant: "Great! Let's set up 'regus timelogs'. I need to know what information you'll track. Please provide column headers."
  Current User: "First Name, Last Name, Email, Status"
  
Expected Response: {{"spreadsheet_name": "regus timelogs", "data": {{}}, "headers": ["First Name", "Last Name", "Email", "Status"], "sheet_name": null}}

**CRITICAL DISTINCTION:**

1. **If user is defining STRUCTURE (headers only):**
   - Example: "Create with headers name, email, phone"
   - Example: "Create with columns applicant name, applicant email, status"
   - Example (continuation): "First Name, Last Name, Email, Status" (when conversation shows spreadsheet creation in progress)
   - Return: {{"spreadsheet_name": "...", "data": {{}}, "headers": ["Name", "Email", "Phone"]}}

2. **If user is providing ACTUAL DATA VALUES:**
   - Single record: "Create with Name=John, Email=john@test.com"
   - Multiple records: "Create with Name=John, Email=john@test.com and Name=Jane, Email=jane@test.com"
   - Markdown table: User provides data in markdown table format with pipe (|) delimiters
   - Return single: {{"spreadsheet_name": "...", "data": {{"Name": "John", "Email": "john@test.com"}}, "headers": null}}
   - Return multiple: {{"spreadsheet_name": "...", "data": [{{"Name": "John", "Email": "john@test.com"}}, {{"Name": "Jane", "Email": "jane@test.com"}}], "headers": null}}

**How to tell the difference:**
- Structure/headers: User says "with header(s)", "with column(s)", OR lists comma-separated field names WITHOUT values (no "=" signs, no actual data values like emails/names/numbers)
- Data: User provides actual values like names, emails, numbers, or uses "=" syntax
- Multiple records: User uses "and", "also", lists multiple entries, or provides multiple sets of values
- Markdown table: User provides data in table format with | delimiters - parse each row as a separate record
- **CRITICAL**: A comma-separated list of field names (e.g., "First Name, Last Name, Email") is ALWAYS headers, never data, especially in continuation scenarios

**Markdown Table Parsing:**
- If user provides data in markdown table format (with | delimiters):
  * First row after separator (---) contains headers
  * Each subsequent row is a data record
  * Parse each row into a dictionary using column headers as keys
  * Return all rows as an array in the "data" field
  * Extract spreadsheet name from the user's request (e.g., "VC Firms Data", "Customer List")
  * Example: Table with headers "Name | Email" and rows "John | john@test.com" and "Jane | jane@test.com"
    → Return: {{"spreadsheet_name": "Extracted Name", "data": [{{"Name": "John", "Email": "john@test.com"}}, {{"Name": "Jane", "Email": "jane@test.com"}}], "headers": null}}

**Column Name Guidelines:**
- For person data → Use: "Name", "First Name", "Last Name", "Email", "Phone", "City", "Country", "Zip"
- For company data → Use: "Company Name", "Industry", "Website", "Contact Email"
- For product data → Use: "Product Name", "Price", "SKU", "Category"
- NEVER use "Field1", "Field2", "Field3"!

Return JSON:
{{
    "spreadsheet_name": "name here",
    "data": {{"Column": "value"}} OR [{{"Column": "value"}}, {{"Column": "value2"}}] OR {{}},
    "headers": ["Column1", "Column2"] or null,
    "sheet_name": "TabName" or null
}}

**Examples:**

User: "Create a spreadsheet called Customer List"
Response: {{"spreadsheet_name": "Customer List", "data": {{}}, "headers": null, "sheet_name": null}}

User: "Create YC hackathon 2025 with header applicant name, applicant email, applicant idea and status"
Response: {{"spreadsheet_name": "YC hackathon 2025", "data": {{}}, "headers": ["Applicant Name", "Applicant Email", "Applicant Idea", "Status"], "sheet_name": "Applicants"}}

User: "Create test 1 with columns name, email, country"
Response: {{"spreadsheet_name": "test 1", "data": {{}}, "headers": ["Name", "Email", "Country"], "sheet_name": null}}

**Continuation Example (MOST IMPORTANT):**
Conversation shows: User previously said "create spreadsheet regus timelogs", Assistant asked for headers
Current User: "First Name, Last Name, Email, Status"
Response: {{"spreadsheet_name": "regus timelogs", "data": {{}}, "headers": ["First Name", "Last Name", "Email", "Status"], "sheet_name": null}}

**Another Continuation Example:**
Conversation shows: User previously said "create new spreadsheet called Sales Data", Assistant asked for headers
Current User: "Product, Price, Quantity, Date"
Response: {{"spreadsheet_name": "Sales Data", "data": {{}}, "headers": ["Product", "Price", "Quantity", "Date"], "sheet_name": null}}

User: "Create Archian Test with Name=John, Email=john@test.com"
Response: {{"spreadsheet_name": "Archian Test", "data": {{"Name": "John", "Email": "john@test.com"}}, "headers": null, "sheet_name": "Contacts"}}

User: "Create Contact Database with first name John, last name Smith, email john@smith.com, phone 555-1234"
Response: {{"spreadsheet_name": "Contact Database", "data": {{"First Name": "John", "Last Name": "Smith", "Email": "john@smith.com", "Phone": "555-1234"}}, "headers": null, "sheet_name": "People"}}

User: "Create Sales Report with product iPhone, price 999, quantity 5"
Response: {{"spreadsheet_name": "Sales Report", "data": {{"Product": "iPhone", "Price": "999", "Quantity": "5"}}, "headers": null, "sheet_name": "Items"}}

User: "Create Contact List with Name=John, Email=john@test.com and Name=Jane, Email=jane@test.com and Name=Bob, Email=bob@test.com"
Response: {{"spreadsheet_name": "Contact List", "data": [{{"Name": "John", "Email": "john@test.com"}}, {{"Name": "Jane", "Email": "jane@test.com"}}, {{"Name": "Bob", "Email": "bob@test.com"}}], "headers": null, "sheet_name": null}}

User: "create a spreadsheet with following data | VC Firm | Partner Name | Email | | Unshackled Ventures | Manan Mehta | manan@unshackled.vc | | Unshackled Ventures | Nitin Pachisia | nitin@unshackled.vc |"
Response: {{"spreadsheet_name": "VC Firms Data", "data": [{{"VC Firm": "Unshackled Ventures", "Partner Name": "Manan Mehta", "Email": "manan@unshackled.vc"}}, {{"VC Firm": "Unshackled Ventures", "Partner Name": "Nitin Pachisia", "Email": "nitin@unshackled.vc"}}], "headers": null, "sheet_name": null}}
 
**Sheet Name Detection Rules:**
- If the user mentions a tab/sheet/worksheet name (e.g., "in tab Leads", "sheet Orders", "worksheet Applicants"), set "sheet_name" to that exact name.
- If not specified, set "sheet_name" to null.

**CRITICAL JSON FORMATTING REQUIREMENTS:**
- Return ONLY valid JSON, no markdown, no code blocks, no explanations
- ALL string values MUST be properly escaped:
  * Use \\" for double quotes inside strings: "Field": "Value with \\"quotes\\""
  * DO NOT escape single quotes - they are valid in JSON strings: "Field": "We're done" (NOT "We\\'re done")
  * Escape backslashes: use \\\\ for a single backslash
- For data with quotes, special characters, or URLs, ensure they are properly escaped in JSON
- If user provides markdown tables, parse them into the data array format
- Example with quotes: {{"data": [{{"Field": "Value with \\"quotes\\""}}]}}
- Example with apostrophe: {{"data": [{{"Field": "We're done"}}]}} (single quote NOT escaped)

Return ONLY valid JSON, no other text.
"""
            from services.architect_agent import async_openai_call
            from services.llm import tasks as llm_tasks

            response = await async_openai_call(
                None,
                task=llm_tasks.FILE_EXTRACTION,
                messages=[{"role": "user", "content": create_prompt}],
                max_tokens=4000,
                temperature=0.1,
            )

            ai_response = response.choices[0].message.content.strip()

            from utils.openai_helpers import parse_openai_json_response

            try:
                extraction_result = parse_openai_json_response(ai_response)
            except (ValueError, json.JSONDecodeError) as e:
                print(f"⚠️ JSON parsing failed. Error: {e}")
                extraction_result = {"spreadsheet_name": None, "data": {}}

            if not extraction_result:
                extraction_result = {"spreadsheet_name": None, "data": {}}

            spreadsheet_name = extraction_result.get("spreadsheet_name")
            if spreadsheet_title:
                spreadsheet_name = spreadsheet_title.strip()
            initial_data = extraction_result.get("data", {})
            only_headers = extraction_result.get("headers") or planner_headers
            requested_sheet_name = extraction_result.get("sheet_name") or "Sheet1"

        print(f"🔍 DEBUG [handle_google_sheets_create_from_prompt_stream] Spreadsheet name: {spreadsheet_name}")
        print(f"🔍 DEBUG [handle_google_sheets_create_from_prompt_stream] Only headers: {only_headers}")
        
        # Validate that we have enough information to create a spreadsheet
        if not spreadsheet_name or spreadsheet_name.lower() in ["new spreadsheet", "spreadsheet", "sheet"]:
            async def generate():
                yield "**📝 Let's create your spreadsheet!**\n\n"
                yield "To create a spreadsheet, I need at least:\n"
                yield "1. A descriptive name\n"
                yield "2. Column headers (what information you'll track)\n\n"
                yield "**Here are some examples:**\n\n"
                yield "**Create with data:**\n"
                yield "• `Create a Customer List with name John Doe, email john@example.com, phone 555-1234`\n\n"
                yield "**Create with just headers:**\n"
                yield "• `Create a Sales Report with columns: Product, Price, Quantity, Date`\n\n"
                yield "💡 *Tip: Headers help me understand your data structure and provide better assistance!*\n\n"
            return generate()
        
        # Validate that we have at least headers or data
        if not initial_data and not only_headers:
            # Save intent_state so we can continue this conversation on the next message
            if conversation_id and spreadsheet_name:
                try:
                    from services.chat_service import ChatService
                    from handlers.classification_handler import create_intent_state
                    
                    intent_state_to_save = create_intent_state(
                        active_intent="create",
                        active_source="unknown",
                        active_destination="google_sheets",
                        destination_name=spreadsheet_name,
                        state="awaiting_headers"
                    )
                    
                    await ChatService.log_data_event(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        event_type="spreadsheet_creation_started",
                        event_data={
                            "spreadsheet_name": spreadsheet_name,
                            "intent_state": intent_state_to_save
                        }
                    )
                except Exception as e:
                    print(f"⚠️ Failed to save intent_state for spreadsheet creation: {e}")
            
            async def generate():
                yield f"**Great! Let's set up '{spreadsheet_name}'**\n\n"
                yield "I need to know what information you'll track. Please provide column headers.\n\n"
                yield "**Examples:**\n\n"
                yield f"• `Create {spreadsheet_name} with columns: Product, Price, Quantity`\n\n"
                yield f"• `Create {spreadsheet_name} with headers: First Name, Last Name, Email, Status`\n\n"
                yield "💡 *Tip: Think about what fields you'll need — this helps when building your data pipeline later!*\n\n"
            return generate()
        
        # Validate plan limits (count records correctly - 1 if dict, len() if list, 0 if empty)
        if initial_data:
            if isinstance(initial_data, dict):
                record_count = 1
            elif isinstance(initial_data, list):
                record_count = len(initial_data)
            else:
                record_count = 0
        else:
            record_count = 0
        if record_count > 0:
            is_valid, message, plan_info = await get_entitlements().validate_migration(user_id, record_count)
            
            if not is_valid:
                async def generate():
                    yield f"**Plan Limit Reached**\n\n"
                    yield f"{message}\n\n"
                    yield "Upgrade your plan to continue.\n\n"
                return generate()
        
        # Initialize Google Sheets Service
        sheets_service = GoogleSheetsService(
            access_token=google_sheets_connection['access_token'],
            refresh_token=google_sheets_connection['refresh_token'],
            expires_at=google_sheets_connection['expires_at']
        )
        
        async def generate():
            try:
                # Prepare data for spreadsheet creation
                if only_headers and not initial_data:
                    # User specified headers only - create spreadsheet with just header row
                    data_rows = [only_headers]  # Just the header row
                    if stream_prose:
                        yield f"**Creating spreadsheet '{spreadsheet_name}'** with columns: {', '.join(only_headers)}\n\n"
                elif initial_data:
                    # User provided data - handle both single dict and list of dicts
                    if isinstance(initial_data, dict):
                        # Single record
                        headers = list(initial_data.keys())
                        data_rows = [headers, list(initial_data.values())]
                    elif isinstance(initial_data, list) and len(initial_data) > 0:
                        # Multiple records - extract headers from first record
                        headers = list(initial_data[0].keys())
                        data_rows = [headers]  # Header row
                        # Add data rows for all records
                        for record in initial_data:
                            data_rows.append([record.get(header, '') for header in headers])
                    else:
                        # Empty list or invalid format
                        data_rows = []
                    
                    if data_rows:
                        headers = data_rows[0] if data_rows else []
                        if stream_prose:
                            yield f"**Creating spreadsheet '{spreadsheet_name}'** with data and columns: {', '.join(headers)}\n\n"
                else:
                    # Create empty spreadsheet with just a title
                    data_rows = []
                    if stream_prose:
                        yield f"**Creating empty spreadsheet '{spreadsheet_name}'**\n\n"
                
                # Create the spreadsheet
                create_result = await sheets_service.create_spreadsheet(
                    title=spreadsheet_name,
                    data=data_rows if data_rows else None,
                    sheet_name=requested_sheet_name
                )
                
                if not create_result.get('success'):
                    yield f"**Error creating spreadsheet**: {create_result.get('error', 'Unknown error')}\n\n"
                    return
                
                spreadsheet_id = create_result.get('spreadsheet_id')
                spreadsheet_url = create_result.get('spreadsheet_url')
                # Clear intent_state since spreadsheet creation is complete
                if conversation_id and spreadsheet_id:
                    try:
                        from services.chat_service import ChatService
                        from handlers.classification_handler import create_intent_state
                        
                        # Mark as completed by creating a completed intent_state
                        completed_intent_state = create_intent_state(
                            active_intent="create",
                            active_source="unknown",
                            active_destination="google_sheets",
                            destination_name=spreadsheet_name,
                            state="completed"
                        )
                        
                        await ChatService.log_data_event(
                            conversation_id=conversation_id,
                            user_id=user_id,
                            event_type="spreadsheet_creation_completed",
                            event_data={
                                "spreadsheet_name": spreadsheet_name,
                                "spreadsheet_id": spreadsheet_id,
                                "spreadsheet_url": spreadsheet_url,
                                "destination": "google_sheets",
                                "destination_name": spreadsheet_name,
                                "intent_state": completed_intent_state,
                            },
                        )
                    except Exception as e:
                        print(f"⚠️ Failed to mark intent_state as completed: {e}")

                # Fetch metadata to capture the actual sheetId for the created tab
                sheet_id_value = None
                try:
                    meta = await sheets_service.get_spreadsheet_metadata(spreadsheet_id)
                    for s in meta.get('sheets', []) or []:
                        props = (s or {}).get('properties', {})
                        if props.get('title') == requested_sheet_name:
                            sheet_id_value = str(props.get('sheetId'))
                            break
                except Exception:
                    sheet_id_value = None

                # Store metadata using dynamic sheet name and id
                print(f"🔍 DEBUG [handle_google_sheets_create_from_prompt_stream] Before store_google_spreadsheet_metadata (2nd location), connection keys: {list(google_sheets_connection.keys())}")
                connection_id_for_metadata = google_sheets_connection.get('id') or google_sheets_connection.get('connection_id')
                print(f"🔍 DEBUG [handle_google_sheets_create_from_prompt_stream] Connection ID for metadata: {connection_id_for_metadata}")
                await store_google_spreadsheet_metadata(
                    user_id=user_id,
                    google_sheets_connection_id=connection_id_for_metadata,
                    spreadsheet_id=spreadsheet_id,
                    spreadsheet_name=spreadsheet_name,
                    sheet_name=requested_sheet_name,
                    sheet_id=sheet_id_value
                )

                header_names_for_map = list(only_headers or [])
                if not header_names_for_map and isinstance(initial_data, dict):
                    header_names_for_map = list(initial_data.keys())
                elif not header_names_for_map and isinstance(initial_data, list) and initial_data:
                    header_names_for_map = list(initial_data[0].keys())
                if header_names_for_map and connection_id_for_metadata:
                    await seed_gsheets_extracted_schema_for_mapping(
                        user_id=user_id,
                        google_sheets_connection_id=connection_id_for_metadata,
                        spreadsheet_id=spreadsheet_id,
                        spreadsheet_name=spreadsheet_name,
                        sheet_name=requested_sheet_name,
                        header_names=header_names_for_map,
                    )
                
                # Track usage and create migration batch if we added data
                if record_count > 0:
                    # Create migration batch for bulk operations (2+ records)
                    if record_count > 1:
                        try:
                            from services.migration_records_service import MigrationRecordsService
                            
                            # Prepare records data for tracking (if initial_data is a dict, convert to list)
                            records_data = []
                            if isinstance(initial_data, dict):
                                records_data = [initial_data]
                            elif isinstance(initial_data, list):
                                records_data = initial_data
                            
                            # Extract headers for mapping config
                            if only_headers:
                                extracted_headers = only_headers
                            elif isinstance(initial_data, dict):
                                extracted_headers = list(initial_data.keys())
                            elif isinstance(initial_data, list) and len(initial_data) > 0:
                                extracted_headers = list(initial_data[0].keys())
                            else:
                                extracted_headers = []
                            
                            # Prepare mapping config for batch
                            mapping_config = {
                                "document_type": "google_sheets_create_with_data",
                                "destination_type": "google_sheets",
                                "spreadsheet_id": spreadsheet_id,
                                "spreadsheet_name": spreadsheet_name,
                                "sheet_name": requested_sheet_name,
                                "field_count": len(extracted_headers),
                                "field_names": extracted_headers
                            }
                            
                            # Create migration batch (returns the batch_id)
                            batch_id = await MigrationRecordsService.create_migration_batch(
                                user_id=user_id,
                                batch_name=f"Create_{spreadsheet_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                                document_type="google_sheets_create_with_data",
                                total_records=record_count,
                                mapping_config=mapping_config,
                                destination_type="google_sheets"
                            )
                            
                            # Track individual records with mapped_data and destination_results
                            for idx, record in enumerate(records_data):
                                # Prepare destination data (Google Sheets equivalent of salesforce_results)
                                destination_data = {
                                    spreadsheet_name: {
                                        "id": spreadsheet_id,
                                        "sheet_name": requested_sheet_name,
                                        "status": "created"  # New spreadsheet = created
                                    }
                                }
                                
                                # Create migration record
                                record_id = await MigrationRecordsService.create_migration_record(
                                    batch_id=batch_id,
                                    user_id=user_id,
                                    record_index=idx,
                                    source_data=record,
                                    mapped_data=record  # The data that was written to the sheet
                                )
                                
                                # Update with destination results
                                await MigrationRecordsService.update_migration_record_result(
                                    record_id=record_id,
                                    salesforce_results=destination_data,  # Using same column for Google Sheets
                                    status="success",
                                    error_message=None
                                )
                            
                            # Update batch status to completed
                            await MigrationRecordsService.update_batch_status(
                                batch_id=batch_id,
                                status="completed",
                                successful_count=record_count,
                                failed_count=0
                            )
                        except Exception as batch_error:
                            print(f"⚠️ Warning: Failed to create migration batch: {batch_error}")
                            import traceback
                            print(f"Traceback: {traceback.format_exc()}")
                            # Continue anyway - don't fail the operation if batch tracking fails
                    
                    # Track successful migrations (plan usage)
                    await get_entitlements().track_successful_migration(user_id, record_count)
                
                # Deduct credits for every spreadsheet creation: 1 for creation + 1 per record
                records_to_charge = max(1, record_count)  # Min 1 for empty/headers-only creation
                try:
                    from utils.credit_config import compute_migration_credits
                    user_plan = await get_entitlements().get_user_plan(user_id)
                    credits_per_month = user_plan.get("credits_per_month") if user_plan else None
                    if credits_per_month is not None and credits_per_month != -1:
                        migration_credits = compute_migration_credits(records_to_charge)
                        if migration_credits > 0:
                            await get_entitlements().deduct(
                                user_id=user_id,
                                amount=migration_credits,
                                reference_type="migration",
                                reference_id=f"gsheets_create_{spreadsheet_id}" if spreadsheet_id else "gsheets_create",
                            )
                            print(f"✅ [GSHEETS_CREATE] Deducted {migration_credits} credits (spreadsheet creation, {records_to_charge} records)")
                except Exception as e:
                    print(f"⚠️ [GSHEETS_CREATE] Failed to deduct credits: {e}")

                # Start background categorization for the newly created spreadsheet using existing service
                # Categorize if we have headers (either from only_headers or from initial_data)
                if initial_data or only_headers:
                    import asyncio
                    from services.gsheets_background_processing_service import GoogleSheetsBackgroundProcessingService
                    
                    # Get user email for notification
                    from db.connection import database
                    user_record = await database.fetch_one(
                        "SELECT email FROM users WHERE id = :user_id",
                        {"user_id": user_id}
                    )
                    user_email = user_record['email'] if user_record else ""
                    
                    # Use existing background processing service
                    print(f"🔍 DEBUG [handle_google_sheets_create_from_prompt_stream] Before process_spreadsheet_analysis, connection keys: {list(google_sheets_connection.keys())}")
                    connection_id_for_service = google_sheets_connection.get('id') or google_sheets_connection.get('connection_id')
                    print(f"🔍 DEBUG [handle_google_sheets_create_from_prompt_stream] Connection ID for service: {connection_id_for_service}")
                    service = GoogleSheetsBackgroundProcessingService()
                    asyncio.create_task(service.process_spreadsheet_analysis(
                        google_sheets_connection_id=connection_id_for_service,
                        spreadsheet_id=spreadsheet_id,
                        spreadsheet_name=spreadsheet_name,
                        user_id=user_id,
                        user_email=user_email
                    ))
                
                yield (
                    f"data: {json.dumps({'status': 'success', 'event_type': 'spreadsheet_created', 'spreadsheet_name': spreadsheet_name, 'spreadsheet_id': spreadsheet_id, 'spreadsheet_url': spreadsheet_url, 'sheet_name': requested_sheet_name, 'destination': 'google_sheets', 'destination_name': spreadsheet_name})}\n\n"
                )

                # Success prose — skipped during agent plan runs (UI uses operation preview + spreadsheet_created card)
                if stream_prose:
                    yield f"**Spreadsheet created successfully!**\n\n"
                    yield f"**Name**: {spreadsheet_name}\n"
                    if only_headers:
                        yield f"**Columns**: {', '.join(only_headers)}\n"
                        yield f"📈 **Records**: Empty (headers only)\n"
                    elif initial_data:
                        # Extract headers correctly for display
                        if isinstance(initial_data, dict):
                            display_headers = list(initial_data.keys())
                        elif isinstance(initial_data, list) and len(initial_data) > 0:
                            display_headers = list(initial_data[0].keys())
                        else:
                            display_headers = []

                        yield f"**Columns**: {', '.join(display_headers)}\n"
                        # Show actual record count
                        if record_count == 1:
                            yield f"📈 **Records**: 1 record added\n"
                        else:
                            yield f"📈 **Records**: {record_count} records added\n"
                    yield f"\n**View Spreadsheet**: [Open in Google Sheets]({spreadsheet_url})\n\n"
                
            except Exception as e:
                print(f"❌ Error creating spreadsheet: {e}")
                yield f"**Error creating spreadsheet**: {str(e)}\n\n"
        
        return generate()
        
    except Exception as e:
        import traceback
        error_message = str(e)
        error_traceback = traceback.format_exc()
        print(f"❌ Error in handle_google_sheets_create_from_prompt_stream: {error_message}")
        print(f"🔍 DEBUG [handle_google_sheets_create_from_prompt_stream] Full traceback:\n{error_traceback}")
        async def generate():
            yield f"**Error creating spreadsheet**: {error_message}\n\n"
        return generate()


