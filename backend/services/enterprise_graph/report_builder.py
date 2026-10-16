"""Build unified enterprise graph assessment report from per-member DAA reports."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

OnReportProgress = Optional[Callable[..., Awaitable[None]]]

from services.data_assessment.scoring import DIMENSION_WEIGHTS


def _parse_scores(report: Dict[str, Any]) -> Dict[str, Any]:
    scores = report.get("scores") or {}
    if isinstance(scores, str):
        import json

        try:
            scores = json.loads(scores)
        except json.JSONDecodeError:
            scores = {}
    return scores


def _parse_sections(report: Dict[str, Any]) -> Dict[str, Any]:
    sections = report.get("sections") or {}
    if isinstance(sections, str):
        import json

        try:
            sections = json.loads(sections)
        except json.JSONDecodeError:
            sections = {}
    return sections


def _parse_entities(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    entities = report.get("entities") or []
    if isinstance(entities, str):
        import json

        try:
            entities = json.loads(entities)
        except json.JSONDecodeError:
            entities = []
    return entities if isinstance(entities, list) else []


def _count_problem_entities(entities: List[Dict[str, Any]]) -> int:
    n = 0
    for ep in entities:
        if ep.get("error"):
            n += 1
            continue
        qr = ep.get("quality_report") or {}
        summary = qr.get("summary") or {}
        score = summary.get("data_quality_score")
        dups = qr.get("duplicates") or {}
        exact = (dups.get("exact_duplicates") or {}).get("count") or 0
        key_dup = (dups.get("key_field_duplicates") or {}).get("count") or 0
        miss = (qr.get("missing_values") or {}).get("total_missing") or 0
        fmt = qr.get("format_issues") or {}
        fmt_n = sum(len(v) if isinstance(v, list) else 0 for v in fmt.values())
        if exact or key_dup or miss or fmt_n or (score is not None and score < 80):
            n += 1
    return n


async def _emit_report_progress(
    on_progress: OnReportProgress,
    percent: int,
    sub_step: str,
    message: str,
    *,
    heartbeat: bool = False,
) -> None:
    if on_progress:
        await on_progress(percent, sub_step, message, heartbeat)


def _accumulate_member_result(
    mr: Dict[str, Any],
    *,
    members_out: List[Dict[str, Any]],
    all_entities: List[Dict[str, Any]],
    connected_members: List[Dict[str, Any]],
    by_member_readiness: List[Dict[str, Any]],
    overalls: List[float],
    dim_sums: Dict[str, float],
    dim_count_ref: List[int],
    total_problem_ref: List[int],
    total_entities_ref: List[int],
    all_gaps: List[Dict[str, Any]],
) -> None:
    member_id = mr["member_id"]
    display_name = mr.get("display_name") or mr.get("connector_type")
    connector_type = mr.get("connector_type")
    role = mr.get("role") or "source"
    entry: Dict[str, Any] = {
        "member_id": member_id,
        "connector_type": connector_type,
        "display_name": display_name,
        "role": role,
        "assessment_report_id": mr.get("assessment_report_id"),
    }

    if mr.get("error"):
        entry["error"] = mr["error"]
        members_out.append(entry)
        connected_members.append(
            {
                "member_id": member_id,
                "connector_type": connector_type,
                "display_name": display_name,
                "entity_count": 0,
                "error": mr["error"],
            }
        )
        return

    report = mr.get("report") or {}
    scores = _parse_scores(report)
    sections = _parse_sections(report)
    entities = _parse_entities(report)
    entity_count = int(scores.get("entities_assessed") or len(entities))
    overall = scores.get("overall")
    if overall is not None:
        overalls.append(float(overall))
    dims = scores.get("dimensions") or {}
    if dims:
        dim_count_ref[0] += 1
        for k in DIMENSION_WEIGHTS:
            dim_sums[k] += float(dims.get(k) or 0)

    total_problem_ref[0] += _count_problem_entities(entities)
    total_entities_ref[0] += entity_count

    for ep in entities:
        tagged = dict(ep)
        tagged["member_id"] = member_id
        tagged["member_display_name"] = display_name
        tagged["source_connector_type"] = connector_type
        all_entities.append(tagged)

    gaps = (sections.get("data_gaps") or {}).get("findings") or []
    for g in gaps:
        g2 = dict(g)
        g2["member_id"] = member_id
        g2["member_display_name"] = display_name
        all_gaps.append(g2)

    entry["scores"] = scores
    members_out.append(entry)
    connected_members.append(
        {
            "member_id": member_id,
            "connector_type": connector_type,
            "display_name": display_name,
            "entity_count": entity_count,
            "scores": {"overall": overall},
        }
    )
    by_member_readiness.append(
        {
            "member_id": member_id,
            "display_name": display_name,
            "overall": overall,
            "dimensions": dims,
        }
    )


def _finalize_snapshot(
    *,
    graph: Dict[str, Any],
    member_results: List[Dict[str, Any]],
    depth: str,
    members_out: List[Dict[str, Any]],
    all_entities: List[Dict[str, Any]],
    connected_members: List[Dict[str, Any]],
    by_member_readiness: List[Dict[str, Any]],
    overalls: List[float],
    dim_sums: Dict[str, float],
    dim_count: int,
    total_problem: int,
    total_entities: int,
    all_gaps: List[Dict[str, Any]],
    cross_links: Optional[List[Dict[str, Any]]],
    mapped_entity_clusters: Optional[int],
    canonical_graph: Optional[Dict[str, Any]],
    per_member_overlays: Optional[Dict[str, Any]],
    warehouse_proposal_section: Optional[Dict[str, Any]],
    lake_proposal_section: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rollup_dims = (
        {k: round(dim_sums[k] / dim_count, 1) for k in DIMENSION_WEIGHTS}
        if dim_count
        else {k: 0.0 for k in DIMENSION_WEIGHTS}
    )
    rollup_overall = round(sum(overalls) / len(overalls), 1) if overalls else 0.0
    canon = canonical_graph or {"nodes": [], "edges": [], "summary": {}}
    canon_nodes = canon.get("nodes") or []
    mapped_count = (
        len(canon_nodes)
        if canon_nodes
        else (mapped_entity_clusters if mapped_entity_clusters is not None else 0)
    )
    links = cross_links if cross_links is not None else []
    overlays = per_member_overlays if per_member_overlays is not None else {}
    warehouse_section = warehouse_proposal_section or {}
    lake_section = lake_proposal_section or {}

    return {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "depth": depth,
        "graph": {
            "id": graph.get("id"),
            "name": graph.get("name"),
        },
        "scores": {
            "overall": rollup_overall,
            "sources_included": len(member_results),
            "problem_entities": total_problem,
            "mapped_entities": mapped_count,
            "entities_assessed": total_entities,
            "cross_links_count": len(links),
            "dimensions": rollup_dims,
        },
        "sections": {
            "connected_environment": {
                "members": connected_members,
                "cross_links": links,
            },
            "readiness": {
                "by_member": by_member_readiness,
                "rollup_dimensions": rollup_dims,
            },
            "schema_map": {
                "canonical_graph": canon,
                "per_member_overlays": overlays,
            },
            "data_gaps": {"findings": all_gaps},
            "warehouse_proposal": warehouse_section,
            "lake_proposal": lake_section,
            "summary": _build_summary_text(graph.get("name"), len(member_results), rollup_overall),
        },
        "members": members_out,
        "entities": all_entities,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def build_enterprise_report_snapshot(
    *,
    graph: Dict[str, Any],
    member_results: List[Dict[str, Any]],
    depth: str,
    cross_links: Optional[List[Dict[str, Any]]] = None,
    mapped_entity_clusters: Optional[int] = None,
    canonical_graph: Optional[Dict[str, Any]] = None,
    per_member_overlays: Optional[Dict[str, Any]] = None,
    warehouse_proposal_section: Optional[Dict[str, Any]] = None,
    lake_proposal_section: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Sync snapshot build (tests and callers without progress)."""
    members_out: List[Dict[str, Any]] = []
    all_entities: List[Dict[str, Any]] = []
    connected_members: List[Dict[str, Any]] = []
    by_member_readiness: List[Dict[str, Any]] = []
    overalls: List[float] = []
    dim_sums = {k: 0.0 for k in DIMENSION_WEIGHTS}
    dim_count_ref = [0]
    total_problem_ref = [0]
    total_entities_ref = [0]
    all_gaps: List[Dict[str, Any]] = []

    for mr in member_results:
        _accumulate_member_result(
            mr,
            members_out=members_out,
            all_entities=all_entities,
            connected_members=connected_members,
            by_member_readiness=by_member_readiness,
            overalls=overalls,
            dim_sums=dim_sums,
            dim_count_ref=dim_count_ref,
            total_problem_ref=total_problem_ref,
            total_entities_ref=total_entities_ref,
            all_gaps=all_gaps,
        )

    return _finalize_snapshot(
        graph=graph,
        member_results=member_results,
        depth=depth,
        members_out=members_out,
        all_entities=all_entities,
        connected_members=connected_members,
        by_member_readiness=by_member_readiness,
        overalls=overalls,
        dim_sums=dim_sums,
        dim_count=dim_count_ref[0],
        total_problem=total_problem_ref[0],
        total_entities=total_entities_ref[0],
        all_gaps=all_gaps,
        cross_links=cross_links,
        mapped_entity_clusters=mapped_entity_clusters,
        canonical_graph=canonical_graph,
        per_member_overlays=per_member_overlays,
        warehouse_proposal_section=warehouse_proposal_section,
        lake_proposal_section=lake_proposal_section,
    )


async def build_enterprise_report_snapshot_async(
    *,
    graph: Dict[str, Any],
    member_results: List[Dict[str, Any]],
    depth: str,
    cross_links: Optional[List[Dict[str, Any]]] = None,
    mapped_entity_clusters: Optional[int] = None,
    canonical_graph: Optional[Dict[str, Any]] = None,
    per_member_overlays: Optional[Dict[str, Any]] = None,
    warehouse_proposal_section: Optional[Dict[str, Any]] = None,
    lake_proposal_section: Optional[Dict[str, Any]] = None,
    on_progress: OnReportProgress = None,
) -> Dict[str, Any]:
    """Async snapshot build with real per-step progress (95–99% band)."""
    await _emit_report_progress(
        on_progress,
        95,
        "report_start",
        "Building unified enterprise report",
    )

    members_out: List[Dict[str, Any]] = []
    all_entities: List[Dict[str, Any]] = []
    connected_members: List[Dict[str, Any]] = []
    by_member_readiness: List[Dict[str, Any]] = []
    overalls: List[float] = []
    dim_sums = {k: 0.0 for k in DIMENSION_WEIGHTS}
    dim_count_ref = [0]
    total_problem_ref = [0]
    total_entities_ref = [0]
    all_gaps: List[Dict[str, Any]] = []

    total_members = max(len(member_results), 1)
    for idx, mr in enumerate(member_results):
        display_name = mr.get("display_name") or mr.get("connector_type") or "Connector"
        pct = 95 + min(3, int(3 * (idx + 1) / total_members))
        await _emit_report_progress(
            on_progress,
            pct,
            "report_member",
            f"Merging assessment from {display_name}",
        )
        _accumulate_member_result(
            mr,
            members_out=members_out,
            all_entities=all_entities,
            connected_members=connected_members,
            by_member_readiness=by_member_readiness,
            overalls=overalls,
            dim_sums=dim_sums,
            dim_count_ref=dim_count_ref,
            total_problem_ref=total_problem_ref,
            total_entities_ref=total_entities_ref,
            all_gaps=all_gaps,
        )

    await _emit_report_progress(
        on_progress,
        98,
        "report_rollup",
        "Computing combined readiness scores",
    )
    await _emit_report_progress(
        on_progress,
        99,
        "report_sections",
        "Assembling dashboard sections and schema map",
    )

    return _finalize_snapshot(
        graph=graph,
        member_results=member_results,
        depth=depth,
        members_out=members_out,
        all_entities=all_entities,
        connected_members=connected_members,
        by_member_readiness=by_member_readiness,
        overalls=overalls,
        dim_sums=dim_sums,
        dim_count=dim_count_ref[0],
        total_problem=total_problem_ref[0],
        total_entities=total_entities_ref[0],
        all_gaps=all_gaps,
        cross_links=cross_links,
        mapped_entity_clusters=mapped_entity_clusters,
        canonical_graph=canonical_graph,
        per_member_overlays=per_member_overlays,
        warehouse_proposal_section=warehouse_proposal_section,
        lake_proposal_section=lake_proposal_section,
    )


def _build_summary_text(name: Optional[str], n_sources: int, overall: float) -> str:
    title = name or "Enterprise graph"
    return (
        f"{title}: assessed {n_sources} connected source{'s' if n_sources != 1 else ''}. "
        f"Combined data readiness score is {overall}/100."
    )


def snapshot_for_assessment_dashboard(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Shape compatible with AssessmentDashboard (scores, sections, entities)."""
    return {
        "id": snapshot.get("graph", {}).get("id"),
        "created_at": snapshot.get("created_at") or snapshot.get("generated_at"),
        "scores": snapshot.get("scores") or {},
        "sections": snapshot.get("sections") or {},
        "entities": snapshot.get("entities") or [],
        "is_partial": False,
    }
