"""Resolve entity references (priors, legacy ids) to current assessment inventory."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from services.enterprise_graph.inventory import SourceEntity, normalize_entity_name


def inventory_entity_keys(inventory: List[SourceEntity]) -> Set[Tuple[str, str]]:
    return {(str(e.member_id), str(e.entity_id)) for e in inventory if e.entity_id}


def _norm_ref(value: str) -> str:
    return normalize_entity_name(value or "")


def resolve_entity_ref(
    member_id: str,
    entity_ref: str,
    inventory: List[SourceEntity],
) -> Optional[str]:
    """
    Map a prior or legacy entity reference to the canonical entity_id on this member.
    Matches exact entity_id, label, object_name, and spreadsheet/sheet aliases.
    """
    ref = (entity_ref or "").strip()
    if not ref:
        return None
    ref_norm = _norm_ref(ref)

    for ent in inventory:
        if str(ent.member_id) != str(member_id):
            continue
        eid = str(ent.entity_id)
        if eid == ref:
            return eid
        if (ent.label or "").strip() == ref:
            return eid
        if _norm_ref(ent.label) == ref_norm:
            return eid
        obj = (ent.object_name or "").strip()
        if obj and (_norm_ref(obj) == ref_norm or obj == ref):
            return eid
    return None


def resolve_flow_priors(
    priors: List[Dict[str, Any]],
    inventory: List[SourceEntity],
) -> List[Dict[str, Any]]:
    """Keep flow priors only when both endpoints exist in current inventory."""
    resolved: List[Dict[str, Any]] = []
    for prior in priors or []:
        src_mid = str(prior.get("source_member_id") or "")
        tgt_mid = str(prior.get("target_member_id") or "")
        src_ref = str(prior.get("source_entity_id") or "")
        tgt_ref = str(prior.get("target_entity_id") or "")
        src_eid = resolve_entity_ref(src_mid, src_ref, inventory)
        tgt_eid = resolve_entity_ref(tgt_mid, tgt_ref, inventory)
        if not src_eid or not tgt_eid:
            continue
        resolved.append(
            {
                **prior,
                "source_entity_id": src_eid,
                "target_entity_id": tgt_eid,
            }
        )
    return resolved


def entity_label_for(
    member_id: str,
    entity_id: str,
    inventory: List[SourceEntity],
) -> str:
    for ent in inventory:
        if str(ent.member_id) == str(member_id) and str(ent.entity_id) == str(entity_id):
            return ent.label or ent.entity_id
    return entity_id
