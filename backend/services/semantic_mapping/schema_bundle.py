"""Compact schema payload for warehouse LLM prompts."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services.enterprise_graph.models import GraphMember
from services.semantic_mapping.config import warehouse_max_columns_per_table


def _compact_columns(attrs: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    cols = attrs.get("columns") or []
    out: List[Dict[str, Any]] = []
    for c in cols[:limit]:
        if isinstance(c, dict) and c.get("name"):
            out.append(
                {
                    "name": c.get("name"),
                    "type": c.get("data_type") or c.get("type") or "STRING",
                }
            )
        elif isinstance(c, str) and c.strip():
            out.append({"name": c.strip(), "type": "STRING"})
    return out


def _compact_entity(ent: Dict[str, Any], col_limit: int) -> Dict[str, Any]:
    attrs = ent.get("attributes") or {}
    cat = attrs.get("categorization") or {}
    lineage = ent.get("source_lineage") or []
    return {
        "id": ent.get("id"),
        "name": ent.get("name"),
        "domain_tags": ent.get("domain_tags") or [],
        "category": cat.get("category") or "",
        "category_description": cat.get("category_description") or "",
        "columns": _compact_columns(attrs, col_limit),
        "sources": [
            {
                "member_id": lin.get("member_id"),
                "entity_id": lin.get("entity_id"),
                "label": lin.get("label"),
                "connector_type": lin.get("connector_type"),
            }
            for lin in lineage[:8]
        ],
    }


def build_schema_bundle(
    *,
    graph_name: str,
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    canonical_entities: List[Dict[str, Any]],
    canonical_graph: Dict[str, Any],
    semantic_mappings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    col_limit = warehouse_max_columns_per_table()
    entities = [_compact_entity(e, col_limit) for e in canonical_entities[:80]]

    member_summaries = [
        {
            "id": m.id,
            "display_name": m.display_name,
            "connector_type": m.connector_type,
            "role": m.role,
        }
        for m in members
        if (m.role or "").lower() != "warehouse"
    ]

    edges = []
    for e in (canonical_graph.get("edges") or [])[:120]:
        edges.append(
            {
                "from": e.get("source_id"),
                "to": e.get("target_id"),
                "label": e.get("label"),
            }
        )

    cross_mappings = []
    for row in (semantic_mappings or [])[:60]:
        if (row.get("status") or "").lower() == "rejected":
            continue
        cross_mappings.append(
            {
                "canonical_entity_id": row.get("canonical_entity_id"),
                "member_id": row.get("source_member_id"),
                "entity_id": row.get("source_entity_id"),
                "confidence": row.get("confidence"),
                "status": row.get("status"),
            }
        )

    return {
        "graph_name": graph_name,
        "warehouse_dialect": (graph_settings.get("warehouse_dialect") or "logical"),
        "modeling_preference": graph_settings.get("modeling_preference") or "star",
        "members": member_summaries,
        "canonical_entities": entities,
        "canonical_graph_edges": edges,
        "cross_entity_mappings": cross_mappings,
    }


def bundle_to_prompt_json(bundle: Dict[str, Any]) -> str:
    return json.dumps(bundle, indent=2, default=str)
