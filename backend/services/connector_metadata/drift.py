"""Schema fingerprinting and drift detection."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from services.connector_metadata.models import DriftEntry, DriftReport


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint_salesforce_object(fields: List[Dict[str, Any]]) -> str:
    """Hash field catalog for one object (name, type, lookup target, required)."""
    parts = []
    for f in sorted(fields, key=lambda x: (x.get("field_api_name") or "")):
        parts.append(
            {
                "n": f.get("field_api_name"),
                "t": f.get("data_type"),
                "l": bool(f.get("is_lookup")),
                "r": f.get("lookup_object_api_name"),
                "req": bool(f.get("is_required")),
            }
        )
    return hashlib.sha256(_stable_json(parts).encode()).hexdigest()


def fingerprint_sql_schema(schema_data: Dict[str, Any]) -> str:
    cols = schema_data.get("columns") or []
    fks = schema_data.get("foreign_keys") or []
    col_parts = []
    for c in cols:
        if isinstance(c, dict):
            col_parts.append(
                {
                    "n": c.get("name") or c.get("column_name"),
                    "t": c.get("type") or c.get("data_type"),
                    "nullable": c.get("nullable"),
                }
            )
        elif isinstance(c, str):
            col_parts.append({"n": c})
    col_parts.sort(key=lambda x: x.get("n") or "")
    fk_parts = []
    for fk in fks:
        if isinstance(fk, dict):
            fk_parts.append(
                {
                    "c": fk.get("column") or fk.get("column_name"),
                    "t": fk.get("referenced_table") or fk.get("foreign_table_name"),
                }
            )
    fk_parts.sort(key=lambda x: (x.get("c") or "", x.get("t") or ""))
    return hashlib.sha256(_stable_json({"columns": col_parts, "foreign_keys": fk_parts}).encode()).hexdigest()


def fingerprint_sheets_header(header_schema: Dict[str, Any]) -> str:
    headers = header_schema.get("headers") if isinstance(header_schema, dict) else []
    names = []
    if isinstance(headers, list):
        for h in headers:
            if isinstance(h, dict):
                names.append(h.get("name"))
            elif isinstance(h, str):
                names.append(h)
    names = sorted(n for n in names if n)
    return hashlib.sha256(_stable_json(names).encode()).hexdigest()


def compute_drift(
    *,
    current_ids: List[str],
    stored: Dict[str, Optional[str]],
) -> DriftReport:
    """
    Compare entity ids present in source vs stored hashes.
    stored: entity_id -> hash (None if row exists but never hashed)
    """
    current_set = set(current_ids)
    stored_ids = set(stored.keys())
    report = DriftReport()

    for eid in sorted(current_set - stored_ids):
        report.new_entities.append(eid)
        report.entries.append(DriftEntry(entity_id=eid, kind="new_entity"))

    for eid in sorted(stored_ids - current_set):
        report.removed_entities.append(eid)
        report.entries.append(
            DriftEntry(entity_id=eid, kind="removed_entity", previous_hash=stored.get(eid))
        )

    for eid in sorted(current_set & stored_ids):
        prev = stored.get(eid)
        cur = stored.get(f"__current__:{eid}")
        if cur is None:
            cur = prev
        if prev is None or (cur and prev != cur):
            report.changed_entities.append(eid)
            report.entries.append(
                DriftEntry(
                    entity_id=eid,
                    kind="schema_changed",
                    previous_hash=prev,
                    current_hash=cur,
                )
            )
        else:
            report.unchanged_count += 1
            report.entries.append(
                DriftEntry(entity_id=eid, kind="unchanged", previous_hash=prev, current_hash=cur)
            )

    return report


def compute_drift_from_maps(
    *,
    previous_hashes: Dict[str, Optional[str]],
    current_hashes: Dict[str, str],
) -> DriftReport:
    """Standard drift: previous hash map vs current hash map for same entity ids."""
    all_ids = set(previous_hashes.keys()) | set(current_hashes.keys())
    report = DriftReport()

    for eid in sorted(all_ids):
        prev = previous_hashes.get(eid)
        cur = current_hashes.get(eid)
        if cur is not None and eid not in previous_hashes:
            report.new_entities.append(eid)
            report.entries.append(DriftEntry(entity_id=eid, kind="new_entity", current_hash=cur))
        elif cur is None and eid in previous_hashes:
            report.removed_entities.append(eid)
            report.entries.append(DriftEntry(entity_id=eid, kind="removed_entity", previous_hash=prev))
        elif cur is not None and prev != cur:
            report.changed_entities.append(eid)
            report.entries.append(
                DriftEntry(entity_id=eid, kind="schema_changed", previous_hash=prev, current_hash=cur)
            )
        elif cur is not None:
            report.unchanged_count += 1
            report.entries.append(DriftEntry(entity_id=eid, kind="unchanged", previous_hash=prev, current_hash=cur))

    return report
