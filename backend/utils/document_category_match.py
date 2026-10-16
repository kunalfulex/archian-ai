"""
Normalize and reconcile extraction document_type / document_description
against destination catalog metadata (Salesforce, SQL, Google Sheets).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from db.connection import database


def normalize_doc_label(value: Optional[str]) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"\s+", " ", s)
    if s.endswith("."):
        s = s[:-1].strip()
    return s


def _coerce_json_dict(raw: Any) -> Dict[str, Any]:
    """Parse JSON/JSONB into a dict; JSON null and non-objects become {}."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


@dataclass
class CategoryReconcileResult:
    canonical_type: str
    canonical_description: str
    matched_target: Optional[str] = None
    match_tier: str = "raw"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_document_type": self.canonical_type,
            "canonical_document_description": self.canonical_description,
            "matched_object_or_table": self.matched_target,
            "category_match_tier": self.match_tier,
        }


async def _resolve_salesforce_org_id(user_id: Any, destination_name: Optional[str]) -> Optional[str]:
    if destination_name and str(destination_name).strip() and str(destination_name).strip().lower() != "unknown":
        row = await database.fetch_one(
            """
            SELECT org_id FROM salesforce_tokens
            WHERE user_id = :user_id
              AND LOWER(TRIM(org_display_name)) = LOWER(TRIM(:destination_name))
            ORDER BY is_default DESC NULLS LAST, issued_at DESC
            LIMIT 1
            """,
            values={"user_id": str(user_id), "destination_name": str(destination_name).strip()},
        )
        if row:
            return str(dict(row).get("org_id") or "")
    row = await database.fetch_one(
        """
        SELECT org_id FROM salesforce_tokens
        WHERE user_id = :user_id
        ORDER BY is_default DESC NULLS LAST, issued_at DESC
        LIMIT 1
        """,
        values={"user_id": str(user_id)},
    )
    return str(dict(row).get("org_id") or "") if row else None


async def _reconcile_salesforce(
    user_id: Any,
    destination_name: Optional[str],
    document_type: str,
    document_description: str,
    flat_data_keys: Set[str],
) -> CategoryReconcileResult:
    dt = normalize_doc_label(document_type)
    dd = normalize_doc_label(document_description)
    org_id = await _resolve_salesforce_org_id(user_id, destination_name)
    if not org_id:
        return CategoryReconcileResult(
            document_type or "",
            document_description or "",
            match_tier="no_org",
        )

    rows = await database.fetch_all(
        """
        SELECT object_api_name, label, category, category_description
        FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
          AND category IS NOT NULL
        """,
        values={"user_id": str(user_id), "org_id": org_id},
    )

    exact: List[Any] = []
    type_only: List[Any] = []
    for r in rows or []:
        row = dict(r)
        cat = normalize_doc_label(row.get("category"))
        cat_desc = normalize_doc_label(row.get("category_description"))
        if cat == dt and cat_desc == dd:
            exact.append(row)
        elif cat == dt and dt:
            type_only.append(row)

    if exact:
        pick = exact[0]
        return CategoryReconcileResult(
            str(pick.get("category") or document_type),
            str(pick.get("category_description") or document_description),
            matched_target=str(pick.get("object_api_name") or ""),
            match_tier="exact",
        )
    if type_only:
        pick = type_only[0]
        return CategoryReconcileResult(
            str(pick.get("category") or document_type),
            str(pick.get("category_description") or document_description),
            matched_target=str(pick.get("object_api_name") or ""),
            match_tier="type_only",
        )

    if dt:
        fb = await database.fetch_one(
            """
            SELECT object_api_name, label, category, category_description
            FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id
              AND (LOWER(TRIM(label)) = :dt OR LOWER(TRIM(object_api_name)) = :dt)
            LIMIT 1
            """,
            values={"user_id": str(user_id), "org_id": org_id, "dt": dt},
        )
        if fb:
            pick = dict(fb)
            return CategoryReconcileResult(
                str(pick.get("category") or document_type),
                str(pick.get("category_description") or document_description),
                matched_target=str(pick.get("object_api_name") or ""),
                match_tier="label_fallback",
            )

    return CategoryReconcileResult(
        document_type or "",
        document_description or "",
        match_tier="raw",
    )


def _parse_gsheets_headers(header_schema_raw: Any) -> Set[str]:
    names: Set[str] = set()
    if not header_schema_raw:
        return names
    try:
        data = json.loads(header_schema_raw) if isinstance(header_schema_raw, str) else header_schema_raw
    except (json.JSONDecodeError, TypeError):
        return names
    if isinstance(data, dict):
        headers_list = data.get("headers")
        if isinstance(headers_list, list):
            for item in headers_list:
                if isinstance(item, dict):
                    cn = (item.get("name") or item.get("column_name") or "").strip()
                    if cn:
                        names.add(cn.lower())
                elif isinstance(item, str) and item.strip():
                    names.add(item.strip().lower())
        else:
            for k in data.keys():
                if k and not str(k).startswith("_") and k != "headers":
                    names.add(str(k).lower())
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                cn = (item.get("column_name") or item.get("name") or "").strip()
                if cn:
                    names.add(cn.lower())
            elif isinstance(item, str) and item.strip():
                names.add(item.strip().lower())
    return names


async def _reconcile_google_sheets(
    user_id: Any,
    destination_name: Optional[str],
    document_type: str,
    document_description: str,
    flat_data_keys: Set[str],
) -> CategoryReconcileResult:
    dt = normalize_doc_label(document_type)
    dd = normalize_doc_label(document_description)

    query = """
        SELECT spreadsheet_id, spreadsheet_name, sheet_name, header_schema, ai_categorization
        FROM gsheets_extracted_schemas
        WHERE user_id = :user_id
    """
    values: Dict[str, Any] = {"user_id": str(user_id)}
    if destination_name and str(destination_name).strip().lower() != "unknown":
        query += " AND LOWER(TRIM(spreadsheet_name)) = LOWER(TRIM(:sn))"
        values["sn"] = str(destination_name).strip()

    sheets = await database.fetch_all(query, values=values)
    exact: List[Dict[str, Any]] = []
    type_only: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []

    for s in sheets or []:
        row = dict(s)
        all_rows.append(row)
        ai_cat = _coerce_json_dict(row.get("ai_categorization"))
        category = normalize_doc_label(ai_cat.get("document_type"))
        category_desc = normalize_doc_label(ai_cat.get("document_description"))
        if category == dt and category_desc == dd and dt:
            exact.append(row)
        elif category == dt and dt:
            type_only.append(row)

    if exact:
        pick = exact[0]
        ai_cat = _coerce_json_dict(pick.get("ai_categorization"))
        return CategoryReconcileResult(
            str(ai_cat.get("document_type") or document_type),
            str(ai_cat.get("document_description") or document_description),
            matched_target=str(pick.get("sheet_name") or ""),
            match_tier="exact",
        )
    if type_only:
        pick = type_only[0]
        ai_cat = _coerce_json_dict(pick.get("ai_categorization"))
        return CategoryReconcileResult(
            str(ai_cat.get("document_type") or document_type),
            str(ai_cat.get("document_description") or document_description),
            matched_target=str(pick.get("sheet_name") or ""),
            match_tier="type_only",
        )

    keys_lower = {str(k).lower() for k in flat_data_keys if k and not str(k).startswith("_")}
    if keys_lower and all_rows:
        best = None
        best_score = 0
        for row in all_rows:
            hdr = _parse_gsheets_headers(row.get("header_schema"))
            score = len(keys_lower & hdr)
            if score > best_score:
                best_score = score
                best = row
        if best and best_score >= 1:
            ai_cat = _coerce_json_dict(best.get("ai_categorization"))
            return CategoryReconcileResult(
                str(ai_cat.get("document_type") or document_type),
                str(ai_cat.get("document_description") or document_description),
                matched_target=str(best.get("sheet_name") or ""),
                match_tier="column_overlap",
            )

    if destination_name and str(destination_name).strip().lower() != "unknown" and all_rows:
        sn = normalize_doc_label(destination_name)
        by_name = [
            r
            for r in all_rows
            if normalize_doc_label(r.get("spreadsheet_name")) == sn
        ]
        if by_name:
            pick = by_name[0]
            ai_cat = _coerce_json_dict(pick.get("ai_categorization"))
            return CategoryReconcileResult(
                str(ai_cat.get("document_type") or document_type),
                str(ai_cat.get("document_description") or document_description),
                matched_target=str(pick.get("sheet_name") or ""),
                match_tier="destination_name",
            )

    return CategoryReconcileResult(document_type or "", document_description or "", match_tier="raw")


async def _reconcile_sql(
    user_id: Any,
    destination_name: Optional[str],
    document_type: str,
    document_description: str,
    flat_data: Dict[str, Any],
) -> CategoryReconcileResult:
    from handlers.sql_handler import _select_tables_for_mapping

    connector = None
    if destination_name and str(destination_name).strip().lower() != "unknown":
        connector = await database.fetch_one(
            """
            SELECT id FROM sql_connectors
            WHERE user_id = :user_id AND LOWER(TRIM(name)) = LOWER(TRIM(:name))
            LIMIT 1
            """,
            values={"user_id": str(user_id), "name": str(destination_name).strip()},
        )
    if not connector:
        connector = await database.fetch_one(
            """
            SELECT id FROM sql_connectors
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 1
            """,
            values={"user_id": str(user_id)},
        )
    if not connector:
        return CategoryReconcileResult(document_type or "", document_description or "", match_tier="no_connector")

    connector_id = str(dict(connector).get("id") or "")
    tables = await database.fetch_all(
        """
        SELECT table_name, schema_data, ai_categorization, connector_id
        FROM sql_extracted_schemas
        WHERE user_id = :user_id AND connector_id = :cid
        """,
        values={"user_id": str(user_id), "cid": connector_id},
    )
    matched = _select_tables_for_mapping(
        [dict(t) for t in tables or []],
        flat_data or {},
        document_type,
        document_description,
    )
    if not matched:
        return CategoryReconcileResult(document_type or "", document_description or "", match_tier="raw")

    pick = dict(matched[0])
    tier = "exact"
    ai_cat = _coerce_json_dict(pick.get("ai_categorization"))
    dt_n = normalize_doc_label(document_type)
    dd_n = normalize_doc_label(document_description)
    if normalize_doc_label(ai_cat.get("document_type")) == dt_n:
        if normalize_doc_label(ai_cat.get("document_description")) != dd_n:
            tier = "type_only"
    else:
        tier = "column_overlap"

    return CategoryReconcileResult(
        str(ai_cat.get("document_type") or document_type),
        str(ai_cat.get("document_description") or document_description),
        matched_target=str(pick.get("table_name") or ""),
        match_tier=tier,
    )


async def reconcile_document_category(
    user_id: Any,
    destination: str,
    destination_name: Optional[str],
    document_type: Optional[str],
    document_description: Optional[str],
    flat_data: Optional[Dict[str, Any]] = None,
) -> CategoryReconcileResult:
    """
    Align extraction labels with destination catalog entries.
    """
    doc_type = (document_type or "").strip()
    doc_desc = (document_description or "").strip()
    dest = (destination or "unknown").lower().strip()
    keys: Set[str] = set()
    if isinstance(flat_data, dict):
        keys = {str(k) for k in flat_data.keys() if k}

    if dest == "salesforce":
        return await _reconcile_salesforce(user_id, destination_name, doc_type, doc_desc, keys)
    if dest == "sql":
        return await _reconcile_sql(user_id, destination_name, doc_type, doc_desc, flat_data or {})
    if dest in ("google_sheets", "gsheets"):
        return await _reconcile_google_sheets(user_id, destination_name, doc_type, doc_desc, keys)

    return CategoryReconcileResult(doc_type, doc_desc, match_tier="unsupported_dest")


def apply_reconcile_to_tuple(
    document_type: Optional[str],
    document_description: Optional[str],
    reconcile: CategoryReconcileResult,
) -> tuple:
    """Return (canonical_type, canonical_description) for handler return values."""
    ct = reconcile.canonical_type or document_type or ""
    cd = reconcile.canonical_description or document_description or ""
    return ct, cd


def destination_requires_pick(
    destination: Optional[str],
    destination_name: Optional[str] = None,
) -> bool:
    """True when user must name a concrete connector before map/export (type alone is not enough)."""
    d = (destination or "unknown").strip().lower()
    if not d or d == "unknown":
        return True
    dn = (destination_name or "").strip()
    return not dn or dn.lower() == "unknown"


def _flat_data_sample(flat_data: Any) -> Dict[str, Any]:
    if isinstance(flat_data, dict):
        return flat_data
    if isinstance(flat_data, list) and flat_data and isinstance(flat_data[0], dict):
        return flat_data[0]
    return {}


async def enrich_source_extraction_metadata(
    user_id: Any,
    destination: Optional[str],
    destination_name: Optional[str],
    document_type: Optional[str],
    document_description: Optional[str],
    flat_data: Any = None,
    *,
    source_type: Optional[str] = None,
    source_name: Optional[str] = None,
    extraction_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
  Metadata for extraction_completed / SSE.

  Labels describe the **source** extract only. Destination catalog matching happens at map time.
    """
    doc_type = (document_type or "").strip()
    doc_desc = (document_description or "").strip()
    meta = dict(extraction_metadata or {})

    out: Dict[str, Any] = {
        "requires_destination": destination_requires_pick(destination, destination_name),
        "source_document_type": doc_type,
        "source_document_description": doc_desc,
        "canonical_document_type": doc_type,
        "canonical_document_description": doc_desc,
        "category_match_tier": "source_labels",
        "matched_object_or_table": meta.get("source_object_api_name"),
    }

    if destination_requires_pick(destination, destination_name):
        out["category_match_tier"] = "pending_destination"
        return out

    # Optional: align source labels with source connector catalog (not destination).
    st = (source_type or "").strip().lower()
    catalog_dest = st if st in ("salesforce", "sql", "google_sheets", "gsheets") else None
    catalog_name = (source_name or "").strip() or None
    if catalog_dest and catalog_name:
        try:
            rec = await reconcile_document_category(
                user_id,
                "google_sheets" if catalog_dest == "gsheets" else catalog_dest,
                catalog_name,
                doc_type,
                doc_desc,
                _flat_data_sample(flat_data),
            )
            ct, cd = apply_reconcile_to_tuple(doc_type, doc_desc, rec)
            out["canonical_document_type"] = ct
            out["canonical_document_description"] = cd
            out["source_document_type"] = ct
            out["source_document_description"] = cd
            out["category_match_tier"] = rec.match_tier
            if rec.matched_target:
                out["matched_object_or_table"] = rec.matched_target
                meta.setdefault("source_object_api_name", rec.matched_target)
        except Exception:
            pass

    if meta.get("semantic_data_profile"):
        out["semantic_data_profile"] = meta["semantic_data_profile"]
    return out


async def enrich_extraction_category_fields(
    user_id: Any,
    destination: Optional[str],
    destination_name: Optional[str],
    document_type: Optional[str],
    document_description: Optional[str],
    flat_data: Any = None,
) -> Dict[str, Any]:
    """
    Legacy wrapper — extraction completion should use enrich_source_extraction_metadata.
    Mapping-time destination reconcile uses reconcile_document_category directly.
    """
    return await enrich_source_extraction_metadata(
        user_id,
        destination,
        destination_name,
        document_type,
        document_description,
        flat_data,
    )
