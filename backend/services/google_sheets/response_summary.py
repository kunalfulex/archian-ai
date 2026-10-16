"""Streamlined user-facing chat responses for Google Sheets operations."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.google_sheets.models import SpreadsheetContext, UpsertResult

_PREVIEW_MAX = 5
_DISPLAY_COL_MAX = 4
_INLINE_PREVIEW_ROW_CAP = 500

_PRIORITY_FIELD_HINTS = (
    "name",
    "full name",
    "first name",
    "last name",
    "email",
    "account",
    "company",
    "phone",
    "title",
)


def json_safe_records(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    safe: List[Dict[str, Any]] = []
    for row in rows:
        safe.append(
            {
                k: (
                    ""
                    if v is None
                    else v
                    if isinstance(v, (str, int, float, bool))
                    else str(v)
                )
                for k, v in row.items()
            }
        )
    return safe


def inline_data_preview_sse(payload: Dict[str, Any]) -> str:
    """SSE line for InlineDataPreview (SQL extract, Sheets search/join, etc.)."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def spreadsheet_edit_url(ctx: SpreadsheetContext) -> Optional[str]:
    if ctx.spreadsheet_id:
        return f"https://docs.google.com/spreadsheets/d/{ctx.spreadsheet_id}/edit"
    return None


def _filled_columns(records: Sequence[Dict[str, Any]], headers: Sequence[str]) -> List[str]:
    filled: List[str] = []
    for header in headers:
        if any(str(record.get(header, "")).strip() for record in records):
            filled.append(header)
    return filled


def _display_columns(
    records: Sequence[Dict[str, Any]],
    headers: Sequence[str],
    key_columns: Optional[Sequence[str]] = None,
) -> List[str]:
    chosen: List[str] = []
    header_list = list(headers)

    for hint in _PRIORITY_FIELD_HINTS:
        for header in header_list:
            hl = header.lower()
            if hint in hl and header not in chosen:
                chosen.append(header)
                break

    for key in key_columns or []:
        if key in header_list and key not in chosen:
            chosen.append(key)

    for header in header_list:
        if header in chosen:
            continue
        if any(str(record.get(header, "")).strip() for record in records):
            chosen.append(header)
        if len(chosen) >= _DISPLAY_COL_MAX:
            break

    return chosen[:_DISPLAY_COL_MAX]


def _format_preview_line(record: Dict[str, Any], columns: Sequence[str]) -> str:
    parts: List[str] = []
    for column in columns:
        value = record.get(column)
        if value is not None and str(value).strip():
            parts.append(str(value).strip())
    return " · ".join(parts) if parts else "(empty row)"


def build_workbook_context_lines(
    ctx: SpreadsheetContext,
    *,
    account_email: Optional[str] = None,
    extra: Optional[List[str]] = None,
) -> List[str]:
    workbook = ctx.spreadsheet_name or "spreadsheet"
    tab = ctx.sheet_name or "Sheet1"
    lines = [
        f"- **Workbook:** {workbook}\n",
        f"- **Tab:** {tab}\n",
    ]
    if account_email:
        lines.append(f"- **Account:** {account_email}\n")
    if extra:
        lines.extend(extra)
    return lines


def build_open_sheet_link(ctx: SpreadsheetContext) -> List[str]:
    url = spreadsheet_edit_url(ctx)
    if not url:
        return []
    name = ctx.spreadsheet_name or "spreadsheet"
    return [f"\n[Open **{name}** in Google Sheets]({url})\n"]


def build_next_steps_section(items: Sequence[str]) -> List[str]:
    if not items:
        return []
    lines = ["\n**Suggested next steps**\n"]
    for item in items:
        lines.append(f"- {item}\n")
    lines.append("\n")
    return lines


def build_error_response(
    title: str,
    message: str,
    *,
    hints: Optional[Sequence[str]] = None,
) -> List[str]:
    chunks = [f"### ⚠️ {title}\n\n", f"{message.strip()}\n\n"]
    if hints:
        chunks.append("**Try this**\n")
        for hint in hints:
            chunks.append(f"- {hint}\n")
        chunks.append("\n")
    return chunks


def build_inline_preview_payload(
    ctx: SpreadsheetContext,
    rows: Sequence[Dict[str, Any]],
    *,
    preview_message: str,
    document_type: str = "Google Sheets Data",
    document_description: Optional[str] = None,
    table_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers = list(ctx.headers) if ctx.headers else (list(rows[0].keys()) if rows else [])
    safe_rows = json_safe_records(list(rows)[:_INLINE_PREVIEW_ROW_CAP])
    payload: Dict[str, Any] = {
        "status": "success",
        "data": safe_rows,
        "total_records": len(rows),
        "source_type": "google_sheets",
        "source_name": ctx.spreadsheet_name,
        "table_name": table_name or ctx.sheet_name,
        "document_type": document_type,
        "document_description": document_description or ctx.sheet_name,
        "preview_message": preview_message,
    }
    if extra:
        payload.update(extra)
    return payload


def _upsert_action_lines(result: UpsertResult) -> List[str]:
    inserted = result.rows_inserted
    updated = result.rows_updated
    lines: List[str] = []

    if inserted and updated:
        lines.append(f"- **{inserted}** new row(s) added")
        lines.append(f"- **{updated}** existing row(s) updated")
    elif inserted:
        lines.append(f"- **{inserted}** new row(s) added")
    elif updated:
        lines.append(f"- **{updated}** existing row(s) updated")
    else:
        lines.append("- No rows were changed")

    total = result.processed_records or (inserted + updated)
    if total and (inserted + updated) != total:
        lines.append(f"- **{total}** record(s) processed in total")

    return lines


def build_upsert_completion_chunks(
    ctx: SpreadsheetContext,
    result: UpsertResult,
    records: Sequence[Dict[str, Any]],
    headers: Sequence[str],
    key_columns: Optional[Sequence[str]] = None,
    *,
    preview_count: int = _PREVIEW_MAX,
) -> List[str]:
    workbook = ctx.spreadsheet_name or "spreadsheet"
    chunks: List[str] = [
        f"### ✅ Data written to **{workbook}**\n\n",
        *build_workbook_context_lines(
            ctx,
            extra=(
                [f"- **Match keys:** {', '.join(key_columns)}\n"]
                if key_columns
                else None
            ),
        ),
        "\n**What happened**\n",
    ]
    chunks.extend(f"{line}\n" for line in _upsert_action_lines(result))

    filled = _filled_columns(records, headers)
    if filled:
        shown = filled[:12]
        suffix = f" (+{len(filled) - 12} more)" if len(filled) > 12 else ""
        chunks.append(f"\n**Columns updated:** {', '.join(shown)}{suffix}\n")

    chunks.extend(build_open_sheet_link(ctx))

    if records and len(records) <= preview_count:
        cols = _display_columns(records, headers, key_columns)
        chunks.append(f"\n**Preview** ({len(records)} row(s)):\n\n")
        for index, record in enumerate(records, 1):
            chunks.append(f"{index}. {_format_preview_line(record, cols)}\n")
    elif records:
        chunks.append(
            f"\n_Showing a short text preview; use the table below for all "
            f"{len(records)} row(s)._\n"
        )

    chunks.append("\n")
    return chunks


def build_search_completion(
    ctx: SpreadsheetContext,
    matching: Sequence[Dict[str, Any]],
    *,
    criteria: Optional[Dict[str, Any]] = None,
    use_table_preview: bool = True,
) -> Tuple[List[str], Optional[str]]:
    """Markdown chunks plus optional SSE line for InlineDataPreview."""
    count = len(matching)
    workbook = ctx.spreadsheet_name or "spreadsheet"

    if count == 0:
        chunks = [
            f"### 🔍 No matches in **{workbook}**\n\n",
            *build_workbook_context_lines(ctx),
            "\n**Result**\n",
            "- No rows matched your search.\n",
        ]
        if criteria and criteria.get("method"):
            chunks.append(f"- Search method: {criteria.get('method')}\n")
        chunks.extend(
            build_next_steps_section(
                [
                    "Broaden your criteria (e.g. search by email or partial name)",
                    f"Run `Give me information about {workbook}` to see columns",
                ]
            )
        )
        return chunks, None

    chunks = [
        f"### 🔍 Found **{count}** match{'es' if count != 1 else ''}\n\n",
        *build_workbook_context_lines(ctx),
        "\n**Result**\n",
        f"- **{count}** row(s) matched your query\n",
    ]
    if criteria and criteria.get("method"):
        chunks.append(f"- Method: {criteria.get('method')}\n")

    sse_line: Optional[str] = None
    if use_table_preview and count > 0:
        preview_n = min(_PREVIEW_MAX, count)
        headers = list(ctx.headers) if ctx.headers else list(matching[0].keys())
        cols = _display_columns(matching, headers)
        suffix = " in the data preview below" if count > preview_n else ""
        chunks.append(f"\n**Preview** ({preview_n} of {count}):\n\n")
        for index, record in enumerate(matching[:preview_n], 1):
            chunks.append(f"{index}. {_format_preview_line(record, cols)}\n")
        if count > preview_n:
            chunks.append(f"\n_…and {count - preview_n} more{suffix}._\n")

    chunks.extend(build_open_sheet_link(ctx))
    chunks.append("\n")
    return chunks, sse_line


def build_join_completion(
    ctx: SpreadsheetContext,
    left_tab: str,
    right_tab: str,
    join_key: str,
    *,
    joined_count: int,
    left_count: int,
    right_count: int,
    rows: Sequence[Dict[str, Any]],
    wrote_to_tab: Optional[str] = None,
    right_workbook_name: Optional[str] = None,
) -> Tuple[List[str], Optional[str]]:
    workbook = ctx.spreadsheet_name or "spreadsheet"
    extra = [f"- **Join key:** {join_key}\n"]
    if right_workbook_name and right_workbook_name.strip() != workbook.strip():
        extra = [
            f"- **Left:** {workbook} (`{left_tab}`)\n",
            f"- **Right:** {right_workbook_name} (`{right_tab}`)\n",
            *extra,
        ]
    else:
        extra = [
            f"- **Left tab:** {left_tab}\n",
            f"- **Right tab:** {right_tab}\n",
            *extra,
        ]
    chunks = [
        f"### ✅ Join completed — **{workbook}**\n\n",
        *build_workbook_context_lines(ctx, extra=extra),
        "\n**What happened**\n",
        f"- Merged **{joined_count}** row(s) (left: {left_count}, right: {right_count})\n",
        "- Result saved as an extraction (map, filter, or migrate from the preview)\n",
    ]
    if wrote_to_tab:
        chunks.append(f"- Results written to tab **`{wrote_to_tab}`**\n")

    chunks.extend(build_open_sheet_link(ctx))

    if not wrote_to_tab and rows:
        chunks.extend(
            build_next_steps_section(
                [
                    "`Write join result to tab Joined` to save in the workbook",
                    "Say `Migrate this joined data to Salesforce` to push elsewhere",
                ]
            )
        )
    chunks.append("\n")
    return chunks, None


def build_formula_completion(
    ctx: SpreadsheetContext,
    *,
    formula_type: str,
    cell_range: str,
    formula: str,
    explanation: Optional[str] = None,
    rows_written: Optional[int] = None,
) -> List[str]:
    workbook = ctx.spreadsheet_name or "spreadsheet"
    chunks = [
        f"### ✅ Formula applied — **{workbook}**\n\n",
        *build_workbook_context_lines(ctx),
        "\n**What happened**\n",
        f"- **{formula_type}** written to `{cell_range}`\n",
    ]
    if rows_written and rows_written > 1:
        chunks.append(f"- Filled down **{rows_written}** rows\n")
    if explanation:
        chunks.append(f"\n{explanation.strip()}\n")
    chunks.append(f"\n```\n{formula.strip()}\n```\n")
    chunks.extend(build_open_sheet_link(ctx))
    chunks.extend(
        build_next_steps_section(
            [
                "Open the sheet in Google Sheets to see live recalculated values",
                "For static merged values (no formula), ask to **join** two tabs instead",
            ]
        )
    )
    return chunks


def build_single_row_success(
    ctx: SpreadsheetContext,
    record: Dict[str, Any],
    headers: Sequence[str],
    *,
    action: str = "added",
) -> List[str]:
    workbook = ctx.spreadsheet_name or "spreadsheet"
    cols = _display_columns([record], headers)
    line = _format_preview_line(record, cols)
    chunks = [
        f"### ✅ Row {action} — **{workbook}**\n\n",
        *build_workbook_context_lines(ctx),
        "\n**What happened**\n",
        f"- 1 row {action} on this tab\n",
        f"- {line}\n",
    ]
    chunks.extend(build_open_sheet_link(ctx))
    chunks.append("\n")
    return chunks


def build_duplicate_confirmation(
    ctx: SpreadsheetContext,
    duplicate_count: int,
) -> List[str]:
    workbook = ctx.spreadsheet_name or "spreadsheet"
    chunks = [
        f"### ⚠️ Possible duplicate — **{workbook}**\n\n",
        *build_workbook_context_lines(ctx),
        "\n**What I need from you**\n",
    ]
    if duplicate_count == 1:
        chunks.append(
            "- I found a row that may already exist.\n"
            "- Reply **`Update`** to overwrite, **`Add New`** to insert anyway, or **`Cancel`**.\n"
        )
    else:
        chunks.append(
            f"- I found **{duplicate_count}** possible matches.\n"
            "- Reply with the **row number**, **`Add New`**, or **`Cancel`**.\n"
        )
    chunks.append("\n")
    return chunks


def build_describe_completion(
    spreadsheet_name: str,
    sheet_name: str,
    *,
    account_email: Optional[str] = None,
    headers: Optional[Sequence[str]] = None,
    record_count: Optional[int] = None,
    column_types: Optional[Dict[str, str]] = None,
    document_type: Optional[str] = None,
    document_description: Optional[str] = None,
) -> List[str]:
    headers = list(headers or [])
    chunks = [
        f"### 📊 **{spreadsheet_name}** overview\n\n",
        f"- **Workbook:** {spreadsheet_name}\n",
        f"- **Tab:** {sheet_name or 'Sheet1'}\n",
    ]
    if account_email:
        chunks.append(f"- **Account:** {account_email}\n")

    if headers:
        col_preview = ", ".join(headers[:8])
        suffix = f" (+{len(headers) - 8} more)" if len(headers) > 8 else ""
        chunks.append(f"\n**Structure**\n")
        chunks.append(f"- **{len(headers)}** columns: {col_preview}{suffix}\n")

    if record_count is not None:
        chunks.append(f"\n**Data**\n")
        chunks.append(f"- **{record_count}** row(s) with values\n")

    if column_types:
        chunks.append(f"\n**Column types**\n")
        for col in headers[:10]:
            dtype = column_types.get(col, "Text")
            chunks.append(f"- **{col}:** {dtype}\n")
        if len(headers) > 10:
            chunks.append(f"- _…and {len(headers) - 10} more columns_\n")

    if document_type:
        chunks.append(f"\n**Classification**\n")
        chunks.append(f"- **Type:** {document_type}\n")
        if document_description:
            chunks.append(f"- {document_description}\n")

    chunks.extend(
        build_next_steps_section(
            [
                f"`Extract data from {spreadsheet_name}`",
                f"`Add a row to {spreadsheet_name}`",
                f"`Migrate {spreadsheet_name} to Salesforce`",
            ]
        )
    )
    return chunks
