"""Heuristic cross-connector entity linking."""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from services.enterprise_graph.config import link_confidence_threshold
from services.enterprise_graph.entity_resolution import resolve_flow_priors
from services.enterprise_graph.inventory import SourceEntity, normalize_entity_name

# Shared domain bucket boosts (account ↔ customer, etc.)
_CRM_DOMAIN: Dict[str, str] = {
    "account": "party",
    "customer": "party",
    "contact": "party",
    "lead": "party",
    "user": "party",
    "opportunity": "sales",
    "order": "commerce",
    "product": "catalog",
}
from services.enterprise_graph.models import GraphMember


def _name_similarity(a: str, b: str) -> float:
    na, nb = normalize_entity_name(a), normalize_entity_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.88
    ta, tb = set(na.split("_")), set(nb.split("_"))
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb) / max(len(ta | tb), 1)
    if overlap >= 0.5:
        return 0.72 + overlap * 0.2
    return overlap * 0.6


def _domain_overlap(a: SourceEntity, b: SourceEntity) -> float:
    tags_a = set(a.domain_tags or [])
    tags_b = set(b.domain_tags or [])
    da = _CRM_DOMAIN.get(a.normalized_name)
    db = _CRM_DOMAIN.get(b.normalized_name)
    if da:
        tags_a.add(da)
    if db:
        tags_b.add(db)
    if tags_a & tags_b:
        return 0.42
    return 0.0


def _normalize_category(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _category_match_score(a: SourceEntity, b: SourceEntity) -> float:
    ca = _normalize_category(a.category)
    cb = _normalize_category(b.category)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 0.22
    if ca in cb or cb in ca:
        return 0.12
    return 0.0


def _object_name_match_score(a: SourceEntity, b: SourceEntity) -> float:
    oa = normalize_entity_name(a.object_name or a.label)
    ob = normalize_entity_name(b.object_name or b.label)
    if not oa or not ob:
        return 0.0
    if oa == ob:
        return 0.2
    if oa in ob or ob in oa:
        return 0.12
    return 0.0


def _description_overlap_score(a: SourceEntity, b: SourceEntity) -> float:
    da = set(re.findall(r"[a-z0-9]{4,}", (a.category_description or "").lower()))
    db = set(re.findall(r"[a-z0-9]{4,}", (b.category_description or "").lower()))
    if not da or not db:
        return 0.0
    overlap = len(da & db) / max(len(da | db), 1)
    return min(0.15, overlap * 0.2)


def _column_overlap(a: SourceEntity, b: SourceEntity) -> float:
    if not a.column_names or not b.column_names:
        return 0.0
    ca = {normalize_entity_name(c) for c in a.column_names}
    cb = {normalize_entity_name(c) for c in b.column_names}
    shared = ca & cb
    if not shared:
        return 0.0
    return min(0.25, 0.05 * len(shared))


def _business_entity_match(a: SourceEntity, b: SourceEntity) -> float:
    """Same CRM domain bucket (e.g. account ↔ customer)."""
    da = _CRM_DOMAIN.get(a.normalized_name)
    db = _CRM_DOMAIN.get(b.normalized_name)
    if da and db and da == db:
        return 0.72
    return 0.0


def score_pair(a: SourceEntity, b: SourceEntity) -> Tuple[float, Dict[str, Any]]:
    name_score = _name_similarity(a.label, b.label)
    biz_score = _business_entity_match(a, b)
    effective_name = max(name_score, biz_score)
    dom = 0.0 if biz_score >= 0.7 else _domain_overlap(a, b)
    cat_score = _category_match_score(a, b)
    obj_score = _object_name_match_score(a, b)
    desc_score = _description_overlap_score(a, b)
    col_score = _column_overlap(a, b)
    score = min(1.0, effective_name + dom + cat_score + obj_score + desc_score + col_score)
    evidence = {
        "name_score": round(name_score, 3),
        "business_match": round(biz_score, 3),
        "category_match": round(cat_score, 3),
        "object_name_match": round(obj_score, 3),
        "description_overlap": round(desc_score, 3),
        "column_overlap": round(col_score, 3),
        "labels": [a.label, b.label],
        "categories": [a.category, b.category],
    }
    return score, evidence


def build_candidate_pairs(
    inventory: List[SourceEntity],
    priors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """All cross-member entity pairs with scores (including flow priors)."""
    entities_by_member: Dict[str, List[SourceEntity]] = {}
    for e in inventory:
        entities_by_member.setdefault(e.member_id, []).append(e)

    member_ids = list(entities_by_member.keys())
    candidates: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    for i, mid_a in enumerate(member_ids):
        for mid_b in member_ids[i + 1 :]:
            for ea in entities_by_member[mid_a]:
                for eb in entities_by_member[mid_b]:
                    score, evidence = score_pair(ea, eb)
                    key = (ea.member_id, ea.entity_id, eb.member_id, eb.entity_id)
                    rev = (eb.member_id, eb.entity_id, ea.member_id, ea.entity_id)
                    if key in seen or rev in seen:
                        continue
                    if score < 0.55:
                        continue
                    seen.add(key)
                    candidates.append(
                        {
                            "source_member_id": ea.member_id,
                            "source_entity_id": ea.entity_id,
                            "target_member_id": eb.member_id,
                            "target_entity_id": eb.entity_id,
                            "confidence": round(score, 3),
                            "mapping_kind": "heuristic",
                            "evidence": evidence,
                        }
                    )

    for p in resolve_flow_priors(priors, inventory):
        key = (
            p["source_member_id"],
            p["source_entity_id"],
            p["target_member_id"],
            p["target_entity_id"],
        )
        rev = (key[2], key[3], key[0], key[1])
        replaced = False
        for c in candidates:
            ck = (c["source_member_id"], c["source_entity_id"], c["target_member_id"], c["target_entity_id"])
            if ck == key or ck == rev:
                c["confidence"] = max(c["confidence"], p["confidence"])
                c["mapping_kind"] = "flow_prior"
                c["evidence"] = {**(c.get("evidence") or {}), **(p.get("evidence") or {})}
                replaced = True
                break
        if not replaced and key not in seen and rev not in seen:
            candidates.append(p)
            seen.add(key)

    candidates.sort(key=lambda x: -float(x.get("confidence") or 0))
    return candidates


def candidates_to_mapping_clusters(
    candidates: List[Dict[str, Any]],
    *,
    threshold: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Best-one pairing per entity; each cluster is two endpoints sharing a canonical id."""
    thresh = threshold if threshold is not None else link_confidence_threshold()
    used_entities: Set[Tuple[str, str]] = set()
    clusters: List[Dict[str, Any]] = []

    for c in candidates:
        conf = float(c.get("confidence") or 0)
        if conf < thresh:
            continue
        src_key = (c["source_member_id"], c["source_entity_id"])
        tgt_key = (c["target_member_id"], c["target_entity_id"])
        if src_key in used_entities or tgt_key in used_entities:
            continue
        used_entities.add(src_key)
        used_entities.add(tgt_key)
        clusters.append(
            {
                "canonical_entity_id": str(uuid.uuid4()),
                "confidence": conf,
                "mapping_kind": c.get("mapping_kind") or "heuristic",
                "evidence": c.get("evidence") or {},
                "entities": [
                    {
                        "member_id": c["source_member_id"],
                        "entity_id": c["source_entity_id"],
                    },
                    {
                        "member_id": c["target_member_id"],
                        "entity_id": c["target_entity_id"],
                    },
                ],
            }
        )
    return clusters


def clusters_to_db_rows(graph_id: str, clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for cluster in clusters:
        cid = cluster["canonical_entity_id"]
        for ent in cluster.get("entities") or []:
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "graph_id": graph_id,
                    "canonical_entity_id": cid,
                    "source_member_id": ent["member_id"],
                    "source_entity_id": ent["entity_id"],
                    "confidence": cluster.get("confidence"),
                    "mapping_kind": cluster.get("mapping_kind") or "heuristic",
                    "evidence": cluster.get("evidence") or {},
                    "status": cluster.get("status") or "suggested",
                }
            )
    return rows


def build_cross_links_from_rows(
    rows: List[Dict[str, Any]],
    members: List[GraphMember],
    *,
    inventory: Optional[List[SourceEntity]] = None,
) -> List[Dict[str, Any]]:
    """Edges for connected_environment UI (one edge per canonical cluster pair)."""
    member_names = {m.id: m.display_name for m in members}
    label_by_key: Dict[Tuple[str, str], str] = {}
    if inventory:
        for e in inventory:
            label_by_key[(e.member_id, e.entity_id)] = e.label

    by_canonical: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if (r.get("status") or "").lower() == "rejected":
            continue
        cid = str(r.get("canonical_entity_id") or "")
        if not cid:
            continue
        by_canonical.setdefault(cid, []).append(r)

    links: List[Dict[str, Any]] = []
    for cid, group in by_canonical.items():
        if len(group) < 2:
            continue
        a, b = group[0], group[1]
        mid_a = a["source_member_id"]
        mid_b = b["source_member_id"]
        eid_a = a["source_entity_id"]
        eid_b = b["source_entity_id"]
        links.append(
            {
                "canonical_entity_id": cid,
                "source_member_id": mid_a,
                "source_entity_id": eid_a,
                "source_entity_label": label_by_key.get((mid_a, eid_a), eid_a),
                "source_display_name": member_names.get(mid_a, ""),
                "target_member_id": mid_b,
                "target_entity_id": eid_b,
                "target_entity_label": label_by_key.get((mid_b, eid_b), eid_b),
                "target_display_name": member_names.get(mid_b, ""),
                "confidence": float(a.get("confidence") or b.get("confidence") or 0),
                "status": a.get("status") or b.get("status") or "suggested",
                "mapping_kind": a.get("mapping_kind") or b.get("mapping_kind"),
            }
        )
    return links


async def _run_heuristic_entity_linking(
    *,
    graph_id: str,
    members: List[GraphMember],
    inventory: List[SourceEntity],
    priors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    candidates = build_candidate_pairs(inventory, priors)
    from services.semantic_mapping.linker_llm import refine_ambiguous_candidates

    candidates = await refine_ambiguous_candidates(candidates, inventory, members)
    clusters = candidates_to_mapping_clusters(candidates)
    return clusters_to_db_rows(graph_id, clusters)


async def run_entity_linking(
    *,
    graph_id: str,
    members: List[GraphMember],
    inventory: List[SourceEntity],
    priors: List[Dict[str, Any]],
    user_id: Optional[str] = None,
    graph_name: str = "Enterprise graph",
    graph_settings: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    LLM-first cross-connector mapping; heuristic linker is fallback only.
    Returns (db_rows, cross_links, design_method).
    """
    import logging

    from services.semantic_mapping.config import (
        entity_linking_llm_enabled,
        entity_linking_llm_fallback_enabled,
    )

    logger = logging.getLogger(__name__)
    design_method = "heuristic"
    rows: List[Dict[str, Any]] = []

    if entity_linking_llm_enabled() and user_id:
        try:
            from services.semantic_mapping.entity_linking_llm import propose_entity_mappings_llm

            clusters, _rationale = await propose_entity_mappings_llm(
                graph_name=graph_name,
                graph_settings=graph_settings,
                members=members,
                inventory=inventory,
                user_id=user_id,
            )
            rows = clusters_to_db_rows(graph_id, clusters)
            design_method = "llm"
        except Exception as exc:
            if not entity_linking_llm_fallback_enabled():
                raise
            logger.warning("Entity linking LLM failed, using heuristic fallback: %s", exc)

    if not rows:
        rows = await _run_heuristic_entity_linking(
            graph_id=graph_id,
            members=members,
            inventory=inventory,
            priors=priors,
        )
        design_method = "heuristic"

    cross_links = build_cross_links_from_rows(rows, members, inventory=inventory)
    return rows, cross_links, design_method
