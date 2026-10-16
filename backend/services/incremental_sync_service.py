"""
Incremental sync (no CDC): query fingerprint, SOQL/SQL predicates, watermark persistence.
Phase 0 B: watermark advances on extraction success (see INCREMENTAL_SYNC_IMPLEMENTATION_PLAN.md).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from db.connection import database
from services.extraction_results_service import ExtractionResultsService

SF_DEFAULT_CURSOR = "LastModifiedDate"

# Prefix for grep-friendly logs when diagnosing SQL incremental predicates / watermarks.
_SQL_INCR_DBG = "[SQL INCREMENTAL DEBUG]"


def normalize_query_for_fingerprint(query: Optional[str]) -> str:
    if not query:
        return ""
    q = query.strip()
    q = re.sub(r"\s+", " ", q)
    return q.lower()


def compute_query_fingerprint(query: Optional[str]) -> str:
    n = normalize_query_for_fingerprint(query)
    return hashlib.sha256(n.encode("utf-8")).hexdigest()


def resolve_cursor_field(source_type: str, incremental_cursor_field: Optional[str]) -> str:
    st = (source_type or "file").lower()
    if st == "salesforce":
        return (incremental_cursor_field or "").strip() or SF_DEFAULT_CURSOR
    if st == "sql":
        col = (incremental_cursor_field or "").strip()
        if not col:
            raise ValueError("incremental_cursor_field is required for SQL incremental sync")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", col):
            raise ValueError("Invalid SQL cursor column name")
        return col
    return (incremental_cursor_field or "").strip() or SF_DEFAULT_CURSOR


def format_sf_datetime_utc(wm: datetime) -> str:
    if wm.tzinfo is None:
        wm = wm.replace(tzinfo=timezone.utc)
    else:
        wm = wm.astimezone(timezone.utc)
    return wm.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ensure_soql_selects_cursor_field(soql: str, cursor_field: str) -> str:
    """Append cursor field to SELECT list if missing (common SOQL)."""
    if not soql or not cursor_field:
        return soql
    m = re.search(r"(?is)\bselect\s+(.+?)\s+from\s+", soql)
    if not m:
        return soql
    inner = m.group(1).strip()
    if inner.upper().startswith("FIELDS("):
        return soql
    tokens = [p.strip() for p in inner.split(",")]
    cf_lower = cursor_field.lower()
    for t in tokens:
        tl = t.lower()
        if tl == cf_lower or tl.endswith("." + cf_lower):
            return soql
    return soql[: m.start(1)] + inner + ", " + cursor_field + soql[m.end(1) :]


def ensure_sql_selects_cursor_column(sql: str, cursor_column: str, dialect: str) -> str:
    """
    Append the incremental cursor column to the SELECT list if it is missing.
    Mirrors Salesforce baseline behavior so extracted rows contain the cursor value for watermark updates.
    Uses dialect-appropriate quoting (e.g. "col" for PostgreSQL).
    """
    if not sql or not cursor_column:
        return sql
    m = re.search(r"(?is)\bselect\s+(.+?)\s+from\s+", sql)
    if not m:
        return sql
    inner = m.group(1).strip()
    if inner == "*" or re.match(r"^\*\s*$", inner):
        return sql
    qcol = quote_sql_identifier(dialect, cursor_column)
    cf_lower = cursor_column.lower()
    tokens = [p.strip() for p in inner.split(",")]
    for t in tokens:
        tl = t.lower()
        if tl == cf_lower or tl.endswith("." + cf_lower):
            return sql
        bare = re.sub(r'^[`"\[\]]+|[`"\[\]]+$', "", t.split()[0] if t else "")
        if bare.lower() == cf_lower or bare.lower().endswith("." + cf_lower):
            return sql
    return sql[: m.start(1)] + inner + ", " + qcol + sql[m.end(1) :]


def inject_soql_incremental_predicate(soql: str, cursor_field: str, watermark: datetime) -> str:
    s = soql.strip().rstrip(";")
    lit = format_sf_datetime_utc(watermark)
    pred = f"{cursor_field} > {lit}"
    mlim = re.search(r"(?is)\b(LIMIT|ORDER BY|GROUP BY|OFFSET)\b", s)
    if re.search(r"(?is)\bwhere\b", s):
        if mlim:
            pos = mlim.start()
            return s[:pos].rstrip() + f" AND {pred} " + s[pos:]
        return s + f" AND {pred}"
    mfrom = re.search(r"(?is)\bfrom\s+", s)
    if mfrom:
        after_from = s[mfrom.end() :]
        mlim2 = re.search(r"(?is)\b(LIMIT|ORDER BY|GROUP BY|OFFSET)\b", after_from)
        if mlim2:
            pos = mfrom.end() + mlim2.start()
            return s[:pos].rstrip() + f" WHERE {pred} " + s[pos:]
    return s + f" WHERE {pred}"


def quote_sql_identifier(dialect: str, name: str) -> str:
    d = (dialect or "postgresql").lower()
    if d in ("mysql", "mariadb"):
        return f"`{name}`"
    if d in ("mssql", "sqlserver", "microsoft sql server"):
        return f"[{name}]"
    return f'"{name}"'


def format_sql_literal_timestamp(wm: datetime, dialect: str) -> str:
    if wm.tzinfo is None:
        wm = wm.replace(tzinfo=timezone.utc)
    else:
        wm = wm.astimezone(timezone.utc)
    s = wm.strftime("%Y-%m-%d %H:%M:%S")
    d = (dialect or "postgresql").lower()
    return f"'{s}'"


def inject_sql_incremental_predicate(
    sql: str, cursor_column: str, dialect: str, watermark: datetime
) -> str:
    s = sql.strip().rstrip(";")
    qcol = quote_sql_identifier(dialect, cursor_column)
    lit = format_sql_literal_timestamp(watermark, dialect)
    pred = f"{qcol} > {lit}"
    mlim = re.search(r"(?is)\b(ORDER BY|GROUP BY|LIMIT|OFFSET|FETCH)\b", s)
    if re.search(r"(?is)\bwhere\b", s):
        if mlim:
            pos = mlim.start()
            return s[:pos].rstrip() + f" AND {pred} " + s[pos:]
        return s + f" AND {pred}"
    mfrom = re.search(r"(?is)\bfrom\s+", s)
    if mfrom:
        after = s[mfrom.end() :]
        mlim2 = re.search(r"(?is)\b(ORDER BY|GROUP BY|LIMIT|OFFSET|FETCH)\b", after)
        if mlim2:
            pos = mfrom.end() + mlim2.start()
            return s[:pos].rstrip() + f" WHERE {pred} " + s[pos:]
    return s + f" WHERE {pred}"


def _parse_value_to_datetime(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        s = val.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(val[:19], fmt)
            except ValueError:
                continue
    return None


def _get_record_cursor_value(rec: Dict[str, Any], cursor_field: str) -> Optional[datetime]:
    if not rec or not cursor_field:
        return None
    for k in (cursor_field, cursor_field.lower(), cursor_field.upper()):
        if k in rec:
            return _parse_value_to_datetime(rec[k])
    cf_lower = cursor_field.lower()
    for rk, rv in rec.items():
        if rk and str(rk).lower() == cf_lower:
            return _parse_value_to_datetime(rv)
    return None


def _salesforce_type_is_incremental_cursor(sf_type: Optional[str]) -> bool:
    """Salesforce describe uses lowercase type strings (date, datetime, etc.)."""
    t = (sf_type or "").lower().strip()
    return t in ("datetime", "date")


def _sql_data_type_is_incremental_cursor(data_type: Optional[str]) -> bool:
    """True for DATE / TIMESTAMP / DATETIME-style columns; excludes VARCHAR and TIME-only."""
    if not data_type or not isinstance(data_type, str):
        return False
    u = data_type.upper()
    if any(x in u for x in ("CHAR", "TEXT", "BINARY", "BLOB", "JSON", "XML", "UUID")):
        return False
    if "TIMESTAMP" in u or "DATETIME" in u:
        return True
    if re.search(r"\bDATE\b", u):
        return True
    return False


def _collect_sql_columns_from_schema_data(schema_data: Any) -> List[Dict[str, Any]]:
    """Normalize sql_extracted_schemas.schema_data into dict rows with column_name + data_type."""
    if schema_data is None:
        return []
    raw = schema_data
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    cols_raw: List[Any]
    if isinstance(raw, dict) and "columns" in raw:
        cols_raw = raw.get("columns") or []
    elif isinstance(raw, list):
        cols_raw = raw
    else:
        return []
    out: List[Dict[str, Any]] = []
    for col in cols_raw:
        if not isinstance(col, dict):
            continue
        name = (col.get("column_name") or col.get("name") or "").strip()
        if not name:
            continue
        dtype = col.get("data_type") or col.get("type") or ""
        out.append({"column_name": name, "data_type": str(dtype) if dtype is not None else ""})
    return out


class IncrementalSyncService:
    @staticmethod
    async def fetch_incremental_row(flow_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        row = await database.fetch_one(
            """
            SELECT incremental_sync_enabled, incremental_cursor_field, incremental_high_watermark,
                   incremental_high_watermark_text, incremental_last_extraction_success_at,
                   incremental_query_fingerprint
            FROM migration_flows
            WHERE id = :fid AND user_id = :uid
            """,
            {"fid": flow_id, "uid": user_id},
        )
        return dict(row) if row else None

    @staticmethod
    async def clear_watermark_on_fingerprint_mismatch(
        flow_id: str, user_id: str, incremental_enabled: bool, stored_fp: Optional[str], current_fp: str
    ) -> bool:
        if not incremental_enabled:
            return False
        if stored_fp and current_fp and stored_fp != current_fp:
            await database.execute(
                """
                UPDATE migration_flows
                SET incremental_high_watermark = NULL,
                    incremental_high_watermark_text = NULL,
                    updated_at = NOW()
                WHERE id = :fid AND user_id = :uid
                """,
                {"fid": flow_id, "uid": user_id},
            )
            return True
        return False

    @staticmethod
    def prepare_salesforce_rerun_soql(
        soql: str,
        flow_row: Optional[Dict[str, Any]],
        schema_sql_query: Optional[str],
    ) -> str:
        if not flow_row or not flow_row.get("incremental_sync_enabled"):
            return soql
        current_fp = compute_query_fingerprint(schema_sql_query)
        stored_fp = flow_row.get("incremental_query_fingerprint")
        if stored_fp and current_fp and stored_fp != current_fp:
            return soql
        wm = flow_row.get("incremental_high_watermark")
        if wm is None:
            cursor_field = resolve_cursor_field("salesforce", flow_row.get("incremental_cursor_field"))
            out = ensure_soql_selects_cursor_field(soql, cursor_field)
            return out
        if isinstance(wm, str):
            try:
                wm = datetime.fromisoformat(wm.replace("Z", "+00:00"))
            except ValueError:
                return soql
        cursor_field = resolve_cursor_field("salesforce", flow_row.get("incremental_cursor_field"))
        q = ensure_soql_selects_cursor_field(soql, cursor_field)
        out = inject_soql_incremental_predicate(q, cursor_field, wm)
        return out

    @staticmethod
    def prepare_sql_rerun_query(
        sql: str,
        dialect: str,
        flow_row: Optional[Dict[str, Any]],
        schema_sql_query: Optional[str],
    ) -> str:
        sql_preview = ((sql or "").replace("\n", " "))[:280]
        print(
            f"{_SQL_INCR_DBG} prepare_sql_rerun_query: dialect={dialect!r} sql_len={len(sql or '')} "
            f"preview={sql_preview!r}{'...' if len(sql or '') > 280 else ''}"
        )

        if not flow_row or not flow_row.get("incremental_sync_enabled"):
            print(
                f"{_SQL_INCR_DBG} → full SQL unchanged: incremental off or missing flow_row "
                f"(enabled={bool(flow_row and flow_row.get('incremental_sync_enabled'))})"
            )
            return sql

        try:
            cursor_col = resolve_cursor_field("sql", flow_row.get("incremental_cursor_field"))
        except ValueError as e:
            print(
                f"{_SQL_INCR_DBG} → full SQL unchanged: invalid SQL cursor column "
                f"(saved={flow_row.get('incremental_cursor_field')!r}): {e}"
            )
            return sql

        _sql_before_ensure = sql
        sql = ensure_sql_selects_cursor_column(sql, cursor_col, dialect)
        if sql != _sql_before_ensure:
            _pv = (sql.replace("\n", " "))[:320]
            print(
                f"{_SQL_INCR_DBG} ensure SELECT includes cursor: preview={_pv!r}{'...' if len(sql) > 320 else ''}"
            )

        current_fp = compute_query_fingerprint(schema_sql_query)
        stored_fp = flow_row.get("incremental_query_fingerprint")
        print(
            f"{_SQL_INCR_DBG} fingerprints: stored[:16]={str(stored_fp)[:16] if stored_fp else None!r} "
            f"current[:16]={str(current_fp)[:16]} equal={(stored_fp == current_fp) if (stored_fp and current_fp) else None}"
        )
        if stored_fp and current_fp and stored_fp != current_fp:
            print(
                f"{_SQL_INCR_DBG} → baseline SQL (no WHERE): fingerprint mismatch; cursor {cursor_col!r} ensured in SELECT"
            )
            return sql

        wm = flow_row.get("incremental_high_watermark")
        if wm is None:
            print(
                f"{_SQL_INCR_DBG} → baseline SQL (no WHERE yet): watermark NULL; cursor {cursor_col!r} "
                "added to SELECT if missing so extraction can persist max(cursor). Incremental WHERE applies after that."
            )
            return sql

        if isinstance(wm, str):
            try:
                wm = datetime.fromisoformat(wm.replace("Z", "+00:00"))
            except ValueError:
                print(f"{_SQL_INCR_DBG} → full SQL unchanged: invalid watermark string {wm!r}")
                return sql

        out = inject_sql_incremental_predicate(sql, cursor_col, dialect, wm)
        out_preview = (out.replace("\n", " "))[:360]
        print(
            f"{_SQL_INCR_DBG} → incremental WHERE applied: column={cursor_col!r} wm={wm!r} "
            f"out_len={len(out)} out_preview={out_preview!r}{'...' if len(out) > 360 else ''}"
        )
        return out

    @staticmethod
    async def persist_after_extraction_success(
        flow_id: str,
        user_id: str,
        job_id: str,
        extracted_schema_type: str,
        schema_sql_query: Optional[str],
    ) -> None:
        row = await IncrementalSyncService.fetch_incremental_row(flow_id, user_id)
        if not row or not row.get("incremental_sync_enabled"):
            return
        fp = compute_query_fingerprint(schema_sql_query)
        try:
            cursor_field = resolve_cursor_field(extracted_schema_type, row.get("incremental_cursor_field"))
        except ValueError as e:
            if (extracted_schema_type or "").lower() == "sql":
                print(
                    f"{_SQL_INCR_DBG} persist_after_extraction: skipped — bad SQL cursor for flow_id={flow_id} "
                    f"saved={row.get('incremental_cursor_field')!r}: {e}"
                )
            return
        max_dt: Optional[datetime] = None
        total_seen = 0
        async for batch in ExtractionResultsService.iterate_extraction_records(
            job_id=job_id,
            user_id=user_id,
            batch_size=3000,
            destination="salesforce",
        ):
            for rec in batch:
                total_seen += 1
                v = _get_record_cursor_value(rec, cursor_field)
                if v:
                    if max_dt is None or v > max_dt:
                        max_dt = v
        if (extracted_schema_type or "").lower() == "sql":
            print(
                f"{_SQL_INCR_DBG} persist_after_extraction: flow_id={flow_id} job_id={job_id} "
                f"cursor_field={cursor_field!r} rows_scanned={total_seen} max_from_cursor_column={max_dt!r} "
                f"(if max is None but rows>0, extracted record keys may not match cursor name)"
            )
        now = datetime.now(timezone.utc)
        if total_seen == 0:
            await database.execute(
                """
                UPDATE migration_flows
                SET incremental_query_fingerprint = :fp,
                    incremental_last_extraction_success_at = :now,
                    updated_at = NOW()
                WHERE id = :fid AND user_id = :uid
                """,
                {"fp": fp, "now": now, "fid": flow_id, "uid": user_id},
            )
            return
        if max_dt is not None:
            await database.execute(
                """
                UPDATE migration_flows
                SET incremental_high_watermark = :wm,
                    incremental_query_fingerprint = :fp,
                    incremental_last_extraction_success_at = :now,
                    updated_at = NOW()
                WHERE id = :fid AND user_id = :uid
                """,
                {"wm": max_dt, "fp": fp, "now": now, "fid": flow_id, "uid": user_id},
            )
        else:
            await database.execute(
                """
                UPDATE migration_flows
                SET incremental_query_fingerprint = :fp,
                    incremental_last_extraction_success_at = :now,
                    updated_at = NOW()
                WHERE id = :fid AND user_id = :uid
                """,
                {"fp": fp, "now": now, "fid": flow_id, "uid": user_id},
            )

    @staticmethod
    async def get_incremental_cursor_field_options(flow_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Datetime/date cursor candidates from cached metadata:
        Salesforce: salesforce_object_fields for extracted_schemas.table_name (object API name).
        SQL: sql_extracted_schemas.schema_data columns for connector_id + table_name.
        """
        row = await database.fetch_one(
            """
            SELECT es.extracted_schema_type, es.table_name, es.connector_id
            FROM migration_flows mf
            INNER JOIN extracted_schemas es ON es.migration_flow_id = mf.id
            WHERE mf.id = :fid AND mf.user_id = :uid
            """,
            {"fid": flow_id, "uid": user_id},
        )
        if not row:
            return None
        es = dict(row)
        source_type = (es.get("extracted_schema_type") or "file").lower()
        table_or_object = (es.get("table_name") or "").strip() or None
        connector_id = es.get("connector_id")

        base: Dict[str, Any] = {
            "status": "success",
            "source_type": source_type,
            "table_or_object": table_or_object,
            "options": [],
            "hint": None,
        }

        if source_type == "salesforce":
            if not table_or_object:
                base["hint"] = "No object name on this flow; enter a Salesforce field API name manually."
                return base
            if not connector_id:
                base["hint"] = "No Salesforce connection on this flow."
                return base
            token_row = await database.fetch_one(
                """
                SELECT org_id FROM salesforce_tokens
                WHERE id = :tid AND user_id = :uid
                """,
                {"tid": str(connector_id), "uid": user_id},
            )
            if not token_row:
                base["hint"] = "Salesforce connection not found; enter cursor field manually."
                return base
            org_id = dict(token_row).get("org_id")
            fields = await database.fetch_all(
                """
                SELECT field_api_name, field_label, data_type
                FROM salesforce_object_fields
                WHERE user_id = :uid AND org_id = :org AND object_api_name = :obj
                ORDER BY field_label NULLS LAST, field_api_name
                """,
                {"uid": user_id, "org": org_id, "obj": table_or_object},
            )
            options: List[Dict[str, str]] = []
            for f in fields or []:
                fd = dict(f)
                api = fd.get("field_api_name") or ""
                dtype = fd.get("data_type") or ""
                if not api or not _salesforce_type_is_incremental_cursor(dtype):
                    continue
                label = (fd.get("field_label") or api).strip()
                options.append(
                    {
                        "value": api,
                        "label": f"{label} ({api})",
                        "data_type": dtype,
                    }
                )
            base["options"] = options
            if not options:
                base["hint"] = (
                    f"No date/datetime fields cached for {table_or_object}. "
                    "Sync Salesforce metadata for this object, or type an API name (e.g. LastModifiedDate)."
                )
            return base

        if source_type == "sql":
            if not table_or_object:
                base["hint"] = "No table name on this flow; enter a SQL column name manually."
                return base
            if not connector_id:
                base["hint"] = "No SQL connector on this flow."
                return base
            schema_row = await database.fetch_one(
                """
                SELECT schema_data
                FROM sql_extracted_schemas
                WHERE user_id = :uid AND connector_id = :cid
                  AND LOWER(TRIM(table_name::text)) = LOWER(TRIM(:tbl))
                ORDER BY (migration_flow_id = :fid) DESC, extracted_at DESC NULLS LAST
                LIMIT 1
                """,
                {
                    "uid": user_id,
                    "cid": str(connector_id),
                    "tbl": table_or_object,
                    "fid": flow_id,
                },
            )
            if not schema_row:
                base["hint"] = (
                    f"No cached schema for table «{table_or_object}». "
                    "Reconnect or re-run extraction setup, or type a column name manually."
                )
                return base
            sd = dict(schema_row).get("schema_data")
            col_opts: List[Dict[str, str]] = []
            for col in _collect_sql_columns_from_schema_data(sd):
                cname = col["column_name"]
                dtype = col["data_type"]
                if not _sql_data_type_is_incremental_cursor(dtype):
                    continue
                col_opts.append(
                    {
                        "value": cname,
                        "label": f"{cname} ({dtype})",
                        "data_type": dtype,
                    }
                )
            col_opts.sort(key=lambda x: x["value"].lower())
            base["options"] = col_opts
            if not col_opts:
                base["hint"] = (
                    "No date/timestamp columns found in cached schema for this table. "
                    "Enter a column name manually if your warehouse uses an unusual type name."
                )
            return base

        base["hint"] = "Cursor field picker is only available for Salesforce and SQL flows."
        return base
