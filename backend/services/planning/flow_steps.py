"""Load/save migration flows for planner v2 (Phase 7)."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from services.planning.schemas import Clarification, ClarificationOption


# Match saved-flow runs only when user names a flow/pipeline — not inline "start the migration".
_FLOW_RUN_RE = re.compile(
    r"\b(run|execute|start|rerun|re-run)\b.{0,40}\b(flow|pipeline)\b",
    re.I,
)
_SAVE_FLOW_RE = re.compile(r"\b(save|store)\b.{0,30}\b(as\s+)?(a\s+)?flow\b", re.I)


def prompt_requests_load_flow(
    prompt: Optional[str],
    classification: Optional[Dict[str, Any]] = None,
) -> bool:
    if classification:
        from services.planning.intent_routing import is_map_migrate_classification

        if is_map_migrate_classification(classification):
            return False
    return bool(_FLOW_RUN_RE.search(prompt or ""))


def prompt_requests_save_flow(prompt: Optional[str]) -> bool:
    return bool(_SAVE_FLOW_RE.search(prompt or ""))


async def list_user_flows(user_id: str, *, limit: int = 25) -> List[Dict[str, Any]]:
    from db.connection import database

    rows = await database.fetch_all(
        """
        SELECT id::text AS id, migration_name, destination_type, created_at, updated_at
        FROM migration_flows
        WHERE user_id = :user_id
        ORDER BY updated_at DESC NULLS LAST
        LIMIT :limit
        """,
        {"user_id": str(user_id), "limit": limit},
    )
    return [dict(r) for r in rows or []]


async def apply_saved_flow_to_classification(
    classification: Dict[str, Any],
    flow_id: str,
    user_id: str,
) -> Dict[str, Any]:
    """Merge saved flow source/destination/rules into classification for plan building."""
    from db.extracted_schema import get_flow_details
    from uuid import UUID

    out = dict(classification or {})
    details = await get_flow_details(str(flow_id), UUID(str(user_id)))
    flow = (details or {}).get("flow") or {}
    if not flow:
        return out

    dest_type = (flow.get("destination_type") or "salesforce").lower()
    src_type = (flow.get("source_type") or "file").lower()
    if src_type == "gsheets":
        src_type = "google_sheets"

    out["flow_id"] = str(flow_id)
    out["load_flow"] = True
    out["destination"] = dest_type
    out["source"] = src_type
    if flow.get("source_name"):
        out["source_name"] = flow["source_name"]
    if flow.get("destination_name"):
        out["destination_name"] = flow["destination_name"]
    if flow.get("destination_id"):
        out["destination_spreadsheet_id"] = str(flow["destination_id"])

    rules = flow.get("migration_rules") or {}
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except json.JSONDecodeError:
            rules = {}
    ext_rules = (rules or {}).get("extraction_rules")
    if ext_rules:
        out["extraction_rules"] = ext_rules

    from utils.migration_rules_utils import get_mapping_transformation

    mt = get_mapping_transformation(rules)
    if mt:
        out["flow_mapping_transformation"] = mt

    out["intent"] = out.get("intent") or "extract"
    out["mode"] = out.get("mode") or "extract"
    out["reasoning"] = f"Loaded flow: {flow.get('migration_name') or flow_id}"

    field_mappings = (details or {}).get("field_mappings") or []
    if field_mappings:
        out["flow_field_mappings"] = field_mappings
    return out


def flow_field_mappings_to_config(
    field_mappings: List[Dict[str, Any]],
    *,
    destination: str,
    mapping_transformation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert DB field_mappings rows to map_fields / migration mapping_config shape."""
    dest = (destination or "").lower()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in field_mappings:
        src = (row.get("extracted_field_name") or row.get("source_field") or "").strip()
        if not src:
            continue
        grouped.setdefault(src, []).append(row)

    mapping: Dict[str, Any] = {}
    for extracted_field_name, rows in grouped.items():
        rows.sort(key=lambda x: x.get("created_at") or 0)
        if len(rows) > 1:
            items: List[Dict[str, Any]] = []
            for row in rows:
                items.append(_flow_row_to_mapping_entry(row, dest))
            mapping[extracted_field_name] = items
            continue
        mapping[extracted_field_name] = _flow_row_to_mapping_entry(rows[0], dest)

    if mapping_transformation:
        mapping = apply_mapping_transformation_to_config(
            mapping, mapping_transformation, destination=dest
        )
    return mapping


def _flow_row_to_mapping_entry(row: Dict[str, Any], dest: str) -> Dict[str, Any]:
    object_value = row.get("salesforce_object") or row.get("destination_object")
    field_value = row.get("salesforce_field") or row.get("destination_field") or row.get("sheet_column")
    is_duplicate = bool(row.get("is_duplicate_check", False))
    is_required = bool(row.get("is_required", False))
    lookup_value = row.get("lookup_of")

    if dest == "salesforce":
        entry: Dict[str, Any] = {
            "object": object_value or "Object",
            "field": field_value or row.get("extracted_field_name"),
            "is_duplicate_check": is_duplicate,
            "is_required": is_required,
        }
        if lookup_value:
            entry["lookup_of"] = lookup_value
        return entry
    if dest in ("google_sheets", "gsheets"):
        return {
            "sheet": object_value,
            "column": field_value,
            "is_duplicate_check": is_duplicate,
            "is_required": is_required,
        }
    if dest == "sql":
        entry = {
            "table": object_value or row.get("destination_table") or row.get("table_name"),
            "column": field_value or row.get("column_name"),
            "is_duplicate_check": is_duplicate,
            "is_required": is_required,
        }
        if lookup_value:
            entry["lookup_of"] = lookup_value
        return entry
    return field_value or row.get("extracted_field_name")


def _build_combine_group_id(primary: str, secondary: str) -> str:
    return f"{primary}.{secondary}".lower().replace(" ", "_")


def apply_mapping_transformation_to_config(
    mapping: Dict[str, Any],
    mapping_transformation: Optional[Dict[str, Any]],
    *,
    destination: str,
) -> Dict[str, Any]:
    """Restore is_combined_field flags and drop orphan output keys from saved flow rules."""
    if not mapping or not isinstance(mapping_transformation, dict):
        return mapping
    fields = mapping_transformation.get("fields")
    if not isinstance(fields, list):
        return mapping

    dest = (destination or "").lower()
    out = dict(mapping)
    primary_key = "object" if dest == "salesforce" else ("sheet" if dest in ("google_sheets", "gsheets") else "table")
    secondary_key = "field" if dest == "salesforce" else "column"

    for rule in fields:
        if not isinstance(rule, dict) or rule.get("action") != "combine":
            continue
        source_fields = [sf for sf in (rule.get("source_fields") or []) if sf in out and not isinstance(out[sf], list)]
        if len(source_fields) < 2:
            continue

        primary = (
            rule.get("output_object")
            or rule.get("output_sheet")
            or rule.get("output_table")
            or ""
        )
        secondary = (rule.get("output_field") or "").strip()
        if not primary and source_fields:
            first = out.get(source_fields[0]) or {}
            if isinstance(first, dict):
                primary = (first.get(primary_key) or "").strip()
        if not primary or not secondary:
            continue

        combine_group = _build_combine_group_id(primary, secondary)
        for idx, sf in enumerate(source_fields):
            base = dict(out[sf]) if isinstance(out[sf], dict) else {}
            base.pop("is_combined_field", None)
            base.pop("combine_group", None)
            base.pop("combine_order", None)
            base[primary_key] = primary
            base[secondary_key] = secondary
            base["is_combined_field"] = True
            base["combine_group"] = combine_group
            base["combine_order"] = idx
            out[sf] = base

        output_field = (rule.get("output_field") or "").strip()
        if output_field:
            for key in list(out.keys()):
                if key in source_fields:
                    continue
                if key.lower() == output_field.lower():
                    out.pop(key, None)

    return out


def detect_flow_clarifications(
    classification: Dict[str, Any],
    prompt: Optional[str],
    flows: List[Dict[str, Any]],
) -> List[Clarification]:
    if classification.get("flow_id"):
        return []
    if not prompt_requests_load_flow(prompt, classification):
        return []
    if not flows:
        return []
    options = [
        ClarificationOption(
            id=f"flow_{f['id']}",
            label=f.get("migration_name") or f["id"],
        )
        for f in flows[:20]
        if f.get("id")
    ]
    if not options:
        return []
    return [
        Clarification(
            id=str(uuid.uuid4()),
            clarification_type="pick_flow",
            prompt="Which saved flow should we run?",
            options=options,
            required=True,
        )
    ]
