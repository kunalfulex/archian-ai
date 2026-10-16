"""Google Sheets spreadsheet listing, sync, delete, and sheet metadata endpoints."""

import asyncio
import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from db.connection import database
from routes.auth import get_current_user

router = APIRouter()


@router.get("/google-sheets/categorized-spreadsheets")
async def get_categorized_spreadsheets(
    user_id: UUID = Depends(get_current_user),
    connection_id: Optional[str] = Query(None),
):
    """
    Get all connected Google Spreadsheets for the user.
    Sheets are grouped by workbook from gsheets_extracted_schemas (all synced tabs),
    with fallback rows from user_google_spreadsheets when schema rows are not present yet.
    """
    try:
        conn_filter = ""
        params: dict = {"user_id": str(user_id)}
        if connection_id and str(connection_id).strip():
            conn_filter = " AND gsc.id = :connection_id"
            params["connection_id"] = str(connection_id).strip()

        schema_rows = await database.fetch_all(
            """
            SELECT
                ges.id,
                ges.spreadsheet_id,
                ges.spreadsheet_name,
                ges.sheet_name,
                ges.extracted_at,
                gsc.id AS google_sheets_connection_id,
                gsc.google_account_email,
                gsc.connection_name
            FROM gsheets_extracted_schemas ges
            JOIN google_sheets_connections gsc
              ON ges.google_sheets_connection_id = gsc.id
            WHERE ges.user_id = :user_id
              AND COALESCE(ges.is_deleted, FALSE) = FALSE
              """ + conn_filter + """
            ORDER BY ges.spreadsheet_name, ges.sheet_name
            """,
            params,
        )

        ugs_rows = await database.fetch_all(
            """
            SELECT
                ugs.id,
                ugs.spreadsheet_id,
                ugs.spreadsheet_name,
                ugs.sheet_name,
                ugs.sheet_id,
                ugs.created_at,
                gsc.id AS google_sheets_connection_id,
                gsc.google_account_email,
                gsc.connection_name
            FROM user_google_spreadsheets ugs
            JOIN google_sheets_connections gsc
              ON ugs.google_sheets_connection_id = gsc.id
            WHERE ugs.user_id = :user_id
              """ + conn_filter + """
            ORDER BY ugs.spreadsheet_name, ugs.sheet_name
            """,
            params,
        )

        if not schema_rows and not ugs_rows:
            return {
                "status": "success",
                "spreadsheets": [],
                "message": "No spreadsheets found. Connect a Google Sheet to get started.",
            }

        spreadsheets_dict = {}

        def _ensure_workbook(spreadsheet_id: str, row: dict) -> dict:
            if spreadsheet_id not in spreadsheets_dict:
                spreadsheets_dict[spreadsheet_id] = {
                    "spreadsheet_id": spreadsheet_id,
                    "spreadsheet_name": row.get("spreadsheet_name") or spreadsheet_id,
                    "google_sheets_connection_id": str(row.get("google_sheets_connection_id") or ""),
                    "google_account_email": row.get("google_account_email"),
                    "connection_name": row.get("connection_name"),
                    "sheets": [],
                    "_sheet_names": set(),
                }
            wb = spreadsheets_dict[spreadsheet_id]
            if row.get("spreadsheet_name"):
                wb["spreadsheet_name"] = row["spreadsheet_name"]
            return wb

        for row in schema_rows or []:
            r = dict(row)
            sid = r.get("spreadsheet_id")
            sheet_name = (r.get("sheet_name") or "").strip()
            if not sid or not sheet_name:
                continue
            wb = _ensure_workbook(sid, r)
            if sheet_name in wb["_sheet_names"]:
                continue
            wb["_sheet_names"].add(sheet_name)
            extracted = r.get("extracted_at")
            wb["sheets"].append({
                "id": str(r["id"]),
                "sheet_name": sheet_name,
                "sheet_id": None,
                "created_at": extracted.isoformat() if extracted else None,
                "source": "gsheets_extracted_schemas",
            })

        for row in ugs_rows or []:
            r = dict(row)
            sid = r.get("spreadsheet_id")
            sheet_name = (r.get("sheet_name") or "").strip()
            if not sid or not sheet_name:
                continue
            wb = _ensure_workbook(sid, r)
            if sheet_name in wb["_sheet_names"]:
                continue
            wb["_sheet_names"].add(sheet_name)
            created = r.get("created_at")
            wb["sheets"].append({
                "id": str(r["id"]),
                "sheet_name": sheet_name,
                "sheet_id": r.get("sheet_id"),
                "created_at": created.isoformat() if created else None,
                "source": "user_google_spreadsheets",
            })

        spreadsheets_list = []
        total_sheets = 0
        for wb in spreadsheets_dict.values():
            wb["sheets"].sort(key=lambda s: (s.get("sheet_name") or "").lower())
            total_sheets += len(wb["sheets"])
            del wb["_sheet_names"]
            spreadsheets_list.append(wb)

        spreadsheets_list.sort(
            key=lambda w: (w.get("spreadsheet_name") or "").lower()
        )

        return {
            "status": "success",
            "spreadsheets": spreadsheets_list,
            "total_spreadsheets": len(spreadsheets_list),
            "total_sheets": total_sheets,
        }
        
    except Exception as e:
        print(f"Error fetching categorized spreadsheets: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch categorized spreadsheets")


@router.delete("/google-sheets/spreadsheets/{spreadsheet_id}")
async def delete_google_spreadsheet(
    spreadsheet_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """
    Delete a specific Google Spreadsheet and all its sheets
    """
    try:
        # First, get the spreadsheet details to verify ownership
        spreadsheet = await database.fetch_one(
            """
            SELECT ugs.id, ugs.spreadsheet_name, ugs.sheet_name
            FROM user_google_spreadsheets ugs
            WHERE ugs.user_id = :user_id AND ugs.spreadsheet_id = :spreadsheet_id
            LIMIT 1
            """,
            {
                "user_id": str(user_id),
                "spreadsheet_id": spreadsheet_id
            }
        )
        
        if not spreadsheet:
            raise HTTPException(status_code=404, detail="Spreadsheet not found or access denied")
        
        # Delete all sheets for this spreadsheet from user_google_spreadsheets
        await database.execute(
            """
            DELETE FROM user_google_spreadsheets 
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
            """,
            {
                "user_id": str(user_id),
                "spreadsheet_id": spreadsheet_id
            }
        )
        
        # Delete all sheets for this spreadsheet from gsheets_extracted_schemas
        await database.execute(
            """
            DELETE FROM gsheets_extracted_schemas 
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
            """,
            {
                "user_id": str(user_id),
                "spreadsheet_id": spreadsheet_id
            }
        )
        
        return {
            "status": "success",
            "message": f"Spreadsheet '{spreadsheet['spreadsheet_name']}' and all its sheets have been deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting spreadsheet: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete spreadsheet")


@router.post("/google-sheets/spreadsheets/{spreadsheet_id}/sync")
async def sync_google_spreadsheet(
    spreadsheet_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """
    Re-sync and re-analyze a single connected Google Spreadsheet.
    Validates Google OAuth before starting background work; user receives an email when complete.
    """
    try:
        row = await database.fetch_one(
            """
            SELECT ugs.google_sheets_connection_id, ugs.spreadsheet_name,
                   gsc.access_token, gsc.refresh_token, gsc.expires_at
            FROM user_google_spreadsheets ugs
            JOIN google_sheets_connections gsc ON ugs.google_sheets_connection_id = gsc.id
            WHERE ugs.user_id = :user_id AND ugs.spreadsheet_id = :spreadsheet_id
              AND gsc.is_active = true
            LIMIT 1
            """,
            {"user_id": str(user_id), "spreadsheet_id": spreadsheet_id},
        )
        if not row:
            raise HTTPException(status_code=404, detail="Spreadsheet not found or access denied")

        user_result = await database.fetch_one(
            "SELECT email FROM users WHERE id = :user_id",
            {"user_id": str(user_id)}
        )
        # Row/dict-safe: database driver may return Row without .get()
        user_email = ""
        if user_result:
            user_email = getattr(user_result, "email", None) or (user_result.get("email", "") if isinstance(user_result, dict) else "") or ""

        from services.gsheets_background_processing_service import GoogleSheetsBackgroundProcessingService
        from services.google_sheets_service import GoogleSheetsService, GoogleSheetsAuthError

        def _row_val(key: str):
            return getattr(row, key, None) if not isinstance(row, dict) else row.get(key)

        connection_id = str(_row_val("google_sheets_connection_id"))
        spreadsheet_name = _row_val("spreadsheet_name") or "Spreadsheet"
        access_token = _row_val("access_token")
        refresh_token = _row_val("refresh_token")
        expires_at = _row_val("expires_at")

        # Preflight: fail fast with a clear message if OAuth is invalid (invalid_grant / expired refresh)
        sheets_service = GoogleSheetsService(
            access_token=access_token or "",
            refresh_token=refresh_token or "",
            expires_at=expires_at,
        )
        try:
            await sheets_service.get_spreadsheet_metadata(spreadsheet_id)
        except GoogleSheetsAuthError:
            raise HTTPException(
                status_code=401,
                detail={
                    "status": "error",
                    "code": "google_reauth_required",
                    "message": "Your Google connection expired or was revoked. Reconnect Google Sheets on the Connections page, then try Re-sync again.",
                },
            )
        except Exception as preflight_err:
            from utils.google_sheets_errors import is_google_sheets_reauth_error

            if is_google_sheets_reauth_error(preflight_err):
                raise HTTPException(
                    status_code=401,
                    detail={
                        "status": "error",
                        "code": "google_reauth_required",
                        "message": "Your Google connection expired or was revoked. Reconnect Google Sheets on the Connections page, then try Re-sync again.",
                    },
                )
            print(f"❌ Spreadsheet sync preflight failed: {preflight_err}")
            raise HTTPException(
                status_code=502,
                detail="Could not read this spreadsheet from Google right now. Check that the file exists and you still have access, then try again.",
            )

        service = GoogleSheetsBackgroundProcessingService()
        asyncio.create_task(
            service.process_spreadsheet_analysis(
                google_sheets_connection_id=connection_id,
                spreadsheet_id=spreadsheet_id,
                spreadsheet_name=spreadsheet_name,
                user_id=str(user_id),
                user_email=user_email,
            )
        )

        return {
            "status": "success",
            "message": "Re-sync started. You will receive an email when the sheet has been re-analyzed.",
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error starting spreadsheet sync: {e}")
        raise HTTPException(status_code=500, detail="Failed to start re-sync")


@router.get("/google-sheets/sheets/{spreadsheet_id}")
async def get_spreadsheet_sheets(
    spreadsheet_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """
    Get all categorized sheets for a specific spreadsheet
    """
    try:
        sheets = await database.fetch_all(
            """
            SELECT 
                ges.id,
                ges.spreadsheet_id,
                ges.spreadsheet_name,
                ges.sheet_name,
                ges.header_schema,
                ges.ai_categorization,
                ges.extracted_at
            FROM gsheets_extracted_schemas ges
            WHERE ges.user_id = :user_id 
              AND ges.spreadsheet_id = :spreadsheet_id
            ORDER BY ges.sheet_name
            """,
            {
                "user_id": str(user_id),
                "spreadsheet_id": spreadsheet_id
            }
        )
        
        if not sheets:
            return {
                "status": "success",
                "spreadsheet_id": spreadsheet_id,
                "sheets": [],
                "message": "No categorized sheets found for this spreadsheet"
            }
        
        from services.labeling.catalog_helpers import fetch_semantics_map, merge_catalog_entry, primary_from_semantics

        conn_row = await database.fetch_one(
            """
            SELECT google_sheets_connection_id::text AS connection_id
            FROM gsheets_extracted_schemas
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
            ORDER BY extracted_at DESC NULLS LAST
            LIMIT 1
            """,
            {"user_id": str(user_id), "spreadsheet_id": spreadsheet_id},
        )
        semantics_map: dict = {}
        if conn_row and conn_row.get("connection_id"):
            semantics_map = await fetch_semantics_map(
                user_id=str(user_id),
                connector_type="google_sheets",
                connector_instance_id=str(conn_row["connection_id"]),
            )

        # Parse and format sheets
        formatted_sheets = []
        for sheet in sheets:
            header_schema = json.loads(sheet['header_schema']) if isinstance(sheet['header_schema'], str) else sheet['header_schema']
            sheet_name = sheet['sheet_name']
            entity_key = f"{sheet['spreadsheet_id']}::{sheet_name}" if sheet.get('spreadsheet_id') and sheet_name else ""
            sem = semantics_map.get(entity_key) if entity_key else None
            primary = primary_from_semantics(sem)
            semantics_payload = sem if isinstance(sem, dict) else {}
            legacy_shape = merge_catalog_entry(
                {
                    "document_type": primary.get("category") or "Spreadsheet",
                    "document_description": primary.get("description") or f"Sheet {sheet_name}",
                },
                semantics=sem,
                category_key="document_type",
                description_key="document_description",
            )
            
            formatted_sheets.append({
                "id": str(sheet['id']),
                "spreadsheet_id": sheet['spreadsheet_id'],
                "spreadsheet_name": sheet['spreadsheet_name'],
                "sheet_name": sheet_name,
                "header_schema": header_schema,
                "semantics": semantics_payload,
                "document_type": legacy_shape.get("document_type"),
                "document_description": legacy_shape.get("document_description"),
                "extracted_at": sheet['extracted_at'].isoformat() if sheet['extracted_at'] else None
            })
        
        return {
            "status": "success",
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": formatted_sheets[0]['spreadsheet_name'] if formatted_sheets else None,
            "sheets": formatted_sheets,
            "total_sheets": len(formatted_sheets)
        }
        
    except Exception as e:
        print(f"Error fetching spreadsheet sheets: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch spreadsheet sheets")
