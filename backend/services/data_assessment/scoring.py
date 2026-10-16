"""Dimension scoring for connector assessments.

Dimensions are per-connector only (single source). Cross-system semantic mapping
and enterprise warehouse design are handled by the Enterprise Data Graph agent.

- completeness: penalized by missing/null/empty values in sampled rows
- quality: from programmatic data_quality_score (duplicates, formats, etc.)
- consistency: penalized by case/type inconsistencies in sample
- structure: schema/catalog readiness for this connector (cached metadata coverage)
- warehouse_readiness: blend of quality + completeness, minus format penalties
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

DIMENSION_WEIGHTS = {
    "completeness": 0.25,
    "quality": 0.30,
    "consistency": 0.15,
    "structure": 0.15,
    "warehouse_readiness": 0.15,
}

_STRUCTURE_GAP_CATEGORIES = frozenset({"metadata", "relationship", "schema_staleness"})


def _entities_with_structure_gaps(gap_findings: Optional[List[Dict[str, Any]]]) -> Set[str]:
    out: Set[str] = set()
    for g in gap_findings or []:
        if (g.get("category") or "") in _STRUCTURE_GAP_CATEGORIES:
            eid = g.get("entity_id")
            if eid and eid != "*":
                out.add(str(eid))
    return out


def score_entity_profile(
    quality_report: Dict[str, Any],
    records_sampled: int,
    *,
    schema_ready: bool = True,
) -> Dict[str, float]:
    summary = (quality_report or {}).get("summary") or {}
    base_quality = float(summary.get("data_quality_score") or 100)
    missing = (quality_report or {}).get("missing_values") or {}
    total_missing = int(missing.get("total_missing") or 0)
    inconsistencies = (quality_report or {}).get("inconsistencies") or {}
    fmt = (quality_report or {}).get("format_issues") or {}

    completeness = 100.0
    if records_sampled > 0 and total_missing > 0:
        completeness = max(0.0, 100.0 - min(100.0, (total_missing / max(records_sampled, 1)) * 50))

    quality = base_quality
    consistency = 100.0
    if inconsistencies:
        consistency = max(40.0, 100.0 - len(inconsistencies) * 8)

    if not schema_ready:
        structure = 45.0
    elif records_sampled > 0:
        structure = 85.0
    else:
        structure = 50.0

    warehouse = quality * 0.6 + completeness * 0.4
    if fmt:
        warehouse = max(30.0, warehouse - len(fmt) * 5)

    return {
        "completeness": round(completeness, 1),
        "quality": round(quality, 1),
        "consistency": round(consistency, 1),
        "structure": round(structure, 1),
        "warehouse_readiness": round(warehouse, 1),
    }


def rollup_connector_scores(
    entity_profiles: List[Dict[str, Any]],
    gap_findings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not entity_profiles:
        return {
            "overall": 0.0,
            "dimensions": {k: 0.0 for k in DIMENSION_WEIGHTS},
            "entities_assessed": 0,
        }

    gap_entities = _entities_with_structure_gaps(gap_findings)
    dim_totals = {k: 0.0 for k in DIMENSION_WEIGHTS}
    count = 0
    for ep in entity_profiles:
        if ep.get("error"):
            continue
        eid = ep.get("entity_id")
        schema_ready = eid not in gap_entities
        dims = ep.get("dimension_scores") or {}
        if not dims and ep.get("quality_report") is not None:
            dims = score_entity_profile(
                ep.get("quality_report") or {},
                int(ep.get("records_sampled") or 0),
                schema_ready=schema_ready,
            )
        elif not schema_ready and dims:
            dims = dict(dims)
            dims["structure"] = min(float(dims.get("structure") or 85), 45.0)

        count += 1
        for k in DIMENSION_WEIGHTS:
            dim_totals[k] += float(dims.get(k) or 0)

    if count == 0:
        return {
            "overall": 0.0,
            "dimensions": {k: 0.0 for k in DIMENSION_WEIGHTS},
            "entities_assessed": 0,
        }

    dimensions = {k: round(dim_totals[k] / count, 1) for k in DIMENSION_WEIGHTS}

    if gap_findings:
        global_gaps = [g for g in gap_findings if g.get("entity_id") == "*"]
        if global_gaps:
            dimensions["structure"] = max(0.0, dimensions["structure"] - 5.0 * len(global_gaps))

    overall = sum(dimensions[k] * DIMENSION_WEIGHTS[k] for k in DIMENSION_WEIGHTS)
    return {
        "overall": round(overall, 1),
        "dimensions": dimensions,
        "entities_assessed": count,
    }
