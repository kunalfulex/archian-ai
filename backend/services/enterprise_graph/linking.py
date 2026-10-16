"""Run cross-connector entity linking and persist results."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.data_assessment import store as assessment_store
from services.enterprise_graph import store
from services.enterprise_graph.inventory import build_inventory_from_member_results
from services.enterprise_graph.canonical_builder import (
    build_canonical_entities_from_mappings,
    run_canonical_build,
)
from services.enterprise_graph.lake_designer import build_lake_proposal
from services.enterprise_graph.warehouse_designer import build_warehouse_proposal
from services.enterprise_graph.linker import (
    build_cross_links_from_rows,
    run_entity_linking,
)
from services.enterprise_graph.models import GraphMember
from services.enterprise_graph.priors import load_flow_mapping_priors


async def load_member_results_from_assessments(
    user_id: str,
    members: List[GraphMember],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for member in members:
        base = {
            "member_id": member.id,
            "display_name": member.display_name,
            "connector_type": member.connector_type,
            "role": member.role,
        }
        report_id = member.last_assessment_report_id
        if not report_id:
            results.append({**base, "error": "No assessment report on this member"})
            continue
        report = await assessment_store.get_report(str(report_id), user_id)
        if not report:
            results.append({**base, "error": "Assessment report not found"})
            continue
        results.append(
            {
                **base,
                "assessment_report_id": str(report_id),
                "report": dict(report),
            }
        )
    return results


async def run_graph_linking_and_canonical(
    *,
    graph_id: str,
    user_id: str,
    members: List[GraphMember],
    member_results: List[Dict[str, Any]],
    graph_settings: Optional[Dict[str, Any]] = None,
    graph_name: str = "Enterprise graph",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """
    Returns (mapping_rows, cross_links, canonical_entities, canonical_graph, per_member_overlays).
    """
    rows, cross_links, mapped_clusters = await run_graph_entity_linking(
        graph_id=graph_id,
        user_id=user_id,
        members=members,
        member_results=member_results,
        graph_settings=graph_settings,
        graph_name=graph_name,
    )
    inventory = await build_inventory_from_member_results(
        members, member_results, user_id=user_id
    )
    mappings = await store.list_semantic_mappings(graph_id)
    canonical_entities, canonical_graph, overlays = await run_canonical_build(
        graph_id=graph_id,
        user_id=user_id,
        members=members,
        mappings=mappings,
        inventory=inventory,
    )
    canonical_entities = await store.replace_canonical_entities(graph_id, canonical_entities)
    return rows, cross_links, canonical_entities, canonical_graph, overlays


async def run_graph_entity_linking(
    *,
    graph_id: str,
    user_id: str,
    members: List[GraphMember],
    member_results: List[Dict[str, Any]],
    graph_settings: Optional[Dict[str, Any]] = None,
    graph_name: str = "Enterprise graph",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """
    Returns (db_rows, cross_links, mapped_cluster_count).
    Preserves user-confirmed mappings; replaces suggestions only.
    """
    inventory = await build_inventory_from_member_results(
        members, member_results, user_id=user_id
    )
    await store.prune_semantic_mappings_outside_inventory(graph_id, inventory)
    if len(inventory) < 2:
        existing = await store.list_semantic_mappings(graph_id)
        cross_links = build_cross_links_from_rows(existing, members, inventory=inventory)
        confirmed = [r for r in existing if (r.get("status") or "").lower() == "confirmed"]
        by_c: Dict[str, int] = {}
        for r in confirmed:
            cid = str(r.get("canonical_entity_id") or "")
            by_c[cid] = by_c.get(cid, 0) + 1
        mapped = sum(1 for n in by_c.values() if n >= 2)
        return existing, cross_links, mapped

    priors = await load_flow_mapping_priors(user_id, members)
    suggested_rows, cross_links, _link_method = await run_entity_linking(
        graph_id=graph_id,
        members=members,
        inventory=inventory,
        priors=priors,
        user_id=user_id,
        graph_name=graph_name,
        graph_settings=graph_settings,
    )

    confirmed = [
        r
        for r in await store.list_semantic_mappings(graph_id)
        if (r.get("status") or "").lower() == "confirmed"
    ]

    def _cluster_member_keys(rows: List[Dict[str, Any]]) -> frozenset:
        return frozenset(
            (str(r["source_member_id"]), str(r["source_entity_id"]))
            for r in rows
            if r.get("source_member_id") and r.get("source_entity_id")
        )

    confirmed_entity_keys = {
        (str(r["source_member_id"]), str(r["source_entity_id"])) for r in confirmed
    }
    confirmed_cluster_keys: set = set()
    by_confirmed_cid: Dict[str, List[Dict[str, Any]]] = {}
    for r in confirmed:
        cid = str(r.get("canonical_entity_id") or "")
        if cid:
            by_confirmed_cid.setdefault(cid, []).append(r)
    for grp in by_confirmed_cid.values():
        if len(grp) >= 2:
            confirmed_cluster_keys.add(_cluster_member_keys(grp))

    by_new_cid: Dict[str, List[Dict[str, Any]]] = {}
    for r in suggested_rows:
        cid = str(r.get("canonical_entity_id") or "")
        if cid:
            by_new_cid.setdefault(cid, []).append(r)

    filtered_suggested: List[Dict[str, Any]] = []
    for grp in by_new_cid.values():
        if len(grp) < 2:
            continue
        if _cluster_member_keys(grp) in confirmed_cluster_keys:
            continue
        grp_filtered = [
            r
            for r in grp
            if (str(r["source_member_id"]), str(r["source_entity_id"]))
            not in confirmed_entity_keys
        ]
        if len(grp_filtered) < 2:
            continue
        filtered_suggested.extend(grp_filtered)

    # semantic_entity_mappings.canonical_entity_id FK requires rows in canonical_entities first
    provisional_mappings = list(confirmed) + filtered_suggested
    if provisional_mappings:
        canonical_stubs = build_canonical_entities_from_mappings(
            graph_id=graph_id,
            mappings=provisional_mappings,
            inventory=inventory,
        )
        await store.upsert_canonical_entities(graph_id, canonical_stubs)

    all_rows = await store.replace_suggested_mappings(graph_id, filtered_suggested)
    cross_links = build_cross_links_from_rows(all_rows, members, inventory=inventory)

    # Count clusters with 2+ members (actual cross-links)
    by_c: Dict[str, int] = {}
    for r in all_rows:
        if (r.get("status") or "").lower() == "rejected":
            continue
        cid = str(r.get("canonical_entity_id") or "")
        by_c[cid] = by_c.get(cid, 0) + 1
    mapped_clusters = sum(1 for n in by_c.values() if n >= 2)

    return all_rows, cross_links, mapped_clusters


async def run_lake_proposal_for_graph(
    *,
    graph_id: str,
    user_id: str,
    run_id: Optional[str],
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    member_results: List[Dict[str, Any]],
    canonical_entities: List[Dict[str, Any]],
    on_progress=None,
) -> Dict[str, Any]:
    inventory = await build_inventory_from_member_results(
        members, member_results, user_id=user_id
    )
    mappings = await store.list_semantic_mappings(graph_id)
    graph = await store.get_graph(graph_id, user_id)
    graph_name = graph.name if graph else "Enterprise graph"
    proposal_payload = await build_lake_proposal(
        graph_id=graph_id,
        run_id=run_id,
        graph_settings=graph_settings,
        members=members,
        inventory=inventory,
        canonical_entities=canonical_entities,
        semantic_mappings=mappings,
        user_id=user_id,
        graph_name=graph_name,
        on_progress=on_progress,
    )
    if on_progress:
        await on_progress(91, "lake_save", "Saving lake proposal to database")
    return await store.create_lake_proposal(graph_id, proposal_payload)


async def run_warehouse_proposal_for_graph(
    *,
    graph_id: str,
    user_id: str,
    run_id: Optional[str],
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    canonical_entities: List[Dict[str, Any]],
    canonical_graph: Dict[str, Any],
    on_progress=None,
) -> Dict[str, Any]:
    graph = await store.get_graph(graph_id, user_id)
    graph_name = graph.name if graph else "Enterprise graph"
    mappings = await store.list_semantic_mappings(graph_id)
    from services.enterprise_graph.config import lake_enabled

    lake_proposal = None
    if lake_enabled(graph_settings):
        lake_proposal = await store.get_latest_lake_proposal(graph_id)
    proposal_payload = await build_warehouse_proposal(
        graph_id=graph_id,
        run_id=run_id,
        graph_settings=graph_settings,
        members=members,
        canonical_entities=canonical_entities,
        canonical_graph=canonical_graph,
        graph_name=graph_name,
        semantic_mappings=mappings,
        user_id=user_id,
        on_progress=on_progress,
        lake_proposal=lake_proposal,
    )
    if on_progress:
        await on_progress(94, "warehouse_save", "Saving warehouse proposal to database")
    return await store.create_warehouse_proposal(graph_id, proposal_payload)


async def refresh_latest_report_snapshot(
    graph_id: str,
    user_id: str,
    *,
    rebuild_warehouse: bool = False,
    rebuild_lake: bool = False,
) -> bool:
    """
    Refresh cross-links, canonical schema map, and mapped_entities on latest run.

    Mapping confirm/reject should use rebuild_warehouse=False so the API returns quickly.
    Full warehouse redesign belongs on assessment/remap/warehouse_redesign runs.
    """
    run = await store.get_latest_completed_run(graph_id, user_id)
    if not run or not run.get("report_snapshot"):
        return False
    graph = await store.get_graph(graph_id, user_id)
    if not graph:
        return False

    member_results = await load_member_results_from_assessments(user_id, graph.members)
    inventory = await build_inventory_from_member_results(
        graph.members, member_results, user_id=user_id
    )
    mappings = await store.list_semantic_mappings(graph_id)
    cross_links = build_cross_links_from_rows(mappings, graph.members, inventory=inventory)
    canonical_entities, canonical_graph, overlays = await run_canonical_build(
        graph_id=graph_id,
        user_id=user_id,
        members=graph.members,
        mappings=mappings,
        inventory=inventory,
    )
    await store.replace_canonical_entities(graph_id, canonical_entities)

    if rebuild_warehouse:
        proposal = await run_warehouse_proposal_for_graph(
            graph_id=graph_id,
            user_id=user_id,
            run_id=str(run["id"]),
            graph_settings=graph.settings or {},
            members=graph.members,
            canonical_entities=canonical_entities,
            canonical_graph=canonical_graph,
        )
        warehouse_section = _warehouse_proposal_section(proposal)
    else:
        existing = await store.get_latest_warehouse_proposal(graph_id)
        warehouse_section = (
            _warehouse_proposal_section(existing)
            if existing
            else (run.get("report_snapshot") or {})
            .get("sections", {})
            .get("warehouse_proposal")
        )

    from services.enterprise_graph.config import should_run_lake_phase

    if rebuild_lake and should_run_lake_phase(graph.settings or {}, graph.members):
        lake_proposal = await run_lake_proposal_for_graph(
            graph_id=graph_id,
            user_id=user_id,
            run_id=str(run["id"]),
            graph_settings=graph.settings or {},
            members=graph.members,
            member_results=member_results,
            canonical_entities=canonical_entities,
        )
        lake_section = _lake_proposal_section(lake_proposal)
    else:
        existing_lake = await store.get_latest_lake_proposal(graph_id)
        lake_section = (
            _lake_proposal_section(existing_lake)
            if existing_lake
            else (run.get("report_snapshot") or {})
            .get("sections", {})
            .get("lake_proposal")
        )

    snapshot = dict(run["report_snapshot"])
    scores = dict(snapshot.get("scores") or {})
    scores["mapped_entities"] = len(canonical_entities)
    scores["cross_links_count"] = len(cross_links)
    snapshot["scores"] = scores
    sections = dict(snapshot.get("sections") or {})
    connected = dict(sections.get("connected_environment") or {})
    connected["cross_links"] = cross_links
    sections["connected_environment"] = connected
    schema_map = dict(sections.get("schema_map") or {})
    schema_map["canonical_graph"] = canonical_graph
    schema_map["per_member_overlays"] = overlays
    sections["schema_map"] = schema_map
    if warehouse_section:
        sections["warehouse_proposal"] = warehouse_section
    if lake_section:
        sections["lake_proposal"] = lake_section
    snapshot["sections"] = sections

    await store.update_run(str(run["id"]), user_id, report_snapshot=snapshot)
    return True


def _lake_proposal_section(proposal: Dict[str, Any]) -> Dict[str, Any]:
    lm = proposal.get("logical_model") or {}
    summary = lm.get("summary") or {}
    zones = lm.get("zones") or {}
    return {
        "proposal_id": proposal.get("id"),
        "version": proposal.get("version"),
        "dialect": proposal.get("dialect"),
        "status": proposal.get("status"),
        "rationale": proposal.get("rationale"),
        "design_method": proposal.get("design_method") or summary.get("design_method") or "rules",
        "bronze_table_count": summary.get("bronze_table_count", 0),
        "silver_view_count": summary.get("silver_view_count", 0),
        "bronze_schema": zones.get("bronze_schema"),
        "silver_schema": zones.get("silver_schema"),
        "gap_count": len(proposal.get("gaps") or []),
        "load_spec_count": len(proposal.get("load_spec") or []),
    }


def _warehouse_proposal_section(proposal: Dict[str, Any]) -> Dict[str, Any]:
    lm = proposal.get("logical_model") or {}
    summary = lm.get("summary") or {}
    design_method = proposal.get("design_method") or lm.get("design_method") or "rules"
    excluded = lm.get("excluded_entities") or []
    return {
        "proposal_id": proposal.get("id"),
        "version": proposal.get("version"),
        "dialect": proposal.get("dialect"),
        "status": proposal.get("status"),
        "rationale": proposal.get("rationale"),
        "design_method": design_method,
        "excluded_entities": excluded,
        "excluded_entity_count": len(excluded),
        "table_count": summary.get("table_count", 0),
        "fact_count": summary.get("fact_count", 0),
        "dimension_count": summary.get("dimension_count", 0),
        "gap_count": len(proposal.get("gaps") or []),
    }


async def refresh_latest_report_cross_links(graph_id: str, user_id: str) -> bool:
    """Backward-compatible alias."""
    return await refresh_latest_report_snapshot(graph_id, user_id)
