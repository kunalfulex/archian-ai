"""Load cross-connector hints from saved migration flow field mappings."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from db.connection import database
from services.enterprise_graph.connector_ref import assessment_connector_ref
from services.enterprise_graph.inventory import normalize_entity_name
from services.enterprise_graph.models import GraphMember


async def load_flow_mapping_priors(
    user_id: str,
    members: List[GraphMember],
) -> List[Dict[str, Any]]:
    """Entity-level pairs inferred from historical flow mappings."""
    if len(members) < 2:
        return []

    rows = await database.fetch_all(
        """
        SELECT DISTINCT
            mf2.id AS flow_id,
            es.document_type,
            es.table_name,
            es.extracted_schema_type,
            es.connector_id,
            mf2.org_id,
            mf2.destination_type,
            fm.salesforce_object
        FROM migration_flows mf2
        JOIN extracted_schemas es ON es.migration_flow_id = mf2.id
        JOIN field_mappings fm ON fm.migration_flow_id = mf2.id
        WHERE mf2.user_id = :user_id
          AND fm.salesforce_object IS NOT NULL
          AND fm.salesforce_object != ''
        LIMIT 3000
        """,
        {"user_id": user_id},
    )

    priors: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    for r in rows or []:
        row = dict(r)
        src_object = (row.get("table_name") or row.get("document_type") or "").strip()
        dest_object = (row.get("salesforce_object") or "").strip()
        if not src_object or not dest_object:
            continue
        src_member = _resolve_source_member(row, members)
        dest_member = _resolve_dest_member(row, members)
        if not src_member or not dest_member or src_member.id == dest_member.id:
            continue
        src_norm = normalize_entity_name(src_object)
        dest_norm = normalize_entity_name(dest_object)
        key = (src_member.id, src_norm, dest_member.id, dest_norm)
        if key in seen:
            continue
        seen.add(key)
        priors.append(
            {
                "source_member_id": src_member.id,
                "source_entity_id": src_object,
                "target_member_id": dest_member.id,
                "target_entity_id": dest_object,
                "confidence": 0.92,
                "mapping_kind": "flow_prior",
                "evidence": {"flow_id": str(row.get("flow_id"))},
            }
        )
    return priors


def _resolve_source_member(row: Dict[str, Any], members: List[GraphMember]) -> Optional[GraphMember]:
    schema_type = (row.get("extracted_schema_type") or "").strip().lower()
    connector_id = str(row.get("connector_id") or "").strip()

    if schema_type == "sql" and connector_id:
        for m in members:
            if m.connector_type != "sql":
                continue
            ref = assessment_connector_ref(m.connector_type, m.connector_ref)
            if str(ref.get("connector_id") or "") == connector_id:
                return m
    if schema_type in ("gsheets", "google_sheets"):
        for m in members:
            if m.connector_type == "google_sheets":
                return m
    doc = (row.get("document_type") or row.get("table_name") or "").strip()
    if doc:
        dn = normalize_entity_name(doc)
        for m in members:
            if dn and dn in normalize_entity_name(m.display_name):
                return m
    return members[0] if len(members) == 1 else None


def _resolve_dest_member(row: Dict[str, Any], members: List[GraphMember]) -> Optional[GraphMember]:
    dest_type = (row.get("destination_type") or "").strip().lower()
    org_id = str(row.get("org_id") or "").strip()
    if dest_type == "salesforce" or org_id:
        for m in members:
            if m.connector_type != "salesforce":
                continue
            ref = assessment_connector_ref(m.connector_type, m.connector_ref)
            if org_id and str(ref.get("org_id") or "") == org_id:
                return m
        for m in members:
            if m.connector_type == "salesforce":
                return m
    if dest_type == "sql":
        for m in members:
            if m.connector_type == "sql":
                return m
    for m in members:
        if m.connector_type == "salesforce":
            return m
    return None
