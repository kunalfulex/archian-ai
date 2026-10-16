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

from handlers.google_sheets.background import store_google_spreadsheet_metadata

# In-memory storage for pending update confirmations
# In production, this should be in Redis or database with TTL
pending_google_sheets_updates = {}


async def resolve_gsheet_from_plan_entity_key(
    user_id: str,
    source_entity_key: str,
    *,
    destination_sheet_name: Optional[str] = None,
) -> dict:
    """
    Resolve spreadsheet + tab from plan contract entity_key (spreadsheet_id::tab).
    Skips LLM/detection when the pinned key exists in synced catalog.
    """
    key = (source_entity_key or "").strip()
    if not key or "::" not in key:
        return {"found": False}
    spreadsheet_id, sheet_name = key.split("::", 1)
    spreadsheet_id = spreadsheet_id.strip()
    sheet_name = sheet_name.strip()
    if not spreadsheet_id or not sheet_name:
        return {"found": False}

    from db.connection import database

    row = await database.fetch_one(
        """
        SELECT ugs.spreadsheet_id, ugs.spreadsheet_name, ugs.sheet_name
        FROM user_google_spreadsheets ugs
        JOIN google_sheets_connections gsc ON ugs.google_sheets_connection_id = gsc.id
        WHERE ugs.user_id = :user_id
          AND ugs.spreadsheet_id = :spreadsheet_id
        ORDER BY CASE WHEN LOWER(ugs.sheet_name) = LOWER(:sheet_name) THEN 0 ELSE 1 END,
                 ugs.last_accessed_at DESC NULLS LAST
        LIMIT 1
        """,
        {
            "user_id": str(user_id),
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
        },
    )
    if not row:
        row = await database.fetch_one(
            """
            SELECT ges.spreadsheet_id, ges.spreadsheet_name, ges.sheet_name
            FROM gsheets_extracted_schemas ges
            WHERE ges.user_id = :user_id
              AND ges.spreadsheet_id = :spreadsheet_id
            ORDER BY CASE WHEN LOWER(ges.sheet_name) = LOWER(:sheet_name) THEN 0 ELSE 1 END
            LIMIT 1
            """,
            {
                "user_id": str(user_id),
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
            },
        )
    if not row:
        return {"found": False}

    print(
        f"✅ GSheets extract pinned from plan contract: "
        f"{row['spreadsheet_name']} / tab {sheet_name} ({spreadsheet_id})"
    )
    return {
        "found": True,
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_name": row["spreadsheet_name"],
        "sheet_name": sheet_name,
        "range_name": "A1:ZZ",
        "destination_sheet_name": destination_sheet_name,
        "source": "plan_entity_pinned",
    }


async def detect_google_sheet_from_prompt(
    prompt: str, 
    user_id: str, 
    conversation_history: list = None,
    intent_state: Optional[Dict] = None,
    source_name: Optional[str] = None,
    destination_name: Optional[str] = None,
    source_sheet_name: Optional[str] = None,
    destination_sheet_name: Optional[str] = None,
) -> dict:
    """
    Detect Google Spreadsheet details from user prompt and conversation.
    Searches both current prompt AND conversation history for spreadsheet names.
    If source_name is provided (from classification), uses it directly (highest priority).
    If intent_state is provided, uses spreadsheet from intent_state.
    
    Returns dict with spreadsheet_id, spreadsheet_name, sheet_name, range_name, or None if not detected.
    """
    try:
        import re
        from db.connection import database

        from services.google_sheets_connection_service import (
            resolve_workbook_by_name,
            resolve_workbook_by_spreadsheet_id,
        )

        # PRIORITY 1: Use source_name (+ optional tab) from classification if provided
        if source_name:
            result = await resolve_workbook_by_name(
                str(user_id),
                source_name,
                source_sheet_name,
                source="classification_source_name",
                extra={"destination_sheet_name": destination_sheet_name},
            )
            if result.get("found") or result.get("error") == "ambiguous_spreadsheet_name":
                if result.get("found"):
                    print(
                        f"✅ Using source from classification: {source_name}"
                        + (f" (tab: {source_sheet_name})" if source_sheet_name else "")
                    )
                return result
        
        # PRIORITY 2: Try to extract spreadsheet URL/ID using regex
        url_pattern = r'https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)'
        url_match = re.search(url_pattern, prompt)
        
        if url_match:
            spreadsheet_id = url_match.group(1)
            
            # Fetch spreadsheet name from Google Sheets API
            try:
                from services.google_sheets_service import GoogleSheetsService
                from services.google_sheets_connection_service import resolve_connection_for_spreadsheet

                token_info = await resolve_connection_for_spreadsheet(user_id, spreadsheet_id)
                if not token_info:
                    raise ValueError("No Google Sheets connection")

                google_sheets_connection = {
                    "id": token_info.get("id") or token_info.get("connection_id"),
                    "access_token": token_info["access_token"],
                    "refresh_token": token_info.get("refresh_token"),
                    "expires_at": token_info.get("expires_at"),
                }

                sheets_service = GoogleSheetsService(
                    access_token=google_sheets_connection['access_token'],
                    refresh_token=google_sheets_connection['refresh_token'],
                    expires_at=google_sheets_connection['expires_at']
                )

                from db.connection import database

                # Get spreadsheet metadata
                spreadsheet_metadata = await sheets_service.get_spreadsheet_metadata(spreadsheet_id)
                spreadsheet_name = spreadsheet_metadata.get('properties', {}).get('title', f'Spreadsheet_{spreadsheet_id}')

                # Collect available sheets from metadata (title and sheetId)
                sheets = spreadsheet_metadata.get('sheets', []) or []
                parsed_sheets = []
                for s in sheets:
                    props = (s or {}).get('properties', {})
                    title = props.get('title')
                    sid = props.get('sheetId')
                    if title is not None and sid is not None:
                        parsed_sheets.append((str(title), str(sid)))

                # If API returned no sheets, fall back to a single unknown sheet entry
                if not parsed_sheets:
                    parsed_sheets = [(None, None)]

                # Check if any spreadsheet rows already exist for this spreadsheet_id
                existing_rows = await database.fetch_all(
                    """
                    SELECT id, sheet_name, sheet_id
                    FROM user_google_spreadsheets
                    WHERE user_id = :user_id
                      AND spreadsheet_id = :spreadsheet_id
                      AND google_sheets_connection_id = :connection_id
                    """,
                    {
                        "user_id": user_id,
                        "spreadsheet_id": spreadsheet_id,
                        "connection_id": google_sheets_connection['id']
                    }
                )

                is_new_spreadsheet = len(existing_rows) == 0

                # Upsert one row per sheet found in metadata
                for sheet_title, sheet_id_str in parsed_sheets:
                    # If there is an older NULL entry, upgrade it to the real sheet
                    null_row = next((r for r in existing_rows if r['sheet_name'] is None), None)
                    if null_row and sheet_title:
                        await database.execute(
                            """
                            UPDATE user_google_spreadsheets
                            SET sheet_name = :sheet_name,
                                sheet_id = :sheet_id,
                                access_count = access_count + 1,
                                last_accessed_at = NOW(),
                                updated_at = NOW()
                            WHERE id = :id
                            """,
                            {"sheet_name": sheet_title, "sheet_id": sheet_id_str, "id": null_row['id']}
                        )
                        existing_rows = [
                            {**r, 'sheet_name': sheet_title, 'sheet_id': sheet_id_str} if r['id'] == null_row['id'] else r
                            for r in existing_rows
                        ]
                        continue

                    already_exists = next(
                        (r for r in existing_rows if r['sheet_name'] == sheet_title),
                        None
                    )
                    if already_exists:
                        await database.execute(
                            """
                            UPDATE user_google_spreadsheets 
                            SET access_count = access_count + 1, last_accessed_at = NOW(), updated_at = NOW()
                            WHERE id = :id
                            """,
                            {"id": already_exists['id']}
                        )
                    else:
                        await store_google_spreadsheet_metadata(
                            user_id=user_id,
                            spreadsheet_id=spreadsheet_id,
                            spreadsheet_name=spreadsheet_name,
                            sheet_name=sheet_title,
                            sheet_id=sheet_id_str,
                            google_sheets_connection_id=google_sheets_connection['id']
                        )

                if is_new_spreadsheet:
                    import asyncio
                    from services.gsheets_background_processing_service import GoogleSheetsBackgroundProcessingService

                    user_record = await database.fetch_one(
                        "SELECT email FROM users WHERE id = :user_id",
                        {"user_id": user_id}
                    )
                    user_email = user_record['email'] if user_record else ""

                    service = GoogleSheetsBackgroundProcessingService()
                    asyncio.create_task(service.process_spreadsheet_analysis(
                        google_sheets_connection_id=google_sheets_connection['id'],
                        spreadsheet_id=spreadsheet_id,
                        spreadsheet_name=spreadsheet_name,
                        user_id=user_id,
                        user_email=user_email
                    ))

                return {
                    "spreadsheet_id": spreadsheet_id,
                    "spreadsheet_name": spreadsheet_name,
                    "sheet_name": None,
                    "range_name": None,
                    "source": "url",
                    "found": True,
                    "google_sheets_connection_id": str(google_sheets_connection["id"]),
                }
            except HttpError as e:
                error_str = str(e)
                print(f"❌ Error fetching spreadsheet metadata: {e}")
                
                # Check if it's a 403 permission error OR 404 not found error (both indicate shared file limitation)
                if '403' in error_str or 'permission' in error_str.lower():
                    print(f"🔑 403 Permission Error - Spreadsheet not accessible (shared file limitation)")
                    return {
                        "found": False,
                        "spreadsheet_id": spreadsheet_id,
                        "spreadsheet_url": url_match.group(0),
                        "error": "shared_file_limitation",
                        "message": "I can only access spreadsheets created through Archian AI. Please ask me to create a new spreadsheet and copy your data into it."
                    }
                
                # Check if it's a 404 not found error (file not accessible with drive.file scope)
                if '404' in error_str or 'not found' in error_str.lower():
                    print(f"🔑 404 Not Found Error - Spreadsheet not accessible (not created through Archian AI)")
                    return {
                        "found": False,
                        "spreadsheet_id": spreadsheet_id,
                        "spreadsheet_url": url_match.group(0),
                        "error": "not_created_by_edison",
                        "message": "I can only access spreadsheets created through Archian AI due to Google's security policies."
                    }
                
                # Other errors - return basic info
                return {
                    "spreadsheet_id": spreadsheet_id,
                    "spreadsheet_name": f"Spreadsheet_{spreadsheet_id}",
                    "sheet_name": None,
                    "range_name": None,
                    "source": "url",
                    "found": True
                }
            except Exception as e:
                print(f"❌ Unexpected error fetching spreadsheet metadata: {e}")
                return {
                    "spreadsheet_id": spreadsheet_id,
                    "spreadsheet_name": f"Spreadsheet_{spreadsheet_id}",
                    "sheet_name": None,
                    "range_name": None,
                    "source": "url",
                    "found": True
                }
        
        # Fetch user's connected Google Spreadsheets
        available_spreadsheets = await database.fetch_all(
            """
            SELECT spreadsheet_id, spreadsheet_name, sheet_name
            FROM user_google_spreadsheets
            WHERE user_id = :user_id
            ORDER BY updated_at DESC
            """,
            {"user_id": str(user_id)}
        )
        
        # Format spreadsheets for the prompt
        import json
        spreadsheet_list = []
        if available_spreadsheets:
            for sheet in available_spreadsheets:
                spreadsheet_list.append({
                    "spreadsheet_id": sheet["spreadsheet_id"],
                    "spreadsheet_name": sheet["spreadsheet_name"],
                    "sheet_name": sheet["sheet_name"] if sheet["sheet_name"] else "Not specified"
                })

        # PRIORITY 3: If no explicit mention, check intent_state (for clarification flows)
        # Only use intent_state if prompt is short (likely a clarification)
        prompt_words = prompt.strip().split()
        is_short_clarification = len(prompt_words) <= 5  # Very short prompts like "yes", "that one", "first name, email"
        
        if intent_state and intent_state.get("active_source") == "google_sheets" and is_short_clarification:
            source_name = intent_state.get("source_name")
            if source_name:
                result = await resolve_workbook_by_name(
                    str(user_id),
                    source_name,
                    source="intent_state",
                )
                if result.get("found") or result.get("error") == "ambiguous_spreadsheet_name":
                    return result
        
        # PRIORITY 5: Search conversation_history for spreadsheet names (most recent first)
        if conversation_history:
            print(f"🔍 Searching conversation history for Google Spreadsheet names...")
            spreadsheet_names = [sheet["spreadsheet_name"] for sheet in available_spreadsheets if sheet["spreadsheet_name"]]
            for msg in reversed(conversation_history):
                msg_text = msg.get('text', '') or msg.get('content', '')
                if not msg_text:
                    continue
                
                # Check if any spreadsheet name appears in this message
                for spreadsheet_name in spreadsheet_names:
                    if spreadsheet_name.lower() in msg_text.lower():
                        result = await resolve_workbook_by_name(
                            str(user_id),
                            spreadsheet_name,
                            source="conversation_history",
                        )
                        if result.get("found") or result.get("error") == "ambiguous_spreadsheet_name":
                            return result
        
        # PRIORITY 6: Build conversation context for AI detection (fallback)
        conversation_context = ""
        if conversation_history:
            # Include recent messages (last 5) to help AI find spreadsheet name
            recent_messages = conversation_history[-5:] if len(conversation_history) > 5 else conversation_history
            conversation_context = "\n\n**Recent Conversation History:**\n"
            for msg in recent_messages:
                msg_type = msg.get('type', msg.get('role', 'user'))
                msg_text = msg.get('text', '') or msg.get('content', '')
                if msg_text:
                    role_label = "User" if msg_type in ['user', 'User'] else "Assistant"
                    conversation_context += f"{role_label}: {msg_text}\n"
        
        # Use AI to detect spreadsheet details from current user message
        detection_prompt = f"""
        Analyze the user's current message and extract Google Spreadsheet details.
        
        **AVAILABLE GOOGLE SPREADSHEETS FOR THIS USER:**
        {json.dumps(spreadsheet_list, indent=2) if spreadsheet_list else "No spreadsheets connected yet"}
        
        **Current User Message:**
        "{prompt}"
        {conversation_context}
        
        **INSTRUCTIONS:**
        1. First, check if the user mentions any spreadsheet name that matches the AVAILABLE SPREADSHEETS above
        2. Extract the following information:
           - **Spreadsheet Name**: Match from available spreadsheets (prefer exact match)
           - **Sheet/Tab Name**: Specific sheet or tab name within the spreadsheet
           - **Range**: Cell range like "A1:D100" or "Sheet1!A1:D100"
        
        **MATCHING RULES:**
        - If user says "my spreadsheet" and only ONE spreadsheet exists, use that one
        - If user mentions part of a name (e.g., "customer"), match to available spreadsheets
        - If no match found in available list but user clearly mentions a name, set "found": false 
      
        
        Return your response in this EXACT JSON format:
        {{
            "spreadsheet_name": "Exact name from available list OR user's mentioned name",
            "sheet_name": "Sheet/tab name or null",
            "range_name": "Cell range or null",
            "found": true or false
        }}
        
        **Examples:**
        
        Available: [{{"spreadsheet_name": "Customer Database", "sheet_name": "Not specified"}}]
        User: "Extract data from my customer spreadsheet"
          Response: {{"spreadsheet_name": "Customer Database", "sheet_name": null, "range_name": null, "found": true}}
        
        Available: [{{"spreadsheet_name": "Q4 Report", "sheet_name": "Leads"}}]
        User: "Get leads from Q4 report"
          Response: {{"spreadsheet_name": "Q4 Report", "sheet_name": "Leads", "range_name": null, "found": true}}
        
        Available: [{{"spreadsheet_name": "Sales Data", "sheet_name": "Not specified"}}]
        User: "Read cells A1 to E50 from my Sales Data spreadsheet"
          Response: {{"spreadsheet_name": "Sales Data", "sheet_name": null, "range_name": "A1:E50", "found": true}}
        
        Available: []
        User: "Extract from my inventory spreadsheet"
        Response: {{"spreadsheet_name": "inventory spreadsheet", "sheet_name": null, "range_name": null, "found": false}}
        
        Available: [{{"spreadsheet_name": "Spreadsheet 1", "sheet_name": "Not specified"}}, {{"spreadsheet_name": "Spreadsheet 2", "sheet_name": "Not specified"}}]
        User: "I want to extract some data"
          Response: {{"spreadsheet_name": null, "sheet_name": null, "range_name": null, "found": false}}
        
        Return ONLY the JSON, no other text.
        """
        
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.GOOGLE_SHEETS_DETECTION,
            messages=[{"role": "user", "content": detection_prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        
        ai_response = response.choices[0].message.content.strip()
        
        # Use centralized JSON parsing function (handles markdown code blocks, JSON fixing, etc.)
        from utils.openai_helpers import parse_openai_json_response
        try:
            detection_result = parse_openai_json_response(ai_response)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"❌ Error parsing detection JSON response: {e}")
            raise ValueError("No valid JSON found in AI response")
        
        if detection_result.get("found", False):
            # Database lookup to get spreadsheet_id from spreadsheet_name
            spreadsheet_name = detection_result.get("spreadsheet_name")
            spreadsheet_id = None
            
            if spreadsheet_name:
                try:
                    from db.connection import database
                    
                    # Query database for spreadsheet_id
                    spreadsheet_result = await database.fetch_one(
                        """
                        SELECT spreadsheet_id, sheet_name
                        FROM user_google_spreadsheets
                        WHERE user_id = :user_id 
                          AND spreadsheet_name = :spreadsheet_name
                        ORDER BY last_accessed_at DESC
                        LIMIT 1
                        """,
                        {
                            "user_id": str(user_id),
                            "spreadsheet_name": spreadsheet_name
                        }
                    )
                    
                    if spreadsheet_result:
                        spreadsheet_id = spreadsheet_result["spreadsheet_id"]
                        # If sheet_name not detected by AI but exists in DB, use it
                        if not detection_result.get("sheet_name") and spreadsheet_result["sheet_name"]:
                            detection_result["sheet_name"] = spreadsheet_result["sheet_name"]
                        
                except Exception as db_error:
                    print(f"❌ Database lookup error: {db_error}")
            
            result = {
                "spreadsheet_id": spreadsheet_id,
                "spreadsheet_name": spreadsheet_name,
                "sheet_name": detection_result.get("sheet_name"),
                "range_name": detection_result.get("range_name"),
                "source": "ai_detection",
                "found": True
            }
            return result
        else:
            names = [s["spreadsheet_name"] for s in spreadsheet_list] if spreadsheet_list else []
            avail = ", ".join(names) if names else "None"
            result = {
                "found": False,
                "needs_clarification": True,
                "message": (
                    "Which Google Sheet should we use?"
                    + (f" Your connected workbooks: {avail}." if names else "")
                ),
            }
            return result
            
    except Exception as e:
        print(f"❌ Error detecting Google Sheet from prompt: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        # Return a proper response instead of None so the handler can show the list
        return {
            "found": False,
            "message": f"Error detecting spreadsheet: {str(e)}"
        }


