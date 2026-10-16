"""Schema-level gap analysis (no row storage)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.connection import database
from services.data_assessment.config import SF_QUICK_OBJECTS
from services.data_assessment.models import EntityRef, GapFinding

CRM_CORE = set(SF_QUICK_OBJECTS)
EMPTY_SAMPLE_MISSING_PCT = 50.0


async def analyze_salesforce_gaps(
    user_id: str,
    org_id: str,
    entities: List[EntityRef],
) -> List[GapFinding]:
    findings: List[GapFinding] = []
    assessed = {e.entity_id for e in entities}

    last_row = await database.fetch_one(
        """
        SELECT MAX(created_at) AS ts FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
        """,
        {"user_id": user_id, "org_id": org_id},
    )
    if last_row and last_row["ts"]:
        ts = last_row["ts"]
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            age_days = (datetime.now(timezone.utc) - ts.replace(tzinfo=timezone.utc)).days
        else:
            age_days = (datetime.now(timezone.utc) - ts).days
        if age_days > 14:
            findings.append(
                GapFinding(
                    entity_id="*",
                    severity="medium",
                    category="schema_staleness",
                    message=f"Salesforce schema last synced {age_days} days ago; refresh before trusting field metadata.",
                )
            )

    for core in CRM_CORE:
        if core not in assessed:
            findings.append(
                GapFinding(
                    entity_id=core,
                    severity="low",
                    category="coverage",
                    message=f"Core object {core} was not included in this assessment run.",
                )
            )

    synced_objects = await database.fetch_all(
        """
        SELECT object_api_name FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
        """,
        {"user_id": user_id, "org_id": org_id},
    )
    synced_set = {dict(r).get("object_api_name") for r in synced_objects or []}
    synced_set.discard(None)

    for ent in entities:
        fields = await database.fetch_all(
            """
            SELECT field_api_name, is_required, is_lookup, lookup_object_api_name
            FROM salesforce_object_fields
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
            """,
            {"user_id": user_id, "org_id": org_id, "obj": ent.entity_id},
        )
        field_rows = [dict(r) for r in fields or []]
        if not field_rows:
            findings.append(
                GapFinding(
                    entity_id=ent.entity_id,
                    severity="medium",
                    category="metadata",
                    message=f"No synced fields for {ent.label}; run Salesforce schema sync first.",
                )
            )
            continue
        if not any(fr.get("is_required") for fr in field_rows):
            findings.append(
                GapFinding(
                    entity_id=ent.entity_id,
                    severity="low",
                    category="metadata",
                    message=f"No required fields recorded for {ent.label}; field catalog may be incomplete.",
                )
            )

        for fr in field_rows:
            if not fr.get("is_lookup"):
                continue
            target = (fr.get("lookup_object_api_name") or "").strip()
            if not target:
                continue
            fname = fr.get("field_api_name") or "lookup"
            if target not in synced_set:
                findings.append(
                    GapFinding(
                        entity_id=ent.entity_id,
                        severity="high",
                        category="relationship",
                        message=(
                            f"{ent.label}: lookup field {fname} references {target}, "
                            f"which is not in synced schema for this org."
                        ),
                    )
                )
            elif target not in assessed:
                findings.append(
                    GapFinding(
                        entity_id=ent.entity_id,
                        severity="medium",
                        category="relationship",
                        message=(
                            f"{ent.label}: lookup field {fname} references {target}, "
                            f"which was not included in this assessment run."
                        ),
                    )
                )

    return findings


async def analyze_sql_gaps(
    user_id: str,
    connector_id: str,
    entities: List[EntityRef],
    entity_profiles: Optional[List[Dict[str, Any]]] = None,
) -> List[GapFinding]:
    findings: List[GapFinding] = []
    rows = await database.fetch_all(
        """
        SELECT table_name, schema_data FROM sql_extracted_schemas
        WHERE user_id = :user_id AND connector_id = :connector_id
        """,
        {"user_id": user_id, "connector_id": connector_id},
    )
    schema_by_table: Dict[str, Dict[str, Any]] = {}
    synced = set()
    for r in rows or []:
        row = dict(r)
        tbl = row.get("table_name")
        if not tbl:
            continue
        synced.add(tbl)
        raw = row.get("schema_data")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        schema_by_table[tbl] = raw if isinstance(raw, dict) else {}

    profile_by_entity = {ep.get("entity_id"): ep for ep in (entity_profiles or [])}

    for ent in entities:
        if ent.entity_id not in synced:
            findings.append(
                GapFinding(
                    entity_id=ent.entity_id,
                    severity="medium",
                    category="metadata",
                    message=f"Table {ent.label or ent.entity_id} has no cached schema; run database analysis first.",
                )
            )
            continue

        payload = schema_by_table.get(ent.entity_id) or {}
        fks = payload.get("foreign_keys") or []
        if not isinstance(fks, list):
            fks = []

        for fk in fks:
            if not isinstance(fk, dict):
                continue
            ref_table = (fk.get("referenced_table") or fk.get("foreign_table_name") or "").strip()
            col = (fk.get("column") or fk.get("column_name") or "").strip()
            if not ref_table:
                continue
            if ref_table not in synced:
                findings.append(
                    GapFinding(
                        entity_id=ent.entity_id,
                        severity="high",
                        category="relationship",
                        message=(
                            f"{ent.label or ent.entity_id}: foreign key {col or '?'} references "
                            f"table {ref_table}, which has no cached schema for this connector."
                        ),
                    )
                )

        ep = profile_by_entity.get(ent.entity_id)
        if ep and not ep.get("error"):
            qr = ep.get("quality_report") or {}
            sampled_cols = set((qr.get("missing_values") or {}).get("by_field") or {})
            sampled_cols.update((qr.get("format_issues") or {}).keys())
            cols_meta = payload.get("columns") or []
            if isinstance(cols_meta, list):
                for col_def in cols_meta:
                    cname = None
                    if isinstance(col_def, dict):
                        cname = col_def.get("name") or col_def.get("column_name")
                    elif isinstance(col_def, str):
                        cname = col_def
                    if cname and cname not in sampled_cols and fks:
                        for fk in fks:
                            if isinstance(fk, dict) and (fk.get("column") or fk.get("column_name")) == cname:
                                findings.append(
                                    GapFinding(
                                        entity_id=ent.entity_id,
                                        severity="low",
                                        category="metadata",
                                        message=(
                                            f"{ent.label or ent.entity_id}: FK column {cname} "
                                            f"was not present in sampled row data."
                                        ),
                                    )
                                )
                                break

    return findings


async def analyze_sheets_gaps(
    user_id: str,
    connection_id: str,
    entities: List[EntityRef],
    entity_profiles: Optional[List[Dict[str, Any]]] = None,
) -> List[GapFinding]:
    findings: List[GapFinding] = []
    profile_by_entity = {ep.get("entity_id"): ep for ep in (entity_profiles or [])}

    for ent in entities:
        sheet = ent.meta.get("sheet_name")
        spreadsheet_id = ent.meta.get("spreadsheet_id") or (
            ent.entity_id.split("::")[0] if "::" in ent.entity_id else None
        )
        if not sheet:
            findings.append(
                GapFinding(
                    entity_id=ent.entity_id,
                    severity="low",
                    category="metadata",
                    message="Sheet tab metadata missing.",
                )
            )
            continue
        if not spreadsheet_id:
            continue

        schema_row = await database.fetch_one(
            """
            SELECT header_schema FROM gsheets_extracted_schemas
            WHERE user_id = :user_id
              AND google_sheets_connection_id = :conn_id
              AND spreadsheet_id = :sid
              AND sheet_name = :sheet
            LIMIT 1
            """,
            {
                "user_id": user_id,
                "conn_id": connection_id,
                "sid": spreadsheet_id,
                "sheet": sheet,
            },
        )
        if not schema_row:
            findings.append(
                GapFinding(
                    entity_id=ent.entity_id,
                    severity="medium",
                    category="metadata",
                    message=(
                        f"{ent.label}: no header schema cached; run sheet analysis before trusting column metadata."
                    ),
                )
            )
            continue

        raw = dict(schema_row).get("header_schema")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        headers: List[str] = []
        if isinstance(raw, dict):
            h = raw.get("headers")
            if isinstance(h, list):
                headers = [str(x) for x in h if x]
        elif isinstance(raw, list):
            headers = [str(x) for x in raw if x]

        ep = profile_by_entity.get(ent.entity_id)
        if not ep or ep.get("error") or not headers:
            continue

        qr = ep.get("quality_report") or {}
        by_field = (qr.get("missing_values") or {}).get("by_field") or {}
        percentages = (qr.get("missing_values") or {}).get("percentages") or {}
        records = int(ep.get("records_sampled") or qr.get("total_records") or 0)
        if records <= 0:
            continue

        for col in headers:
            missing_count = int(by_field.get(col) or 0)
            pct = percentages.get(col)
            if pct is None and records > 0:
                pct = round((missing_count / records) * 100, 2)
            try:
                pct_f = float(pct or 0)
            except (TypeError, ValueError):
                pct_f = 0.0
            if pct_f >= EMPTY_SAMPLE_MISSING_PCT:
                findings.append(
                    GapFinding(
                        entity_id=ent.entity_id,
                        severity="medium",
                        category="metadata",
                        message=(
                            f"{ent.label}: column '{col}' is {pct_f:.0f}% empty in the sample "
                            f"({missing_count} of {records} rows)."
                        ),
                    )
                )

    return findings
