"""Build schema graph (ERD) from cached connector metadata."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from db.connection import database
from services.connector_metadata.models import SchemaEdge, SchemaGraph, SchemaNode
from services.data_assessment.config import SF_QUICK_OBJECTS
from services.data_assessment.metadata_gap_analyzer import (
    analyze_salesforce_gaps,
    analyze_sql_gaps,
)
from services.data_assessment.models import EntityRef, GapFinding


async def build_schema_graph(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    graph_scope: str = "neighborhood",
    entity_ids: Optional[List[str]] = None,
    include_deleted: bool = True,
) -> SchemaGraph:
    if connector_type == "salesforce":
        return await _build_salesforce_graph(
            user_id=user_id,
            org_id=connector_ref["org_id"],
            graph_scope=graph_scope,
            entity_ids=entity_ids,
            include_deleted=include_deleted,
        )
    if connector_type == "sql":
        return await _build_sql_graph(
            user_id=user_id,
            connector_id=connector_ref["connector_id"],
            graph_scope=graph_scope,
            entity_ids=entity_ids,
            include_deleted=include_deleted,
        )
    if connector_type == "google_sheets":
        connection_id = (
            connector_ref.get("connection_id")
            or connector_ref.get("google_sheets_connection_id")
        )
        if not connection_id:
            raise KeyError("connection_id")
        return await _build_sheets_graph(
            user_id=user_id,
            connection_id=str(connection_id),
            entity_ids=entity_ids,
            include_deleted=include_deleted,
        )
    raise ValueError(f"Unsupported connector type: {connector_type}")


def _gap_severity_by_entity(gaps: List[GapFinding]) -> Dict[str, List[str]]:
    out: Dict[str, Set[str]] = {}
    for g in gaps:
        eid = g.entity_id
        if not eid or eid == "*":
            continue
        out.setdefault(eid, set()).add(g.severity)
    return {k: sorted(v) for k, v in out.items()}


def _resolve_scope_ids(
    all_ids: List[str],
    graph_scope: str,
    entity_ids: Optional[List[str]],
) -> Set[str]:
    if entity_ids:
        base = set(entity_ids)
    elif graph_scope == "core_crm":
        base = {i for i in all_ids if i in SF_QUICK_OBJECTS}
    elif graph_scope == "synced" or graph_scope == "full":
        base = set(all_ids)
    else:
        base = set(entity_ids or all_ids[:50])

    if graph_scope == "neighborhood":
        return base
    return base


async def _build_salesforce_graph(
    *,
    user_id: str,
    org_id: str,
    graph_scope: str,
    entity_ids: Optional[List[str]],
    include_deleted: bool,
) -> SchemaGraph:
    deleted_filter = "" if include_deleted else " AND COALESCE(is_deleted, FALSE) = FALSE"
    rows = await database.fetch_all(
        f"""
        SELECT object_api_name, label, COALESCE(is_deleted, FALSE) AS is_deleted
        FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id{deleted_filter}
        ORDER BY object_api_name
        """,
        {"user_id": user_id, "org_id": org_id},
    )
    all_ids = [dict(r)["object_api_name"] for r in rows or [] if dict(r).get("object_api_name")]
    scope_ids = _resolve_scope_ids(all_ids, graph_scope, entity_ids)
    synced_set = set(all_ids)

    field_rows = await database.fetch_all(
        """
        SELECT object_api_name, field_api_name, is_lookup, lookup_object_api_name
        FROM salesforce_object_fields
        WHERE user_id = :user_id AND org_id = :org_id
          AND is_lookup IS TRUE
        """,
        {"user_id": user_id, "org_id": org_id},
    )

    edges: List[SchemaEdge] = []
    inbound: Dict[str, int] = {}
    outbound: Dict[str, int] = {}

    for fr in field_rows or []:
        row = dict(fr)
        src = row.get("object_api_name")
        tgt = (row.get("lookup_object_api_name") or "").strip()
        fname = row.get("field_api_name") or "lookup"
        if not src or not tgt:
            continue
        if graph_scope == "neighborhood" and src not in scope_ids and tgt not in scope_ids:
            continue
        if graph_scope != "synced" and graph_scope != "full" and src not in scope_ids:
            continue
        sev = None
        if tgt not in synced_set:
            sev = "high"
        elif tgt not in scope_ids and graph_scope != "synced":
            sev = "medium"
        edges.append(
            SchemaEdge(
                source_id=src,
                target_id=tgt,
                label=fname,
                edge_type="lookup",
                gap_severity=sev,
            )
        )
        outbound[src] = outbound.get(src, 0) + 1
        inbound[tgt] = inbound.get(tgt, 0) + 1

    node_ids: Set[str] = set(scope_ids)
    for e in edges:
        node_ids.add(e.source_id)
        node_ids.add(e.target_id)

    entities = [EntityRef(entity_id=i, label=i) for i in sorted(node_ids)]
    gaps = await analyze_salesforce_gaps(user_id, org_id, entities)
    gap_by_entity = _gap_severity_by_entity(gaps)

    nodes: List[SchemaNode] = []
    row_by_id = {dict(r)["object_api_name"]: dict(r) for r in rows or []}
    for nid in sorted(node_ids):
        meta = row_by_id.get(nid, {})
        out_c = outbound.get(nid, 0)
        in_c = inbound.get(nid, 0)
        orphan_kind = None
        if meta.get("is_deleted"):
            orphan_kind = "removed"
        elif out_c == 0 and in_c == 0:
            orphan_kind = "structural"
        nodes.append(
            SchemaNode(
                id=nid,
                label=meta.get("label") or nid,
                node_type="object",
                orphan_kind=orphan_kind,
                is_deleted=bool(meta.get("is_deleted")),
                gap_severities=gap_by_entity.get(nid, []),
            )
        )

    return SchemaGraph(
        nodes=nodes,
        edges=edges,
        summary={
            "connector_type": "salesforce",
            "org_id": org_id,
            "scope": graph_scope,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "as_of": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _build_sql_graph(
    *,
    user_id: str,
    connector_id: str,
    graph_scope: str,
    entity_ids: Optional[List[str]],
    include_deleted: bool,
) -> SchemaGraph:
    deleted_filter = "" if include_deleted else " AND COALESCE(is_deleted, FALSE) = FALSE"
    rows = await database.fetch_all(
        f"""
        SELECT table_name, schema_data, COALESCE(is_deleted, FALSE) AS is_deleted
        FROM sql_extracted_schemas
        WHERE user_id = :user_id AND connector_id = :connector_id{deleted_filter}
        ORDER BY table_name
        """,
        {"user_id": user_id, "connector_id": connector_id},
    )
    import json

    all_ids: List[str] = []
    schema_by_table: Dict[str, Dict[str, Any]] = {}
    deleted_by: Dict[str, bool] = {}
    for r in rows or []:
        row = dict(r)
        tbl = row.get("table_name")
        if not tbl:
            continue
        all_ids.append(tbl)
        deleted_by[tbl] = bool(row.get("is_deleted"))
        raw = row.get("schema_data")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        schema_by_table[tbl] = raw if isinstance(raw, dict) else {}

    scope_ids = _resolve_scope_ids(all_ids, graph_scope, entity_ids)
    synced_set = set(all_ids)
    edges: List[SchemaEdge] = []
    inbound: Dict[str, int] = {}
    outbound: Dict[str, int] = {}

    for tbl, payload in schema_by_table.items():
        if tbl not in scope_ids and graph_scope not in ("synced", "full"):
            continue
        for fk in payload.get("foreign_keys") or []:
            if not isinstance(fk, dict):
                continue
            ref = (fk.get("referenced_table") or fk.get("foreign_table_name") or "").strip()
            col = (fk.get("column") or fk.get("column_name") or "").strip()
            if not ref:
                continue
            if graph_scope == "neighborhood" and tbl not in scope_ids and ref not in scope_ids:
                continue
            sev = "high" if ref not in synced_set else None
            edges.append(
                SchemaEdge(
                    source_id=tbl,
                    target_id=ref,
                    label=col or "fk",
                    edge_type="foreign_key",
                    gap_severity=sev,
                )
            )
            outbound[tbl] = outbound.get(tbl, 0) + 1
            inbound[ref] = inbound.get(ref,  0) + 1

    node_ids: Set[str] = set(scope_ids)
    for e in edges:
        node_ids.add(e.source_id)
        node_ids.add(e.target_id)

    entities = [EntityRef(entity_id=i, label=i) for i in sorted(node_ids)]
    gaps = await analyze_sql_gaps(user_id, connector_id, entities)
    gap_by_entity = _gap_severity_by_entity(gaps)

    nodes: List[SchemaNode] = []
    for nid in sorted(node_ids):
        out_c = outbound.get(nid, 0)
        in_c = inbound.get(nid, 0)
        orphan_kind = None
        if deleted_by.get(nid):
            orphan_kind = "removed"
        elif out_c == 0 and in_c == 0:
            orphan_kind = "structural"
        nodes.append(
            SchemaNode(
                id=nid,
                label=nid,
                node_type="table",
                orphan_kind=orphan_kind,
                is_deleted=deleted_by.get(nid, False),
                gap_severities=gap_by_entity.get(nid, []),
            )
        )

    return SchemaGraph(
        nodes=nodes,
        edges=edges,
        summary={
            "connector_type": "sql",
            "connector_id": connector_id,
            "scope": graph_scope,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "as_of": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _build_sheets_graph(
    *,
    user_id: str,
    connection_id: str,
    entity_ids: Optional[List[str]],
    include_deleted: bool,
) -> SchemaGraph:
    deleted_filter = "" if include_deleted else " AND COALESCE(is_deleted, FALSE) = FALSE"
    rows = await database.fetch_all(
        f"""
        SELECT spreadsheet_id, sheet_name, COALESCE(is_deleted, FALSE) AS is_deleted
        FROM gsheets_extracted_schemas
        WHERE user_id = :user_id AND google_sheets_connection_id = :conn_id{deleted_filter}
        """,
        {"user_id": user_id, "conn_id": connection_id},
    )
    nodes: List[SchemaNode] = []
    for r in rows or []:
        row = dict(r)
        sid = row.get("spreadsheet_id")
        sheet = row.get("sheet_name")
        if not sid or not sheet:
            continue
        eid = f"{sid}::{sheet}"
        if entity_ids and eid not in entity_ids:
            continue
        nodes.append(
            SchemaNode(
                id=eid,
                label=sheet,
                node_type="sheet",
                orphan_kind="structural",
                is_deleted=bool(row.get("is_deleted")),
            )
        )
    return SchemaGraph(
        nodes=nodes,
        edges=[],
        summary={
            "connector_type": "google_sheets",
            "connection_id": connection_id,
            "scope": "synced",
            "node_count": len(nodes),
            "edge_count": 0,
            "as_of": datetime.now(timezone.utc).isoformat(),
        },
    )
