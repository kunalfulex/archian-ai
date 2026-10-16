"""Execute Google Sheets operations (v2)."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional

from services.google_sheets.context_resolver import enrich_context, resolve_spreadsheet_context
from services.google_sheets.models import OperationPlan, OperationType, SpreadsheetContext
from services.google_sheets.pending_store import (
    clear_pending,
    load_pending,
    parse_confirmation_action,
    save_pending,
)
from services.google_sheets.response_summary import (
    build_duplicate_confirmation,
    build_error_response,
    build_formula_completion,
    build_inline_preview_payload,
    build_search_completion,
    build_single_row_success,
    build_upsert_completion_chunks,
    inline_data_preview_sse,
)
from services.google_sheets.row_builder import infer_key_columns
from services.google_sheets.search_service import search_spreadsheet
from services.google_sheets.upsert_engine import UpsertEngine
from services.google_sheets_service import GoogleSheetsService
from services.entitlements import get_entitlements
from utils.google_sheets_auth import get_valid_google_sheets_token


async def _connection_required_stream():
    async def generate():
        for chunk in build_error_response(
            "Google Sheets not connected",
            "Connect your Google account on the **Connections** page, then try again.",
            hints=["Open Connections → Google Sheets → Connect"],
        ):
            yield chunk

    return generate()


async def stream_search(
    ctx: SpreadsheetContext,
    sheets_service: GoogleSheetsService,
    prompt: str,
    user_id: str,
    *,
    conversation_id: Optional[str] = None,
    classification: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    search_result = await search_spreadsheet(sheets_service, ctx, prompt)
    if not search_result.get("success"):
        for chunk in build_error_response(
            "Search failed",
            str(search_result.get("error", "Unknown error")),
            hints=["Check the workbook name and tab", "Try searching by email or exact column value"],
        ):
            yield chunk
        return

    matching = search_result.get("matching_records", [])
    criteria = search_result.get("search_criteria", {})
    chunks, _sse_line = build_search_completion(ctx, matching, criteria=criteria)
    if matching:
        from services.google_sheets.extraction_persist import (
            persist_gsheets_operation_as_extraction,
        )

        _, finished_sse = await persist_gsheets_operation_as_extraction(
            user_id=user_id,
            conversation_id=conversation_id,
            classification=classification,
            ctx=ctx,
            rows=matching,
            extraction_goals=prompt,
            document_type="Search Results",
            document_description=f"Search in {ctx.spreadsheet_name}",
            extraction_metadata={
                "operation": "gsheets_search",
                "search_criteria": criteria,
            },
            operation_summary="".join(chunks),
        )
        yield finished_sse
    else:
        for chunk in chunks:
            yield chunk


async def stream_upsert(
    prompt: str,
    user_id: str,
    ctx: SpreadsheetContext,
    sheets_service: GoogleSheetsService,
    conversation_history: Optional[list] = None,
) -> AsyncGenerator[str, None]:
    from handlers.google_sheets.ai_prompts import (
        extract_data_from_prompt_with_ai,
        find_potential_duplicates_with_ai,
    )

    headers = ctx.headers
    if not headers:
        for chunk in build_error_response(
            "Headers not available",
            f"Could not read column headers for **{ctx.spreadsheet_name}**.",
            hints=[
                f"`Give me information about {ctx.spreadsheet_name}`",
                "Re-sync the spreadsheet on the Connections page",
            ],
        ):
            yield chunk
        return

    extraction = await extract_data_from_prompt_with_ai(
        prompt, conversation_history, headers=headers
    )
    new_data = extraction.get("data", {})
    is_bulk = extraction.get("is_bulk", False)
    record_count = extraction.get("record_count", 0)

    if is_bulk and isinstance(new_data, list):
        records = new_data
    elif isinstance(new_data, dict) and new_data:
        records = [new_data]
    else:
        for chunk in build_error_response(
            "Could not parse row data",
            "I couldn't find field values in your message. Use `Column=Value` pairs or a small table.",
            hints=["Example: `Add Email=john@example.com, First Name=John to Employee list`"],
        ):
            yield chunk
        return

    if not records or record_count == 0:
        for chunk in build_error_response(
            "Could not parse row data",
            "No rows were extracted from your message.",
        ):
            yield chunk
        return

    is_valid, message, _plan = await get_entitlements().validate_migration(
        user_id, len(records)
    )
    if not is_valid:
        for chunk in build_error_response("Plan limit reached", message):
            yield chunk
        return

    key_columns = ctx.key_columns or infer_key_columns(headers)

    if key_columns and not is_bulk:
        engine = UpsertEngine()
        result = await engine.upsert_records(
            sheets_service,
            ctx.spreadsheet_id,
            ctx.sheet_name or "Sheet1",
            records,
            headers,
            key_columns,
        )
        await get_entitlements().track_successful_migration(user_id, result.processed_records)
        for chunk in build_upsert_completion_chunks(
            ctx, result, records, headers, key_columns
        ):
            yield chunk
        if len(records) > 1:
            yield inline_data_preview_sse(
                build_inline_preview_payload(
                    ctx,
                    records,
                    preview_message=(
                        f"✅ **Rows updated in {ctx.spreadsheet_name}**\n\n"
                        f"**{result.processed_records}** record(s) on `{ctx.sheet_name}`."
                    ),
                    document_type="Sheet Data",
                )
            )
        return

    if is_bulk and len(records) > 1:
        engine = UpsertEngine()
        result = await engine.upsert_records(
            sheets_service,
            ctx.spreadsheet_id,
            ctx.sheet_name or "Sheet1",
            records,
            headers,
            key_columns,
        )
        await get_entitlements().track_successful_migration(user_id, result.processed_records)
        for chunk in build_upsert_completion_chunks(
            ctx, result, records, headers, key_columns
        ):
            yield chunk
        yield inline_data_preview_sse(
            build_inline_preview_payload(
                ctx,
                records,
                preview_message=(
                    f"✅ **Data written to {ctx.spreadsheet_name}**\n\n"
                    f"**{result.processed_records}** record(s) on `{ctx.sheet_name}`."
                ),
                document_type="Sheet Data",
            )
        )
        return

    record = records[0]
    read_result = await sheets_service.read_spreadsheet(
        ctx.spreadsheet_id,
        range_name=f"{ctx.sheet_name}!A1:ZZ2000",
        sheet_name=ctx.sheet_name,
    )
    if not read_result.get("success"):
        for chunk in build_error_response(
            "Could not read spreadsheet",
            str(read_result.get("error", "Unknown error")),
        ):
            yield chunk
        return

    existing_data = read_result.get("data", [])
    headers_from_api = read_result.get("headers", headers)

    if not existing_data:
        row = [record.get(h, "") for h in headers_from_api]
        append_result = await sheets_service.append_to_spreadsheet(
            ctx.spreadsheet_id, [row], ctx.sheet_name
        )
        if append_result.get("success"):
            await get_entitlements().track_successful_migration(user_id, 1)
            for chunk in build_single_row_success(
                ctx, record, headers_from_api, action="added"
            ):
                yield chunk
        else:
            for chunk in build_error_response(
                "Could not add row",
                str(append_result.get("error", "Unknown error")),
            ):
                yield chunk
        return

    records_with_indices = [
        {"row_index": idx + 2, "data": row} for idx, row in enumerate(existing_data)
    ]
    duplicates = await find_potential_duplicates_with_ai(
        new_data=record,
        existing_records=records_with_indices,
        headers=headers_from_api,
    )

    if not duplicates:
        engine = UpsertEngine()
        await engine.upsert_records(
            sheets_service,
            ctx.spreadsheet_id,
            ctx.sheet_name or "Sheet1",
            [record],
            headers_from_api,
            key_columns or infer_key_columns(headers_from_api),
        )
        await get_entitlements().track_successful_migration(user_id, 1)
        for chunk in build_single_row_success(ctx, record, headers_from_api, action="added"):
            yield chunk
        return

    if len(duplicates) == 1:
        dup = duplicates[0]
        dup_record = dup.get("matched_record", {})
        if isinstance(dup_record, dict) and "data" in dup_record:
            dup_record = dup_record["data"]
        await save_pending(
            user_id,
            {
                "spreadsheet_id": ctx.spreadsheet_id,
                "spreadsheet_name": ctx.spreadsheet_name,
                "sheet_name": ctx.sheet_name,
                "new_data": record,
                "headers": headers_from_api,
                "duplicate_row_index": dup.get("row_index"),
                "duplicate_record": dup_record,
                "all_duplicates": [],
            },
        )
        for chunk in build_duplicate_confirmation(ctx, 1):
            yield chunk
        return

    await save_pending(
        user_id,
        {
            "spreadsheet_id": ctx.spreadsheet_id,
            "spreadsheet_name": ctx.spreadsheet_name,
            "sheet_name": ctx.sheet_name,
            "new_data": record,
            "headers": headers_from_api,
            "all_duplicates": duplicates,
            "duplicate_row_index": None,
            "duplicate_record": {},
        },
    )
    for chunk in build_duplicate_confirmation(ctx, len(duplicates)):
        yield chunk


def _tab_title_from_metadata(tab: Dict[str, Any]) -> str:
    """Google API metadata uses sheet_name in our service; other callers may use title/name."""
    return (
        tab.get("sheet_name") or tab.get("title") or tab.get("name") or ""
    ).strip()


async def stream_pending_confirmation(
    prompt: str,
    user_id: str,
    pending: Dict[str, Any],
    action_info: Dict[str, Any],
) -> AsyncGenerator[str, None]:
    from handlers.google_sheets.pending import handle_update_confirmation_response

    from agent.stream_utils import iter_legacy_handler_stream

    stream = await handle_update_confirmation_response(user_id, prompt, None)
    if stream:
        async for chunk in iter_legacy_handler_stream(stream):
            yield chunk
        await clear_pending(user_id)


async def execute_plan_stream(
    plan: OperationPlan,
    prompt: str,
    user_id: str,
    conversation_history: Optional[list] = None,
    classification: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    if plan.operation == OperationType.UNSUPPORTED:
        yield plan.unsupported_reason or "This operation is not supported."
        yield "\n\n"
        return

    conn = await get_valid_google_sheets_token(user_id)
    if not conn:
        from agent.stream_utils import iter_legacy_handler_stream

        async for c in iter_legacy_handler_stream(await _connection_required_stream()):
            yield c
        return

    pending = await load_pending(user_id)
    if pending:
        action_info = parse_confirmation_action(prompt)
        if action_info.get("has_pending"):
            async for chunk in stream_pending_confirmation(prompt, user_id, pending, action_info):
                yield chunk
            return

    classification = classification or {}
    ctx = await resolve_spreadsheet_context(
        prompt,
        user_id,
        conversation_history,
        source_name=classification.get("source_name"),
        destination_name=classification.get("destination_name"),
        source_sheet_name=classification.get("source_sheet_name"),
        destination_sheet_name=classification.get("destination_sheet_name"),
    )
    if not ctx.found:
        for chunk in build_error_response(
            "Spreadsheet not found",
            ctx.message or "Could not determine which workbook you mean.",
            hints=["Name the workbook: `… in Employee list`", "Paste the Google Sheets URL"],
        ):
            yield chunk
        return

    if ctx.error == "ambiguous_spreadsheet_name":
        hints = []
        for c in ctx.candidates or []:
            email = c.get("google_account_email") or c.get("connection_label")
            name = c.get("spreadsheet_name") or ctx.spreadsheet_name
            if email and name:
                hints.append(f"`{name} in {email}`")
        for chunk in build_error_response(
            "Multiple workbooks with the same name",
            ctx.message or "Please specify which Google account owns this workbook.",
            hints=hints or ["Include the Google account email in your request"],
        ):
            yield chunk
        return

    if ctx.error in ("not_created_by_edison", "shared_file_limitation"):
        for chunk in build_error_response(
            "Spreadsheet not accessible",
            ctx.message
            or "Connect or create this workbook through Archian AI first (Google sharing limits apply).",
            hints=["Create a new sheet via chat", "Share the file with your connected Google account"],
        ):
            yield chunk
        return

    if ctx.spreadsheet_id:
        from services.google_sheets_connection_service import resolve_connection_for_spreadsheet

        resolved = await resolve_connection_for_spreadsheet(user_id, ctx.spreadsheet_id)
        if resolved:
            conn = resolved

    ctx = await enrich_context(ctx, user_id, conn)
    sheets_service = GoogleSheetsService(
        access_token=conn["access_token"],
        refresh_token=conn.get("refresh_token"),
        expires_at=conn.get("expires_at"),
    )

    if plan.operation == OperationType.SEARCH:
        async for chunk in stream_search(ctx, sheets_service, prompt):
            yield chunk
        return

    if plan.operation == OperationType.UPSERT:
        async for chunk in stream_upsert(prompt, user_id, ctx, sheets_service, conversation_history):
            yield chunk
        return

    if plan.operation == OperationType.FORMULA:
        async for chunk in stream_formula(prompt, user_id, ctx, sheets_service):
            yield chunk
        return

    yield f"**Operation `{plan.operation.value}`** is handled by the standard flow.\n\n"


async def stream_formula(
    prompt: str,
    user_id: str,
    ctx: SpreadsheetContext,
    sheets_service: GoogleSheetsService,
) -> AsyncGenerator[str, None]:
    from services.google_sheets.context_cache import invalidate_context
    from services.google_sheets.formula_service import (
        apply_formula_spec,
        generate_formula_spec,
        try_build_simple_vlookup,
    )

    try:
        spec = await try_build_simple_vlookup(prompt, ctx, sheets_service)
        if not spec:
            spec = await generate_formula_spec(prompt, ctx, sheets_service)
    except ValueError as e:
        for chunk in build_error_response(
            "Could not build formula",
            str(e),
            hints=[
                "`Add VLOOKUP from Sheet2 into column D using Email`",
                "Paste the exact formula you want in a code block",
            ],
        ):
            yield chunk
        return
    except Exception as e:
        for chunk in build_error_response("Formula generation failed", str(e)):
            yield chunk
        return

    try:
        result = await apply_formula_spec(sheets_service, ctx.spreadsheet_id, spec)
    except Exception as e:
        for chunk in build_error_response("Failed to write formula", str(e)):
            yield chunk
        return

    await invalidate_context(user_id, ctx.spreadsheet_id, spec.target_sheet)

    for chunk in build_formula_completion(
        ctx,
        formula_type=spec.formula_type,
        cell_range=str(result.get("range", "")),
        formula=spec.formula,
        explanation=spec.explanation,
        rows_written=result.get("rows_written") if spec.fill_rows and spec.fill_rows > 1 else None,
    ):
        yield chunk
