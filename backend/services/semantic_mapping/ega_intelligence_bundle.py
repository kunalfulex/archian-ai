"""Shared semantic bundle for EGA LLM stages (mapping, lake, warehouse)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.models import GraphMember
from services.enterprise_graph.validation import connector_id_from_ref
from services.labeling.catalog_helpers import (
    entity_class_from_semantics,
    fetch_semantics_map,
    primary_from_semantics,
)
from services.semantic_mapping.config import ega_max_entities_per_member, ega_max_columns_per_entity


def member_option_key(member: GraphMember) -> str:
    cid = connector_id_from_ref(member.connector_type, member.connector_ref or {})
    return f"{member.connector_type}:{cid}"


def parse_cross_map_member_pairs(
    graph_settings: Optional[Dict[str, Any]],
    members: List[GraphMember],
) -> List[Tuple[str, str]]:
    """Resolve canvas cross_map edges to (from_member_id, to_member_id)."""
    settings = graph_settings or {}
    layout = settings.get("layout") or {}
    nodes = {n.get("id"): n for n in (layout.get("nodes") or []) if n.get("id")}
    members_by_key = {member_option_key(m): m for m in members}
    pairs: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()

    for edge in layout.get("edges") or []:
        if (edge.get("linkType") or "").strip().lower() != "cross_map":
            continue
        from_node = nodes.get(edge.get("fromNodeId"))
        to_node = nodes.get(edge.get("toNodeId"))
        if not from_node or not to_node:
            continue
        from_member = members_by_key.get(from_node.get("optionKey") or "")
        to_member = members_by_key.get(to_node.get("optionKey") or "")
        if not from_member or not to_member or from_member.id == to_member.id:
            continue
        key = tuple(sorted((from_member.id, to_member.id)))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((from_member.id, to_member.id))
    return pairs


def _compact_columns(entity: SourceEntity, limit: int) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for name in (entity.column_names or [])[:limit]:
        if name:
            out.append({"name": str(name)})
    if not out:
        for col in (entity.schema_columns or [])[:limit]:
            if isinstance(col, dict) and col.get("name"):
                out.append({"name": str(col["name"])})
    return out


def _compact_entity(
    entity: SourceEntity,
    *,
    member_name: str,
    semantics: Optional[Dict[str, Any]],
    col_limit: int,
) -> Dict[str, Any]:
    primary = primary_from_semantics(semantics)
    entity_class = entity_class_from_semantics(semantics) or "business"
    return {
        "member_id": entity.member_id,
        "member_name": member_name,
        "connector_type": entity.connector_type,
        "entity_id": entity.entity_id,
        "display_label": entity.label,
        "linking_name": entity.object_name or primary.get("category") or entity.label,
        "entity_class": entity_class,
        "semantics": {
            "category": primary.get("category") or entity.category or "",
            "description": primary.get("description") or entity.category_description or "",
            "domains": primary.get("domains") or [],
            "synonyms": primary.get("synonyms") or [],
            "confidence": primary.get("confidence"),
            "entity_class": entity_class,
        },
        "columns": _compact_columns(entity, col_limit),
    }


async def build_ega_intelligence_bundle(
    *,
    graph_name: str,
    graph_settings: Optional[Dict[str, Any]],
    members: List[GraphMember],
    inventory: List[SourceEntity],
    user_id: str,
    semantic_mappings: Optional[List[Dict[str, Any]]] = None,
    canonical_entities: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compact JSON-serializable bundle for EGA LLM prompts."""
    col_limit = ega_max_columns_per_entity()
    ent_limit = ega_max_entities_per_member()
    member_by_id = {m.id: m for m in members}
    semantics_by_member: Dict[str, Dict[str, Dict[str, Any]]] = {}

    source_members = [
        m
        for m in members
        if (m.role or "").strip().lower() not in ("warehouse", "lake")
    ]

    for m in source_members:
        inst = connector_id_from_ref(m.connector_type, m.connector_ref or {})
        if not inst:
            semantics_by_member[m.id] = {}
            continue
        semantics_by_member[m.id] = await fetch_semantics_map(
            user_id=user_id,
            connector_type=m.connector_type,
            connector_instance_id=inst,
        )

    entities_by_member: Dict[str, List[Dict[str, Any]]] = {}
    for ent in inventory:
        member = member_by_id.get(ent.member_id)
        if not member or (member.role or "").lower() in ("warehouse", "lake"):
            continue
        sem = (semantics_by_member.get(ent.member_id) or {}).get(ent.entity_id)
        payload = _compact_entity(
            ent,
            member_name=member.display_name or member.connector_type,
            semantics=sem,
            col_limit=col_limit,
        )
        entities_by_member.setdefault(ent.member_id, []).append(payload)

    for mid in list(entities_by_member.keys()):
        entities_by_member[mid] = entities_by_member[mid][:ent_limit]

    cross_map_pairs = [
        {"from_member_id": a, "to_member_id": b}
        for a, b in parse_cross_map_member_pairs(graph_settings, members)
    ]

    return {
        "graph_name": graph_name,
        "members": [
            {
                "id": m.id,
                "display_name": m.display_name,
                "connector_type": m.connector_type,
                "role": m.role,
            }
            for m in source_members
        ],
        "entities_by_member": entities_by_member,
        "cross_map_intent": cross_map_pairs,
        "existing_mappings": [
            {
                "canonical_entity_id": row.get("canonical_entity_id"),
                "member_id": row.get("source_member_id"),
                "entity_id": row.get("source_entity_id"),
                "status": row.get("status"),
                "confidence": row.get("confidence"),
            }
            for row in (semantic_mappings or [])
            if (row.get("status") or "").lower() != "rejected"
        ][:80],
        "canonical_entities": [
            {
                "id": e.get("id"),
                "name": e.get("name"),
                "domain_tags": e.get("domain_tags") or [],
            }
            for e in (canonical_entities or [])[:60]
        ],
    }


def bundle_to_prompt_json(bundle: Dict[str, Any]) -> str:
    return json.dumps(bundle, indent=2, default=str)
