"""
Generate and write Google Sheets formulas (VLOOKUP, XLOOKUP, QUERY, ARRAYFORMULA, INDEX/MATCH).

Uses USER_ENTERED so formulas are interpreted by Sheets — not the in-memory join path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from services.google_sheets.models import SpreadsheetContext
from services.google_sheets_service import GoogleSheetsService


@dataclass
class FormulaSpec:
    formula_type: str
    target_sheet: str
    start_cell: str
    formula: str
    fill_rows: int = 0
    column_header: Optional[str] = None
    explanation: str = ""


def _col_letter_to_index(col: str) -> int:
    col = col.upper()
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _index_to_col_letter(index: int) -> str:
    index += 1
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or "A"


def _parse_cell(cell: str) -> Tuple[str, int]:
    m = re.match(r"^([A-Za-z]+)(\d+)$", (cell or "A1").strip())
    if not m:
        return "A", 2
    return m.group(1).upper(), int(m.group(2))


def expand_formula_down(formula: str, start_cell: str, fill_rows: int) -> List[List[str]]:
    """Duplicate formula with row references incremented (e.g. A2 -> A3)."""
    if fill_rows <= 1:
        return [[formula]]
    col, start_row = _parse_cell(start_cell)
    out: List[List[str]] = []
    for i in range(fill_rows):
        row_num = start_row + i

        def repl(m: re.Match) -> str:
            ref_col = m.group(1).upper()
            return f"{ref_col}{row_num}"

        row_formula = re.sub(r"([A-Z]{1,3})\d+\b", repl, formula, count=0)
        if row_formula == formula and i > 0:
            row_formula = re.sub(
                rf"\b{col}{start_row}\b",
                f"{col}{row_num}",
                formula,
                flags=re.I,
            )
        out.append([row_formula])
    return out


def _quote_sheet_name(name: str) -> str:
    if not name:
        return "Sheet1"
    if re.search(r"[^\w]", name):
        return f"'{name.replace(chr(39), chr(39) * 2)}'"
    return name


async def _sheet_catalog(
    sheets_service: GoogleSheetsService, spreadsheet_id: str
) -> List[Dict[str, str]]:
    tabs = await sheets_service.get_all_sheet_names(spreadsheet_id)
    out = []
    for t in tabs or []:
        title = (t.get("sheet_name") or t.get("title") or t.get("name") or "").strip()
        if title:
            out.append({"title": title})
    return out


async def generate_formula_spec(
    prompt: str,
    ctx: SpreadsheetContext,
    sheets_service: GoogleSheetsService,
) -> FormulaSpec:
    tabs = await _sheet_catalog(sheets_service, ctx.spreadsheet_id)
    tab_names = [t["title"] for t in tabs]
    headers = ctx.headers or []

    ai_prompt = f"""
You are a Google Sheets formula expert. The user wants a formula written INTO the workbook (live formula, not static values).

**Workbook:** {ctx.spreadsheet_name}
**Active tab:** {ctx.sheet_name}
**Columns on active tab:** {", ".join(headers) if headers else "unknown"}
**All tabs:** {", ".join(tab_names)}

**User request:**
"{prompt}"

Return JSON only:
{{
  "formula_type": "VLOOKUP" | "XLOOKUP" | "QUERY" | "ARRAYFORMULA" | "INDEX_MATCH" | "OTHER",
  "target_sheet": "tab name where formula goes (usually active tab)",
  "start_cell": "top-left cell for formula e.g. D2",
  "formula": "full formula starting with =, use Sheet!A:Z syntax with quoted sheet names if spaces",
  "fill_rows": integer 0-2000, number of rows to fill down (0 = single cell only),
  "column_header": "optional header label for new column e.g. in row 1",
  "explanation": "one sentence for the user"
}}

Rules:
- formula MUST start with =
- For VLOOKUP use exact match FALSE unless user asks otherwise
- Reference other tabs as 'Tab Name'!A:D
- ARRAYFORMULA may wrap ranges e.g. =ARRAYFORMULA(IF(A2:A500="",,VLOOKUP(A2:A500,'Other'!A:C,2,FALSE)))
- QUERY must use QUERY('Tab'!A:Z, "select ...", 1) style
- fill_rows: estimate from "all rows" as 500 if unknown, else 0 for single cell
"""

    from openai import OpenAI
    import os
    from services.architect_agent import async_openai_call
    from utils.openai_helpers import parse_openai_json_response

    from services.llm import tasks as llm_tasks

    response = await async_openai_call(
        None,
        task=llm_tasks.GOOGLE_SHEETS_FORMULA,
        messages=[{"role": "user", "content": ai_prompt}],
        max_tokens=1200,
        temperature=0.1,
    )
    raw = response.choices[0].message.content.strip()
    try:
        data = parse_openai_json_response(raw)
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"Could not parse formula plan: {e}") from e

    formula = (data.get("formula") or "").strip()
    if not formula.startswith("="):
        formula = "=" + formula.lstrip("=")

    fill_rows = int(data.get("fill_rows") or 0)
    fill_rows = max(0, min(fill_rows, 2000))

    return FormulaSpec(
        formula_type=(data.get("formula_type") or "OTHER").upper(),
        target_sheet=data.get("target_sheet") or ctx.sheet_name or "Sheet1",
        start_cell=data.get("start_cell") or "A2",
        formula=formula,
        fill_rows=fill_rows,
        column_header=data.get("column_header"),
        explanation=data.get("explanation") or "",
    )


async def apply_formula_spec(
    sheets_service: GoogleSheetsService,
    spreadsheet_id: str,
    spec: FormulaSpec,
) -> Dict[str, Any]:
    sheet = _quote_sheet_name(spec.target_sheet)
    col, start_row = _parse_cell(spec.start_cell)

    if spec.column_header and start_row > 1:
        header_cell = f"{sheet}!{col}1"
        await sheets_service.write_data(
            spreadsheet_id,
            header_cell,
            [[spec.column_header]],
        )

    if spec.fill_rows and spec.fill_rows > 1:
        values = expand_formula_down(spec.formula, spec.start_cell, spec.fill_rows)
        end_row = start_row + spec.fill_rows - 1
        range_name = f"{sheet}!{col}{start_row}:{col}{end_row}"
    else:
        values = [[spec.formula]]
        range_name = f"{sheet}!{spec.start_cell}"

    result = await sheets_service.write_data(spreadsheet_id, range_name, values)
    return {
        "success": True,
        "range": range_name,
        "formula_type": spec.formula_type,
        "rows_written": len(values),
        "api_result": result,
    }


async def try_build_simple_vlookup(
    prompt: str,
    ctx: SpreadsheetContext,
    sheets_service: GoogleSheetsService,
) -> Optional[FormulaSpec]:
    """
    Fast path: "vlookup X from Tab into column D" without LLM when pattern is clear.
    """
    pl = prompt.lower()
    if "vlookup" not in pl and "xlookup" not in pl:
        return None

    tabs = await _sheet_catalog(sheets_service, ctx.spreadsheet_id)
    lookup_tab = None
    for t in tabs:
        title = t["title"]
        if title.lower() != (ctx.sheet_name or "").lower() and title.lower() in pl:
            lookup_tab = title
            break
    if not lookup_tab and len(tabs) == 2:
        for t in tabs:
            if t["title"] != ctx.sheet_name:
                lookup_tab = t["title"]
                break
    if not lookup_tab:
        return None

    col_match = re.search(r"\bcolumn\s+([a-z])\b", pl, re.I) or re.search(
        r"\binto\s+([a-z])\d*\b", pl, re.I
    )
    target_col = (col_match.group(1) if col_match else "D").upper()

    key_col = "A"
    for h in ctx.headers or []:
        if any(k in h.lower() for k in ("email", "id", "name", "key")):
            key_col = _index_to_col_letter(ctx.headers.index(h))
            break

    lookup_ref = f"{_quote_sheet_name(lookup_tab)}!A:Z"
    fn = "XLOOKUP" if "xlookup" in pl else "VLOOKUP"
    if fn == "VLOOKUP":
        formula = f"=VLOOKUP({key_col}2, {lookup_ref}, 2, FALSE)"
    else:
        formula = f"=XLOOKUP({key_col}2, {lookup_ref}, 2)"

    fill_rows = 500 if any(w in pl for w in ("all rows", "every row", "each row", "fill")) else 0

    return FormulaSpec(
        formula_type=fn,
        target_sheet=ctx.sheet_name or "Sheet1",
        start_cell=f"{target_col}2",
        formula=formula,
        fill_rows=fill_rows,
        explanation=f"{fn} from {lookup_tab} into column {target_col}",
    )
