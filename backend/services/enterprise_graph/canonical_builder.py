"""Build canonical entities and cross-connector schema graph from semantic mappings."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from services.connector_metadata import store as cma_store
from services.connector_metadata.graph_builder import build_schema_graph
from services.enterprise_graph.config import link_confidence_threshold
from services.enterprise_graph.connector_ref import assessment_connector_ref
from services.enterprise_graph.inventory import SourceEntity, normalize_entity_name
from services.enterprise_graph.models import GraphMember

_DOMAIN_TAGS: Dict[str, str] = {
    "account": "party",
    "customer": "party",
    "contact": "party",
    "lead": "party",
    "opportunity": "sales",
    "order": "commerce",
    "product": "catalog",
    "invoice": "finance",
    "case": "support",
}

_COMMERCE_CHAIN_ORDER = ("party", "commerce", "catalog", "sales", "finance", "support")


def _entity_key(member_id: str, entity_id: str) -> Tuple[str, str]:
    return (member_id, entity_id)


def _short_entity_id(entity_id: str) -> str:
    eid = (entity_id or "").strip()
    if "::" in eid:
        eid = eid.split("::", 1)[-1]
    if "." in eid:
        eid = eid.split(".")[-1]
    return eid


def _title_name(norm: str, fallback: str) -> str:
    if not fallback and not norm:
        return "Entity"
    base = fallback or norm.replace("_", " ")
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), base.strip())


def _mapping_included(row: Dict[str, Any], *, min_confidence: float) -> bool:
    status = (row.get("status") or "").strip().lower()
    if status == "rejected":
        return False
    if status == "confirmed":
        return True
    conf = float(row.get("confidence") or 0)
    return status == "suggested" and conf >= min_confidence


def build_canonical_entities_from_mappings(
    *,
    graph_id: str,
    mappings: List[Dict[str, Any]],
    inventory: List[SourceEntity],
    min_confidence: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """One canonical_entities row per canonical_entity_id cluster."""
    thresh = min_confidence if min_confidence is not None else link_confidence_threshold()
    inv_by_key = {_entity_key(e.member_id, e.entity_id): e for e in inventory}

    clusters: Dict[str, List[Dict[str, Any]]] = {}
    for row in mappings:
        if not _mapping_included(row, min_confidence=thresh):
            continue
        cid = str(row.get("canonical_entity_id") or "").strip()
        if not cid:
            continue
        clusters.setdefault(cid, []).append(row)

    entities: List[Dict[str, Any]] = []
    for cid, rows in clusters.items():
        lineage: List[Dict[str, Any]] = []
        labels: List[str] = []
        column_names: Set[str] = set()
        schema_columns: List[Dict[str, Any]] = []
        seen_col_names: Set[str] = set()
        categorization: Dict[str, Any] = {}
        domain_tags: Set[str] = set()
        max_conf = 0.0
        has_confirmed = False

        for row in rows:
            mid = row["source_member_id"]
            eid = row["source_entity_id"]
            ent = inv_by_key.get(_entity_key(mid, eid))
            label = ent.label if ent else _short_entity_id(eid)
            labels.append(label)
            norm = ent.normalized_name if ent else normalize_entity_name(label)
            dom = _DOMAIN_TAGS.get(norm)
            if dom:
                domain_tags.add(dom)
            if ent:
                column_names.update(ent.column_names[:30])
                for col in ent.schema_columns or []:
                    if not isinstance(col, dict):
                        continue
                    cname = (col.get("name") or "").strip().lower()
                    if cname and cname not in seen_col_names:
                        seen_col_names.add(cname)
                        schema_columns.append(col)
                if ent.category and not categorization.get("category"):
                    categorization = {
                        "category": ent.category,
                        "category_description": ent.category_description,
                        "object_name": ent.object_name,
                        "confidence_score": ent.categorization_confidence,
                    }
            lineage.append(
                {
                    "member_id": mid,
                    "entity_id": eid,
                    "label": label,
                    "connector_type": ent.connector_type if ent else None,
                }
            )
            max_conf = max(max_conf, float(row.get("confidence") or 0))
            if (row.get("status") or "").lower() == "confirmed":
                has_confirmed = True

        preferred = next((l for l in labels if l), "Entity")
        norms = [normalize_entity_name(l) for l in labels]
        primary_norm = max(norms, key=len) if norms else "entity"
        name = _title_name(primary_norm, preferred)

        entities.append(
            {
                "id": cid,
                "graph_id": graph_id,
                "name": name,
                "domain_tags": sorted(domain_tags),
                "attributes": {
                    "columns": schema_columns[:40] if schema_columns else sorted(column_names)[:40],
                    "column_names": sorted(column_names)[:40],
                    "normalized_name": primary_norm,
                    "categorization": categorization,
                },
                "source_lineage": lineage,
                "version": 1,
                "status": "approved" if has_confirmed else "draft",
                "confidence": max_conf,
            }
        )

    entities.sort(key=lambda e: (_domain_sort_key(e.get("domain_tags") or []), e.get("name") or ""))
    return entities


def _domain_sort_key(tags: List[str]) -> int:
    if not tags:
        return len(_COMMERCE_CHAIN_ORDER)
    t = tags[0]
    try:
        return _COMMERCE_CHAIN_ORDER.index(t)
    except ValueError:
        return len(_COMMERCE_CHAIN_ORDER)


def build_entity_to_canonical_map(
    mappings: List[Dict[str, Any]],
    *,
    min_confidence: Optional[float] = None,
) -> Dict[Tuple[str, str], str]:
    thresh = min_confidence if min_confidence is not None else link_confidence_threshold()
    out: Dict[Tuple[str, str], str] = {}
    for row in mappings:
        if not _mapping_included(row, min_confidence=thresh):
            continue
        cid = str(row.get("canonical_entity_id") or "").strip()
        if not cid:
            continue
        out[_entity_key(row["source_member_id"], row["source_entity_id"])] = cid
    return out


def _graph_node_matches_entity(node_id: str, entity_id: str) -> bool:
    if node_id == entity_id:
        return True
    short_e = _short_entity_id(entity_id)
    short_n = _short_entity_id(node_id)
    return short_e == short_n or normalize_entity_name(short_e) == normalize_entity_name(short_n)


def _resolve_canonical_for_graph_node(
    member_id: str,
    node_id: str,
    entity_to_canonical: Dict[Tuple[str, str], str],
    inventory: List[SourceEntity],
) -> Optional[str]:
    if (member_id, node_id) in entity_to_canonical:
        return entity_to_canonical[(member_id, node_id)]
    for ent in inventory:
        if ent.member_id != member_id:
            continue
        if _graph_node_matches_entity(node_id, ent.entity_id):
            return entity_to_canonical.get(_entity_key(member_id, ent.entity_id))
    return None


def _entity_to_canonical_from_entities(
    canonical_entities: List[Dict[str, Any]],
    mappings: List[Dict[str, Any]],
) -> Dict[Tuple[str, str], str]:
    out = build_entity_to_canonical_map(mappings)
    for ce in canonical_entities:
        cid = ce["id"]
        for lin in ce.get("source_lineage") or []:
            mid = lin.get("member_id")
            eid = lin.get("entity_id")
            if mid and eid:
                out[_entity_key(mid, eid)] = cid
    return out


def build_canonical_graph(
    *,
    canonical_entities: List[Dict[str, Any]],
    mappings: List[Dict[str, Any]],
    inventory: List[SourceEntity],
    member_graphs: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """SchemaGraph-shaped dict: canonical nodes + FK edges projected from member CMA graphs."""
    entity_to_canonical = _entity_to_canonical_from_entities(canonical_entities, mappings)

    nodes: List[Dict[str, Any]] = []
    for ent in canonical_entities:
        tags = ent.get("domain_tags") or []
        nodes.append(
            {
                "id": ent["id"],
                "label": ent.get("name") or ent["id"],
                "node_type": tags[0] if tags else "entity",
                "canonical": True,
                "source_count": len(ent.get("source_lineage") or []),
                "status": ent.get("status") or "draft",
            }
        )

    edge_seen: Set[Tuple[str, str, str]] = set()
    edges: List[Dict[str, Any]] = []

    for member_id, graph in member_graphs.items():
        for ge in graph.get("edges") or []:
            src_node = ge.get("source_id") or ge.get("source")
            tgt_node = ge.get("target_id") or ge.get("target")
            if not src_node or not tgt_node:
                continue
            src_c = _resolve_canonical_for_graph_node(
                member_id, str(src_node), entity_to_canonical, inventory
            )
            tgt_c = _resolve_canonical_for_graph_node(
                member_id, str(tgt_node), entity_to_canonical, inventory
            )
            if not src_c or not tgt_c or src_c == tgt_c:
                continue
            label = ge.get("label") or ge.get("edge_type") or "references"
            key = (src_c, tgt_c, label)
            if key in edge_seen:
                continue
            edge_seen.add(key)
            edges.append(
                {
                    "source_id": src_c,
                    "target_id": tgt_c,
                    "label": label,
                    "edge_type": ge.get("edge_type") or "lookup",
                    "via_member_id": member_id,
                }
            )

    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "canonical_entity_count": len(canonical_entities),
            "as_of": datetime.now(timezone.utc).isoformat(),
            "scope": "canonical",
        },
    }


async def load_member_schema_graphs(
    *,
    user_id: str,
    members: List[GraphMember],
    inventory: List[SourceEntity],
) -> Dict[str, Dict[str, Any]]:
    """Latest or freshly built CMA graph per member, scoped to assessed entities."""
    graphs: Dict[str, Dict[str, Any]] = {}
    entity_ids_by_member: Dict[str, List[str]] = {}
    for ent in inventory:
        entity_ids_by_member.setdefault(ent.member_id, []).append(ent.entity_id)

    for member in members:
        ref = assessment_connector_ref(member.connector_type, member.connector_ref)
        entity_ids = entity_ids_by_member.get(member.id) or None
        try:
            latest = await cma_store.get_latest_graph(
                user_id, member.connector_type, ref
            )
            if latest and latest.get("graph_data"):
                graphs[member.id] = _parse_graph_data(latest["graph_data"])
                continue
            sg = await build_schema_graph(
                user_id=user_id,
                connector_type=member.connector_type,
                connector_ref=ref,
                graph_scope="neighborhood",
                entity_ids=entity_ids,
            )
            graphs[member.id] = sg.to_dict()
        except Exception:
            graphs[member.id] = {"nodes": [], "edges": [], "summary": {}}
    return graphs


def _parse_graph_data(val: Any) -> Dict[str, Any]:
    if isinstance(val, str):
        import json

        try:
            val = json.loads(val)
        except json.JSONDecodeError:
            return {"nodes": [], "edges": [], "summary": {}}
    if isinstance(val, dict):
        return val
    return {"nodes": [], "edges": [], "summary": {}}


def build_per_member_overlays(
    member_graphs: Dict[str, Dict[str, Any]],
    members: List[GraphMember],
) -> Dict[str, Dict[str, Any]]:
    overlays: Dict[str, Dict[str, Any]] = {}
    names = {m.id: m.display_name for m in members}
    for mid, graph in member_graphs.items():
        overlays[mid] = {
            "display_name": names.get(mid, mid),
            "graph": graph,
        }
    return overlays


def _augment_with_fk_neighbors(
    *,
    graph_id: str,
    entities: List[Dict[str, Any]],
    mappings: List[Dict[str, Any]],
    inventory: List[SourceEntity],
    member_graphs: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Add draft canonical nodes for FK neighbors of already-mapped entities."""
    import uuid as _uuid

    entity_to_canonical = build_entity_to_canonical_map(mappings)
    inv_by_key = {_entity_key(e.member_id, e.entity_id): e for e in inventory}
    existing_ids = {e["id"] for e in entities}
    extra: List[Dict[str, Any]] = []

    def ensure_node(member_id: str, node_id: str) -> Optional[str]:
        for ent in inventory:
            if ent.member_id != member_id:
                continue
            if _graph_node_matches_entity(node_id, ent.entity_id):
                key = _entity_key(member_id, ent.entity_id)
                if key in entity_to_canonical:
                    return entity_to_canonical[key]
                cid = str(_uuid.uuid4())
                entity_to_canonical[key] = cid
                inv_ent = inv_by_key.get(key)
                label = inv_ent.label if inv_ent else _short_entity_id(node_id)
                norm = inv_ent.normalized_name if inv_ent else normalize_entity_name(label)
                dom = _DOMAIN_TAGS.get(norm)
                extra.append(
                    {
                        "id": cid,
                        "graph_id": graph_id,
                        "name": _title_name(norm, label),
                        "domain_tags": [dom] if dom else [],
                        "attributes": {"normalized_name": norm, "inferred": True},
                        "source_lineage": [
                            {
                                "member_id": member_id,
                                "entity_id": ent.entity_id if inv_ent else node_id,
                                "label": label,
                            }
                        ],
                        "version": 1,
                        "status": "draft",
                        "confidence": 0.0,
                    }
                )
                return cid
        return None

    for member_id, graph in member_graphs.items():
        for ge in graph.get("edges") or []:
            src_node = str(ge.get("source_id") or "")
            tgt_node = str(ge.get("target_id") or "")
            if not src_node or not tgt_node:
                continue
            src_c = _resolve_canonical_for_graph_node(
                member_id, src_node, entity_to_canonical, inventory
            )
            tgt_c = _resolve_canonical_for_graph_node(
                member_id, tgt_node, entity_to_canonical, inventory
            )
            if src_c and not tgt_c:
                ensure_node(member_id, tgt_node)
            elif tgt_c and not src_c:
                ensure_node(member_id, src_node)

    merged = entities + [e for e in extra if e["id"] not in existing_ids]
    return merged


async def run_canonical_build(
    *,
    graph_id: str,
    user_id: str,
    members: List[GraphMember],
    mappings: List[Dict[str, Any]],
    inventory: List[SourceEntity],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """
    Returns (canonical_entities, canonical_graph, per_member_overlays).
    """
    entities = build_canonical_entities_from_mappings(
        graph_id=graph_id,
        mappings=mappings,
        inventory=inventory,
    )
    member_graphs = await load_member_schema_graphs(
        user_id=user_id,
        members=members,
        inventory=inventory,
    )
    entities = _augment_with_fk_neighbors(
        graph_id=graph_id,
        entities=entities,
        mappings=mappings,
        inventory=inventory,
        member_graphs=member_graphs,
    )
    canonical_graph = build_canonical_graph(
        canonical_entities=entities,
        mappings=mappings,
        inventory=inventory,
        member_graphs=member_graphs,
    )
    overlays = build_per_member_overlays(member_graphs, members)
    return entities, canonical_graph, overlays
