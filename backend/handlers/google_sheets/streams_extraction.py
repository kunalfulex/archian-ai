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

from handlers.google_sheets.background import process_google_sheets_extraction_background
from handlers.google_sheets.detection import detect_google_sheet_from_prompt

# In-memory storage for pending update confirmations
# In production, this should be in Redis or database with TTL
pending_google_sheets_updates = {}



async def handle_google_sheets_extraction_stream(
    prompt: str,
    user_id: str,
    conversation_history: list = None,
    destination: str = "salesforce",
    destination_name: str = None,
    source_name: str = None,
    conversation_id: Optional[str] = None,
    source_sheet_name: Optional[str] = None,
    destination_sheet_name: Optional[str] = None,
    source_entity_key: Optional[str] = None,
    extract_field_scope: Optional[dict] = None,
    extract_contract: Optional[dict] = None,
):
    """
    Handle Google Sheets data extraction requests.
    Emits job_id early so frontend can cancel before slow setup (sheet detection, DB lookup) completes.
    """
    if extract_contract:
        source_name = source_name or extract_contract.get("spreadsheet_name")
        source_sheet_name = source_sheet_name or extract_contract.get("sheet_name")
        if not source_entity_key:
            source_entity_key = extract_contract.get("entity_key") or source_entity_key

    try:
        from db.connection import database
        import json
        import uuid
        import asyncio

        # 1. Check if user has Google Sheets connection (must pass before creating job)
        from utils.google_sheets_auth import get_token_for_detection, user_has_google_sheets_connection

        if not await user_has_google_sheets_connection(user_id):
            async def generate():
                yield "**Google Sheets Connection Required**\n\n"
                yield "I'd love to help you extract data from your Google Spreadsheet! However, I need you to connect your Google Sheets account first.\n\n"
                yield "**Here's how to connect:**\n"
                yield "1. Go to the **Connections** page\n"
                yield "2. Click on **Google Sheets**\n"
                yield "3. Authorize Archian AI to access your spreadsheets\n\n"
                yield "Once connected, come back and I'll help you extract the data!"
            return generate()

        # 2. Create job early so frontend can cancel before slow setup
        from utils.job_stores import extraction_jobs
        job_id = str(uuid.uuid4())
        await extraction_jobs.set(job_id, {
            "status": "starting",
            "conversation_id": conversation_id,
            "user_id": str(user_id),
            "source_type": "google_sheets",
        })

        async def generate():
            nonlocal job_id
            try:
                # 3. Yield extraction_job_created first (enables cancel before setup)
                yield f"data: {json.dumps({'event_type': 'extraction_job_created', 'job_id': job_id})}\n\n"

                await extraction_jobs.update(
                    job_id, {"message": "Locating your spreadsheet…", "progress": 0}
                )
                yield extraction_setup_progress_sse_line(
                    message="Locating your spreadsheet…",
                    phase="resolve_sheet",
                    source_type="google_sheets",
                    job_id=job_id,
                    progress=0,
                )

                # 4. Slow setup: intent_state and sheet detection
                from handlers.classification_handler import get_active_intent_state
                from handlers.google_sheets.detection import (
                    detect_google_sheet_from_prompt,
                    resolve_gsheet_from_plan_entity_key,
                )

                intent_state = await get_active_intent_state(
                    conversation_id, user_id, conversation_history or [], source_type="google_sheets"
                )
                sheet_details = None
                pinned_key = (source_entity_key or "").strip()
                if pinned_key:
                    sheet_details = await resolve_gsheet_from_plan_entity_key(
                        user_id,
                        pinned_key,
                        destination_sheet_name=destination_sheet_name,
                    )
                    if not sheet_details.get("found"):
                        print(
                            f"⚠️ Plan-pinned GSheets entity not in catalog: {pinned_key} — "
                            "falling back to prompt detection"
                        )
                        sheet_details = None
                if not sheet_details:
                    sheet_details = await detect_google_sheet_from_prompt(
                        prompt,
                        user_id,
                        conversation_history,
                        intent_state=intent_state,
                        source_name=source_name,
                        destination_name=destination_name,
                        source_sheet_name=source_sheet_name,
                        destination_sheet_name=destination_sheet_name,
                    )

                # Ambiguous workbook name across Google accounts
                if sheet_details and sheet_details.get("error") == "ambiguous_spreadsheet_name":
                    await extraction_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
                    yield f"**Which workbook did you mean?**\n\n"
                    yield sheet_details.get("message") or ""
                    yield "\n\n"
                    for c in sheet_details.get("candidates") or []:
                        email = c.get("google_account_email") or c.get("connection_label")
                        name = c.get("spreadsheet_name") or "workbook"
                        yield f"- **{name}** ({email})\n"
                    yield "\n"
                    return

                google_sheets_connection = await get_token_for_detection(user_id, sheet_details)

                # Error: file not created by Archian AI
                if sheet_details and sheet_details.get("error") == "not_created_by_edison":
                    await extraction_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
                    yield f"**⚠️ Cannot Access This Spreadsheet**\n\n"
                    yield f"I can only access spreadsheets **created through Archian AI** due to Google's security policies.\n\n"
                    yield f"**This spreadsheet was created outside of Archian AI**, so I don't have permission to access it.\n\n"
                    yield f"**✅ How to Fix This:**\n\n"
                    yield f"1. Ask me: *\"Create a spreadsheet called [Your Name]\"*\n"
                    yield f"2. I'll create it and give you the URL\n"
                    yield f"3. Copy your data from the original spreadsheet into the new one\n"
                    yield f"4. ✅ Now you can use it!\n\n"
                    yield f"*Note: Even if you own the original spreadsheet, I can't access it because you didn't create it through Archian AI.*\n\n"
                    return

                # Error: shared file limitation
                if sheet_details and sheet_details.get("error") == "shared_file_limitation":
                    await extraction_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
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
                    return

                # Detection didn't find a spreadsheet
                if not sheet_details or not sheet_details.get("found", False):
                    await extraction_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
                    yield f"**Google Sheets Data Extraction**\n\n"
                    yield f"I'd love to help you extract data from your Google Spreadsheet!\n\n"
                    account_hint = ""
                    if google_sheets_connection and google_sheets_connection.get("google_account_email"):
                        account_hint = google_sheets_connection["google_account_email"]
                    else:
                        from services.google_sheets_connection_service import list_connections
                        conns = await list_connections(str(user_id))
                        if conns:
                            account_hint = ", ".join(
                                c.get("google_account_email") or c.get("connection_name") or "account"
                                for c in conns[:3]
                            )
                    if account_hint:
                        yield f"**Your Connected Account(s):** {account_hint}\n\n"
                    user_spreadsheets = await database.fetch_all(
                        """
                        SELECT ugs.spreadsheet_name, ugs.sheet_name, ugs.access_count, ugs.is_favorite, ugs.last_accessed_at
                        FROM user_google_spreadsheets ugs
                        JOIN google_sheets_connections gsc ON ugs.google_sheets_connection_id = gsc.id
                        WHERE ugs.user_id = :user_id
                        ORDER BY ugs.is_favorite DESC, ugs.last_accessed_at DESC
                        LIMIT 10
                        """,
                        {"user_id": user_id}
                    )
                    if user_spreadsheets:
                        yield f"**Your Accessible Spreadsheets:**\n\n"
                        for i, sheet in enumerate(user_spreadsheets, 1):
                            yield f"{i}. **{sheet['spreadsheet_name']}**\n"
                            yield f"   - Sheet: {sheet['sheet_name']}\n"
                            yield f"   - Accessed: {sheet['access_count']} times\n"
                            yield f"   - Last used: {sheet['last_accessed_at'].strftime('%Y-%m-%d %H:%M') if sheet['last_accessed_at'] else 'Never'}\n\n"
                        yield f"**How to proceed:**\n"
                        yield f"- Say: \"Extract data from [spreadsheet name]\" (e.g., \"Extract data from UAT Test\")\n"
                        yield f"- Or: \"Get data from [spreadsheet name]\" (e.g., \"Get data from Drive API Test\")\n\n"
                    else:
                        yield f"I couldn't identify which spreadsheet you want to extract from. Please provide:\n\n"
                        yield f"**Option 1:** Spreadsheet URL\n"
                        yield f"```\nhttps://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/...\n```\n\n"
                        yield f"**Option 2:** Spreadsheet Name\n"
                        yield f"- Example: \"Extract data from my 'Customer Database' spreadsheet\"\n"
                        yield f"- Example: \"Get data from 'Q4 Sales Report'\"\n\n"
                    return

                # 5. Look up spreadsheet in DB
                spreadsheet_name = sheet_details.get('spreadsheet_name')
                sheet_name = sheet_details.get('sheet_name')
                range_name = sheet_details.get('range_name') or 'A1:ZZ'
                spreadsheet_record = await database.fetch_one(
                    """
                    SELECT spreadsheet_id, spreadsheet_name, sheet_name
                    FROM user_google_spreadsheets ugs
                    JOIN google_sheets_connections gsc ON ugs.google_sheets_connection_id = gsc.id
                    WHERE ugs.user_id = :user_id
                      AND (ugs.spreadsheet_name = :spreadsheet_name OR ugs.spreadsheet_id = :spreadsheet_id)
                    ORDER BY ugs.last_accessed_at DESC
                    LIMIT 1
                    """,
                    {"user_id": user_id, "spreadsheet_name": spreadsheet_name, "spreadsheet_id": sheet_details.get('spreadsheet_id')}
                )

                if not spreadsheet_record:
                    await extraction_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
                    yield f"**Spreadsheet Not Found:** Could not find '{spreadsheet_name}' in your accessible spreadsheets.\n\n"
                    user_spreadsheets = await database.fetch_all(
                        """
                        SELECT ugs.spreadsheet_name, ugs.sheet_name, ugs.access_count, ugs.is_favorite, ugs.last_accessed_at
                        FROM user_google_spreadsheets ugs
                        JOIN google_sheets_connections gsc ON ugs.google_sheets_connection_id = gsc.id
                        WHERE ugs.user_id = :user_id
                        ORDER BY ugs.is_favorite DESC, ugs.last_accessed_at DESC
                        LIMIT 10
                        """,
                        {"user_id": user_id}
                    )
                    if user_spreadsheets:
                        yield f"**Your Connected Spreadsheets:**\n\n"
                        for i, sheet in enumerate(user_spreadsheets, 1):
                            yield f"{i}. **{sheet['spreadsheet_name']}**\n"
                            yield f"   - Sheet: {sheet['sheet_name']}\n"
                            yield f"   - Accessed: {sheet['access_count']} times\n"
                            yield f"   - Last used: {sheet['last_accessed_at'].strftime('%Y-%m-%d %H:%M') if sheet['last_accessed_at'] else 'Never'}\n\n"
                    yield f"**Which spreadsheet would you like to extract data from?**\n\n"
                    return

                spreadsheet_id = spreadsheet_record['spreadsheet_id']

                await extraction_jobs.update(
                    job_id,
                    {
                        "message": f"Reading “{spreadsheet_name}” ({sheet_name})…",
                        "spreadsheet_name": spreadsheet_name,
                    },
                )
                yield extraction_setup_progress_sse_line(
                    message=f"Reading “{spreadsheet_name}” ({sheet_name})…",
                    phase="read_sheet",
                    source_type="google_sheets",
                    job_id=job_id,
                    progress=0,
                )

                # 6. Check if job was cancelled during setup
                existing_job = await extraction_jobs.get(job_id) or {}
                if existing_job.get("cancelled"):
                    return

                # 7. Start background extraction with existing job_id
                dest_tab = (
                    sheet_details.get("destination_sheet_name")
                    or destination_sheet_name
                )
                from services.planning.extract_field_scope import resolve_extract_field_scope

                resolved_field_scope = resolve_extract_field_scope(
                    extract_field_scope,
                    source_type="google_sheets",
                    destination=destination,
                    source_name=source_name or spreadsheet_name,
                    destination_name=destination_name,
                )
                asyncio.create_task(process_google_sheets_extraction_background(
                    job_id=job_id,
                    spreadsheet_id=spreadsheet_id,
                    spreadsheet_name=spreadsheet_name,
                    sheet_name=sheet_name,
                    range_name=range_name,
                    extraction_goals=prompt,
                    user_id=user_id,
                    google_sheets_connection_id=google_sheets_connection['id'],
                    destination=destination,
                    destination_name=destination_name,
                    destination_sheet_name=dest_tab,
                    conversation_id=conversation_id,
                    extract_field_scope=resolved_field_scope,
                ))
                yield f"data: {json.dumps({'status': 'processing', 'job_id': job_id, 'source_type': 'google_sheets', 'message': 'Google Sheets extraction started in background'})}\n\n"

                from utils.extraction_sse_stream import stream_extraction_job_events_until_terminal

                async for evt in stream_extraction_job_events_until_terminal(job_id, extraction_jobs):
                    yield f"data: {json.dumps(evt, default=str)}\n\n"

            except Exception as e:
                print(f"❌ Error extracting data from Google Sheets: {e}")
                from utils.google_sheets_errors import build_google_sheets_extraction_error_payload

                err_payload = build_google_sheets_extraction_error_payload(e, job_id=job_id)
                err_msg = err_payload.get("message") or str(e)
                job_status = {
                    "status": "failed",
                    "error": err_msg,
                    "job_id": job_id,
                    "source_type": "google_sheets",
                }
                if err_payload.get("code"):
                    job_status["error_code"] = err_payload["code"]
                try:
                    await extraction_jobs.update(
                        job_id,
                        {
                            "cancelled": True,
                            "status": "failed",
                            "error": err_msg,
                            "source_type": "google_sheets",
                        },
                    )
                except Exception:
                    pass
                yield (
                    f"data: {json.dumps({'event_type': 'extraction_job_finished', 'job_id': job_id, 'job_status': job_status})}\n\n"
                )
                yield f"data: {json.dumps(err_payload)}\n\n"

        return generate()

    except Exception as outer_error:
        print(f"❌ Error in Google Sheets extraction handler: {outer_error}")
        from utils.google_sheets_errors import build_google_sheets_extraction_error_payload

        err_payload = build_google_sheets_extraction_error_payload(outer_error)

        async def generate():
            yield f"data: {json.dumps(err_payload)}\n\n"

        return generate()


