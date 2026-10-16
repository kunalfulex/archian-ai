"""Parse natural-language mapping instructions into structured plans."""
from __future__ import annotations

import re
from typing import Optional

from services.mapping_plan.models import ParsedMappingInstruction


def parse_mapping_instruction(
    instruction: Optional[str],
    flat_data: dict,
    *,
    destination: str = "salesforce",
) -> ParsedMappingInstruction:
    """
    Extract prompt-local destination hints (SQL table / GS sheet).

    Salesforce object targets come from plan pins / resolve_mapping_target_async —
    not from deterministic prompt parsing.
    """
    del flat_data  # reserved for API compatibility with mapping executors
    raw = (instruction or "").strip()
    if not raw:
        return ParsedMappingInstruction(raw="", parse_tier="empty")

    parsed = ParsedMappingInstruction(raw=raw, parse_tier="deterministic")

    if destination == "sql":
        m = re.search(r"(?i)\b(?:to|into)\s+(?:table\s+)?([a-z_][\w]*)", raw)
        if m:
            parsed.target_table = m.group(1)
    elif destination in ("google_sheets", "gsheets"):
        m = re.search(r"(?i)\b(?:to|into)\s+(?:sheet\s+)?([^\s,;]+)", raw)
        if m:
            parsed.target_sheet = m.group(1).strip()

    if parsed.target_object_api_name or parsed.target_table or parsed.target_sheet:
        parsed.parse_tier = "deterministic"
    return parsed


def parsed_to_dict(parsed: ParsedMappingInstruction) -> dict:
    return {
        "raw": parsed.raw,
        "target_object_api_name": parsed.target_object_api_name,
        "target_table": parsed.target_table,
        "target_sheet": parsed.target_sheet,
        "parse_tier": parsed.parse_tier,
    }
