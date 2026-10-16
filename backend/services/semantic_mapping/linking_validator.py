"""Validate and normalize LLM cross-connector mapping output."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Set, Tuple

from services.enterprise_graph.inventory import SourceEntity
from services.semantic_mapping.config import ega_mapping_min_confidence


def _inventory_keys(inventory: List[SourceEntity]) -> Set[Tuple[str, str]]:
    return {(str(e.member_id), str(e.entity_id)) for e in inventory}


def validate_entity_mapping_clusters(
    raw: Dict[str, Any],
    inventory: List[SourceEntity],
    *,
    min_confidence: float | None = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Returns (clusters for clusters_to_db_rows, warnings).
    Each cluster: canonical_entity_id, confidence, mapping_kind, evidence, entities.
    """
    thresh = min_confidence if min_confidence is not None else ega_mapping_min_confidence()
    valid_keys = _inventory_keys(inventory)
    warnings: List[str] = []
    clusters: List[Dict[str, Any]] = []
    used_entities: Set[Tuple[str, str]] = set()

    for idx, cluster in enumerate(raw.get("clusters") or []):
        if not isinstance(cluster, dict):
            warnings.append(f"cluster[{idx}]: not an object")
            continue
        conf = float(cluster.get("confidence") or 0)
        if conf < thresh:
            warnings.append(f"cluster[{idx}]: below confidence threshold")
            continue
        entities_in: List[Dict[str, str]] = []
        member_ids: Set[str] = set()
        entities_per_member: Dict[str, int] = {}
        for ent in cluster.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            mid = str(ent.get("member_id") or "").strip()
            eid = str(ent.get("entity_id") or "").strip()
            if not mid or not eid:
                continue
            key = (mid, eid)
            if key not in valid_keys:
                warnings.append(f"cluster[{idx}]: unknown entity {mid}:{eid}")
                continue
            if key in used_entities:
                warnings.append(f"cluster[{idx}]: duplicate entity {mid}:{eid}")
                continue
            entities_in.append({"member_id": mid, "entity_id": eid})
            member_ids.add(mid)
            entities_per_member[mid] = entities_per_member.get(mid, 0) + 1
        if len(entities_in) < 2:
            warnings.append(f"cluster[{idx}]: fewer than 2 valid entities")
            continue
        if len(member_ids) < 2:
            warnings.append(f"cluster[{idx}]: entities must span 2+ connectors")
            continue
        if any(count > 1 for count in entities_per_member.values()):
            warnings.append(f"cluster[{idx}]: at most one entity per connector per cluster")
            continue
        for key in [(e["member_id"], e["entity_id"]) for e in entities_in]:
            used_entities.add(key)
        clusters.append(
            {
                "canonical_entity_id": str(uuid.uuid4()),
                "confidence": conf,
                "mapping_kind": "llm",
                "evidence": {
                    "llm": cluster.get("evidence") or "",
                    "canonical_name": cluster.get("canonical_name") or "",
                },
                "entities": entities_in,
            }
        )
    return clusters, warnings
