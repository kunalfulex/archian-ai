"""Read entity semantics for classification / discovery catalogs."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from db.connection import database


def _parse_json(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return None
    return None


def primary_from_semantics(semantics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract primary category + optional multi-label fields for catalog consumers."""
    if not semantics:
        return {}
    primary = semantics.get("primary") if isinstance(semantics.get("primary"), dict) else {}
    category = (primary.get("category") or semantics.get("document_type") or "").strip()
    description = (primary.get("description") or semantics.get("document_description") or "").strip()
    out: Dict[str, Any] = {}
    if category:
        out["category"] = category
    if description:
        out["description"] = description
    for key in ("domains", "processes", "synonyms"):
        vals = semantics.get(key)
        if isinstance(vals, list) and vals:
            out[key] = [str(v) for v in vals if str(v).strip()]
    if semantics.get("confidence") is not None:
        out["confidence"] = semantics.get("confidence")
    return out


async def fetch_semantics_map(
    *,
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
) -> Dict[str, Dict[str, Any]]:
    """entity_key -> parsed semantics JSON for ready rows."""
    rows = await database.fetch_all(
        """
        SELECT entity_key, semantics
        FROM connector_entity_semantics
        WHERE user_id = :user_id
          AND connector_type = :connector_type
          AND connector_instance_id = :connector_instance_id
          AND labeling_status = 'ready'
          AND semantics IS NOT NULL
        """,
        {
            "user_id": str(user_id),
            "connector_type": connector_type,
            "connector_instance_id": str(connector_instance_id),
        },
    )
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        r = dict(row)
        key = r.get("entity_key")
        if not key:
            continue
        sem = _parse_json(r.get("semantics"))
        if isinstance(sem, dict):
            out[str(key)] = sem
    return out


def entity_class_from_semantics(semantics: Optional[Dict[str, Any]]) -> Optional[str]:
    if not semantics or not isinstance(semantics, dict):
        return None
    ec = (semantics.get("entity_class") or "").strip().lower()
    return ec or None


def entity_label_from_row(
    entity_key: str,
    *,
    entity_display_name: Optional[str] = None,
    semantics: Optional[Dict[str, Any]] = None,
) -> str:
    """Label order: semantics category → display name → entity_key."""
    key = (entity_key or "").strip()
    primary = primary_from_semantics(semantics)
    category = (primary.get("category") or "").strip()
    if category and category.lower() not in ("unknown",) and category.lower() != key.lower():
        return category
    display = (entity_display_name or "").strip()
    if display:
        return display
    return key


async def list_entities_for_connector(
    *,
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
    spreadsheet_id: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """All catalog entities from connector_entity_semantics (pending + ready)."""
    inst = (connector_instance_id or "").strip()
    if not inst:
        return []
    params: Dict[str, Any] = {
        "user_id": str(user_id),
        "connector_type": connector_type,
        "connector_instance_id": inst,
        "limit": limit,
    }
    sid_filter = ""
    if spreadsheet_id and connector_type == "google_sheets":
        sid_filter = " AND entity_key LIKE :sid_prefix"
        params["sid_prefix"] = f"{spreadsheet_id.strip()}::%"
    rows = await database.fetch_all(
        f"""
        SELECT entity_key, entity_display_name, semantics, labeling_status
        FROM connector_entity_semantics
        WHERE user_id = :user_id
          AND connector_type = :connector_type
          AND connector_instance_id = :connector_instance_id
          {sid_filter}
        ORDER BY
          CASE labeling_status WHEN 'ready' THEN 0 WHEN 'processing' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
          labeled_at DESC NULLS LAST,
          entity_key ASC
        LIMIT :limit
        """,
        params,
    )
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        r = dict(row)
        key = (r.get("entity_key") or "").strip()
        if not key:
            continue
        sem = _parse_json(r.get("semantics"))
        sem_dict = sem if isinstance(sem, dict) else {}
        out.append(
            {
                "entity_key": key,
                "entity_display_name": r.get("entity_display_name"),
                "semantics": sem_dict,
                "labeling_status": r.get("labeling_status"),
                "label": entity_label_from_row(
                    key,
                    entity_display_name=r.get("entity_display_name"),
                    semantics=sem_dict,
                ),
            }
        )
    return out


async def resolve_connector_instance_id(
    user_id: str,
    connector_type: str,
    ctx: Dict[str, Any],
    classification: Dict[str, Any],
    *,
    role: str = "source",
) -> Optional[str]:
    """Resolve connector_instance_id for semantics lookups."""
    ct = (connector_type or "").strip().lower()
    id_key = "source_connector_id" if role == "source" else "destination_connector_id"
    cls_id = (classification.get(id_key) or "").strip()

    if ct == "salesforce":
        org_id = (ctx.get("org_id") or "").strip()
        if org_id:
            return org_id
        if cls_id:
            from services.connectors.salesforce_ref import resolve_salesforce_org_id

            resolved = await resolve_salesforce_org_id(str(user_id), cls_id)
            return resolved or cls_id
        return None

    if ct == "sql":
        cid = (ctx.get("connector_id") or cls_id or "").strip()
        return cid or None

    if ct == "google_sheets":
        cid = (ctx.get("connection_id") or cls_id or "").strip()
        if cid:
            return cid
        sid = (ctx.get("spreadsheet_id") or "").strip()
        if not sid:
            sid_key = "source_spreadsheet_id" if role == "source" else "destination_spreadsheet_id"
            sid = (classification.get(sid_key) or "").strip()
        if sid:
            row = await database.fetch_one(
                """
                SELECT google_sheets_connection_id::text AS connection_id
                FROM gsheets_extracted_schemas
                WHERE user_id = :user_id AND spreadsheet_id = :sid
                  AND COALESCE(is_deleted, false) = false
                ORDER BY extracted_at DESC NULLS LAST
                LIMIT 1
                """,
                {"user_id": str(user_id), "sid": sid},
            )
            if row:
                return str(dict(row).get("connection_id") or "") or None
        return None

    return None


def merge_catalog_entry(
    entry: Dict[str, Any],
    *,
    semantics: Optional[Dict[str, Any]],
    category_key: str = "category",
    description_key: str = "description",
) -> Dict[str, Any]:
    """Prefer ready semantics primary label; keep legacy fields as fallback."""
    enriched = dict(entry)
    primary = primary_from_semantics(semantics)
    if primary.get("category"):
        enriched[category_key] = primary["category"]
    if primary.get("description"):
        enriched[description_key] = primary["description"]
    for key in ("domains", "processes", "synonyms", "confidence"):
        if primary.get(key) is not None:
            enriched[key] = primary[key]
    if semantics:
        enriched["semantics"] = semantics
    return enriched
