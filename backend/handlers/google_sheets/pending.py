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

# In-memory storage for pending update confirmations
# In production, this should be in Redis or database with TTL
pending_google_sheets_updates = {}


async def check_for_pending_update_confirmation(user_id: str, prompt: str) -> dict:
    """
    Check if the user is responding to a pending update confirmation.
    
    Returns:
        {
            "has_pending": True/False,
            "action": "update" | "add_new" | "cancel" | "select_number",
            "pending_data": {...} if has_pending else None
        }
    """
    from services.google_sheets.pending_store import load_pending

    pending = await load_pending(user_id)
    if not pending and user_id in pending_google_sheets_updates:
        pending = pending_google_sheets_updates[user_id]
    if not pending:
        return {"has_pending": False, "action": None, "pending_data": None}
    prompt_lower = prompt.lower().strip()
    
    # Check for confirmation keywords
    if prompt_lower in ['update', 'yes', 'y', 'confirm', 'replace']:
        return {"has_pending": True, "action": "update", "pending_data": pending}
    elif prompt_lower in ['add new', 'add', 'new', 'no', 'n', 'skip']:
        return {"has_pending": True, "action": "add_new", "pending_data": pending}
    elif prompt_lower in ['cancel', 'abort', 'stop']:
        return {"has_pending": True, "action": "cancel", "pending_data": pending}
    elif prompt_lower.isdigit():
        # User selected a number from multiple matches
        selected_index = int(prompt_lower) - 1  # Convert to 0-indexed
        return {"has_pending": True, "action": "select_number", "pending_data": pending, "selected_index": selected_index}
    
    return {"has_pending": False, "action": None, "pending_data": None}


async def handle_update_confirmation_response(user_id: str, prompt: str, conversation_history: list = None):
    """
    Handle user's response to update confirmation.
    Executes the update, add, or cancel based on user's choice.
    """
    try:
        from db.connection import database
        from services.google_sheets_service import GoogleSheetsService
        
        # Check for pending update
        confirmation = await check_for_pending_update_confirmation(user_id, prompt)
        
        if not confirmation["has_pending"]:
            return None  # No pending update, continue normal flow
        
        pending = confirmation["pending_data"]
        action = confirmation["action"]
        
        spreadsheet_id = pending["spreadsheet_id"]
        spreadsheet_name = pending["spreadsheet_name"]
        sheet_name = pending["sheet_name"]
        new_data = pending["new_data"]
        headers = pending["headers"]
        
        # Get valid Google Sheets token (refreshes if expired)
        from utils.google_sheets_auth import get_token_for_detection, user_has_google_sheets_connection
        from services.google_sheets_connection_service import resolve_connection_for_spreadsheet

        google_sheets_connection = await resolve_connection_for_spreadsheet(user_id, spreadsheet_id)
        if not google_sheets_connection:
            google_sheets_connection = await get_token_for_detection(user_id, {"spreadsheet_id": spreadsheet_id})
        
        if not google_sheets_connection:
            async def generate():
                yield "**Google Sheets connection not found**\n\n"
                # Clear pending update
                from services.google_sheets.pending_store import clear_pending

                await clear_pending(user_id)
                if user_id in pending_google_sheets_updates:
                    del pending_google_sheets_updates[user_id]
            return generate()
        
        # Initialize Google Sheets Service
        sheets_service = GoogleSheetsService(
            access_token=google_sheets_connection['access_token'],
            refresh_token=google_sheets_connection['refresh_token'],
            expires_at=google_sheets_connection['expires_at']
        )
        
        async def generate():
            try:
                if action == "cancel":
                    yield f"**Operation cancelled**\n\n"
                    yield f"No changes were made to '{spreadsheet_name}'.\n\n"
                    # Clear pending update
                    if user_id in pending_google_sheets_updates:
                        del pending_google_sheets_updates[user_id]
                    return
                
                elif action == "add_new":
                    yield f"**Adding as new record...**\n\n"
                    
                    # Validate plan limits
                    is_valid, message, plan_info = await get_entitlements().validate_migration(user_id, 1)
                    if not is_valid:
                        yield f"**Plan Limit Reached**\n\n{message}\n\n"
                        # Clear pending update
                        if user_id in pending_google_sheets_updates:
                            del pending_google_sheets_updates[user_id]
                        return
                    
                    # Prepare row data
                    new_row = []
                    for header in headers:
                        new_row.append(new_data.get(header, ""))
                    
                    # Append to spreadsheet
                    append_result = await sheets_service.append_to_spreadsheet(
                        spreadsheet_id=spreadsheet_id,
                        data=[new_row],
                        sheet_name=sheet_name
                    )
                    
                    if append_result.get('success'):
                        await get_entitlements().track_successful_migration(user_id, 1)
                        yield f"**Record added successfully!**\n\n"
                        yield f"Added: {', '.join([f'{k}={v}' for k, v in new_data.items()])}\n\n"
                    else:
                        yield f"**Error adding record**: {append_result.get('error', 'Unknown error')}\n\n"
                    
                    # Clear pending update
                    if user_id in pending_google_sheets_updates:
                        del pending_google_sheets_updates[user_id]
                
                elif action == "update":
                    yield f"**Updating existing record...**\n\n"
                    
                    # Validate plan limits (updates count as migrations too)
                    is_valid, message, plan_info = await get_entitlements().validate_migration(user_id, 1)
                    if not is_valid:
                        yield f"**Plan Limit Reached**\n\n{message}\n\n"
                        # Clear pending update
                        if user_id in pending_google_sheets_updates:
                            del pending_google_sheets_updates[user_id]
                        return
                    
                    # Get the row index to update
                    row_to_update = pending.get("duplicate_row_index")
                    duplicate_record = pending.get("duplicate_record", {})
                    
                    # Merge new data with existing record (update only provided fields)
                    updated_record = duplicate_record.copy()
                    updated_record.update(new_data)
                    
                    # Update the specific row using Google Sheets API
                    update_result = await sheets_service.update_spreadsheet(
                        spreadsheet_id=spreadsheet_id,
                        data=[updated_record],
                        sheet_name=sheet_name,
                        start_row=row_to_update
                    )
                    
                    if update_result.get('success'):
                        await get_entitlements().track_successful_migration(user_id, 1)
                        try:
                            from utils.credit_config import compute_migration_credits
                            user_plan = await get_entitlements().get_user_plan(user_id)
                            credits_per_month = user_plan.get("credits_per_month") if user_plan else None
                            if credits_per_month is not None and credits_per_month != -1:
                                credits = compute_migration_credits(1)
                                if credits > 0:
                                    await get_entitlements().deduct(user_id=user_id, amount=credits, reference_type="migration", reference_id="gsheets_smart_update")
                        except Exception as e:
                            print(f"⚠️ [GSHEETS_SMART_UPDATE] Failed to deduct credits: {e}")
                        yield f"**Record updated successfully!**\n\n"
                        
                        # Show what was updated
                        updated_fields = [k for k in new_data.keys() if new_data[k] != duplicate_record.get(k)]
                        if updated_fields:
                            yield f"**Updated fields:**\n"
                            for field in updated_fields:
                                old_val = duplicate_record.get(field, "")
                                new_val = new_data.get(field, "")
                                yield f"  • **{field}**: '{old_val}' → '{new_val}'\n"
                            yield f"\n"
                        else:
                            yield f"**All fields remain the same**\n\n"
                    else:
                        yield f"**Error updating record**: {update_result.get('error', 'Unknown error')}\n\n"
                    
                    # Clear pending update
                    if user_id in pending_google_sheets_updates:
                        del pending_google_sheets_updates[user_id]
                
                elif action == "select_number":
                    # User selected a specific duplicate from multiple matches
                    selected_index = confirmation.get("selected_index", 0)
                    duplicates = pending.get("all_duplicates", [])
                    
                    if selected_index < 0 or selected_index >= len(duplicates):
                        yield f"**Invalid selection**. Please choose a number between 1 and {len(duplicates)}.\n\n"
                        return
                    
                    # Update pending with selected duplicate
                    selected_duplicate = duplicates[selected_index]
                    duplicate_record = selected_duplicate.get("matched_record", {})
                    
                    # Clean up matched_record if it has the wrong structure
                    if isinstance(duplicate_record, dict) and 'row' in duplicate_record and 'data' in duplicate_record:
                        duplicate_record = duplicate_record['data']
                    
                    pending_google_sheets_updates[user_id].update({
                        "duplicate_row_index": selected_duplicate.get("row_index"),
                        "duplicate_record": duplicate_record,  # Use cleaned record
                        "all_duplicates": []  # Clear to prevent re-selection
                    })
                    
                    # Show confirmation for the selected record
                    yield f"**You selected record #{selected_index + 1}**\n\n"
                    yield f"**Current Record**:\n"
                    for key, value in duplicate_record.items():
                        yield f"  • {key}: {value}\n"
                    yield f"\n**Your New Data**:\n"
                    for key, value in new_data.items():
                        yield f"  • {key}: {value}\n"
                    yield f"\n**Confirm:**\n"
                    yield f"• Say **'Update'** to replace this record\n"
                    yield f"• Say **'Add New'** to add as separate record instead\n"
                    yield f"• Say **'Cancel'** to abort\n\n"
                
            except Exception as e:
                print(f"❌ Error handling update confirmation: {e}")
                import traceback
                print(f"Traceback: {traceback.format_exc()}")
                yield f"**Error**: {str(e)}\n\n"
                # Clear pending update
                if user_id in pending_google_sheets_updates:
                    del pending_google_sheets_updates[user_id]
        
        return generate()
        
    except Exception as e:
        print(f"❌ Error in handle_update_confirmation_response: {e}")
        error_message = str(e)

        async def generate():
            yield f"**Error**: {error_message}\n\n"
        return generate()

