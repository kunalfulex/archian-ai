"""Persistence for enterprise graphs and members."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.connection import database
from services.enterprise_graph.models import EnterpriseGraph, GraphMember


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _parse_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _row_to_member(row: Dict[str, Any]) -> GraphMember:
    return GraphMember(
        id=str(row["id"]),
        graph_id=str(row["graph_id"]),
        connector_type=row["connector_type"],
        connector_ref=_parse_json(row.get("connector_ref")) or {},
        role=row.get("role") or "source",
        display_name=row.get("display_name") or "",
        sort_order=int(row.get("sort_order") or 0),
        last_schema_sync_job_id=(
            str(row["last_schema_sync_job_id"]) if row.get("last_schema_sync_job_id") else None
        ),
        last_assessment_report_id=(
            str(row["last_assessment_report_id"]) if row.get("last_assessment_report_id") else None
        ),
    )


async def create_graph(
    *,
    user_id: str,
    name: str,
    description: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> str:
    graph_id = str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO enterprise_graphs (id, user_id, name, description, status, settings)
        VALUES (:id, :user_id, :name, :description, 'draft', CAST(:settings AS jsonb))
        """,
        {
            "id": graph_id,
            "user_id": user_id,
            "name": name.strip(),
            "description": description,
            "settings": _json_dumps(settings or {}),
        },
    )
    return graph_id


async def list_graphs(user_id: str, *, include_archived: bool = False) -> List[Dict[str, Any]]:
    status_filter = "" if include_archived else "AND status != 'archived'"
    rows = await database.fetch_all(
        f"""
        SELECT id, name, description, status, settings, latest_run_id, created_at, updated_at
        FROM enterprise_graphs
        WHERE user_id = :user_id {status_filter}
        ORDER BY updated_at DESC
        """,
        {"user_id": user_id},
    )
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        row = dict(r)
        row["settings"] = _parse_json(row.get("settings"))
        row["id"] = str(row["id"])
        if row.get("latest_run_id"):
            row["latest_run_id"] = str(row["latest_run_id"])
        out.append(row)
    return out


async def get_graph(graph_id: str, user_id: str) -> Optional[EnterpriseGraph]:
    row = await database.fetch_one(
        """
        SELECT * FROM enterprise_graphs
        WHERE id = :id AND user_id = :user_id
        """,
        {"id": graph_id, "user_id": user_id},
    )
    if not row:
        return None
    g = dict(row)
    members = await list_members(graph_id)
    return EnterpriseGraph(
        id=str(g["id"]),
        user_id=str(g["user_id"]),
        name=g["name"],
        description=g.get("description"),
        status=g.get("status") or "draft",
        settings=_parse_json(g.get("settings")) or {},
        latest_run_id=str(g["latest_run_id"]) if g.get("latest_run_id") else None,
        members=members,
    )


async def update_graph(
    graph_id: str,
    user_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> bool:
    sets = ["updated_at = NOW()"]
    values: Dict[str, Any] = {"id": graph_id, "user_id": user_id}
    if name is not None:
        sets.append("name = :name")
        values["name"] = name.strip()
    if description is not None:
        sets.append("description = :description")
        values["description"] = description
    if status is not None:
        sets.append("status = :status")
        values["status"] = status
    if settings is not None:
        sets.append("settings = CAST(:settings AS jsonb)")
        values["settings"] = _json_dumps(settings)
    if len(sets) == 1:
        return True
    result = await database.execute(
        f"""
        UPDATE enterprise_graphs SET {', '.join(sets)}
        WHERE id = :id AND user_id = :user_id
        """,
        values,
    )
    return bool(result)


async def archive_graph(graph_id: str, user_id: str) -> bool:
    return await update_graph(graph_id, user_id, status="archived")


async def list_members(graph_id: str) -> List[GraphMember]:
    rows = await database.fetch_all(
        """
        SELECT * FROM enterprise_graph_members
        WHERE graph_id = :graph_id
        ORDER BY sort_order ASC, created_at ASC
        """,
        {"graph_id": graph_id},
    )
    return [_row_to_member(dict(r)) for r in rows or []]


async def merge_graph_settings(
    graph_id: str,
    user_id: str,
    patch: Dict[str, Any],
) -> None:
    graph = await get_graph(graph_id, user_id)
    if not graph:
        return
    settings = dict(graph.settings or {})
    settings.update(patch)
    await update_graph(graph_id, user_id, settings=settings)


async def mark_assessment_stale(
    graph_id: str,
    user_id: str,
    *,
    reason: str = "members_changed",
) -> None:
    await merge_graph_settings(
        graph_id,
        user_id,
        {
            "assessment_stale": True,
            "assessment_stale_at": datetime.now(timezone.utc).isoformat(),
            "assessment_stale_reason": reason,
        },
    )


async def clear_assessment_stale(graph_id: str, user_id: str) -> None:
    graph = await get_graph(graph_id, user_id)
    if not graph:
        return
    settings = dict(graph.settings or {})
    for key in ("assessment_stale", "assessment_stale_at", "assessment_stale_reason"):
        settings.pop(key, None)
    await update_graph(graph_id, user_id, settings=settings)


async def delete_canonical_entities_for_graph(graph_id: str) -> None:
    """Remove all canonical rows for a graph (mappings cascade on member delete)."""
    await database.execute(
        "DELETE FROM canonical_entities WHERE graph_id = :graph_id",
        {"graph_id": graph_id},
    )


async def replace_members(
    graph_id: str,
    user_id: str,
    members: List[Dict[str, Any]],
) -> List[GraphMember]:
    graph = await get_graph(graph_id, user_id)
    if not graph:
        return []
    await database.execute(
        "DELETE FROM enterprise_graph_members WHERE graph_id = :graph_id",
        {"graph_id": graph_id},
    )
    await delete_canonical_entities_for_graph(graph_id)
    for idx, m in enumerate(members):
        await _insert_member(graph_id, m, sort_order=m.get("sort_order", idx))
    await database.execute(
        "UPDATE enterprise_graphs SET updated_at = NOW() WHERE id = :id",
        {"id": graph_id},
    )
    await mark_assessment_stale(graph_id, user_id, reason="members_changed")
    return await list_members(graph_id)


async def add_member(graph_id: str, member: Dict[str, Any]) -> str:
    return await _insert_member(
        graph_id,
        member,
        sort_order=int(member.get("sort_order", 0)),
    )


async def _insert_member(graph_id: str, member: Dict[str, Any], *, sort_order: int) -> str:
    member_id = str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO enterprise_graph_members (
            id, graph_id, connector_type, connector_ref, role, display_name, sort_order
        ) VALUES (
            :id, :graph_id, :connector_type, CAST(:connector_ref AS jsonb),
            :role, :display_name, :sort_order
        )
        """,
        {
            "id": member_id,
            "graph_id": graph_id,
            "connector_type": member["connector_type"],
            "connector_ref": _json_dumps(member["connector_ref"]),
            "role": member.get("role", "source"),
            "display_name": member.get("display_name", ""),
            "sort_order": sort_order,
        },
    )
    return member_id


async def remove_member(graph_id: str, member_id: str, *, user_id: Optional[str] = None) -> bool:
    await database.execute(
        """
        DELETE FROM enterprise_graph_members
        WHERE id = :member_id AND graph_id = :graph_id
        """,
        {"member_id": member_id, "graph_id": graph_id},
    )
    await delete_canonical_entities_for_graph(graph_id)
    await database.execute(
        "UPDATE enterprise_graphs SET updated_at = NOW() WHERE id = :id",
        {"id": graph_id},
    )
    if user_id:
        await mark_assessment_stale(graph_id, user_id, reason="member_removed")
    return True


async def update_member_assessment_report(
    member_id: str,
    graph_id: str,
    report_id: str,
) -> None:
    await database.execute(
        """
        UPDATE enterprise_graph_members
        SET last_assessment_report_id = :report_id, updated_at = NOW()
        WHERE id = :member_id AND graph_id = :graph_id
        """,
        {"member_id": member_id, "graph_id": graph_id, "report_id": report_id},
    )


async def create_run(
    *,
    graph_id: str,
    user_id: str,
    depth: str,
    run_type: str = "assessment",
) -> str:
    run_id = str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO enterprise_graph_runs (
            id, graph_id, user_id, run_type, depth, status, progress, member_jobs
        ) VALUES (
            :id, :graph_id, :user_id, :run_type, :depth, 'queued',
            CAST(:progress AS jsonb), CAST(:member_jobs AS jsonb)
        )
        """,
        {
            "id": run_id,
            "graph_id": graph_id,
            "user_id": user_id,
            "run_type": run_type,
            "depth": depth,
            "progress": _json_dumps({"percent": 0, "phase": "queued", "message": "Queued"}),
            "member_jobs": _json_dumps([]),
        },
    )
    return run_id


async def get_run(run_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM enterprise_graph_runs
        WHERE id = :id AND user_id = :user_id
        """,
        {"id": run_id, "user_id": user_id},
    )
    if not row:
        return None
    r = dict(row)
    r["id"] = str(r["id"])
    r["graph_id"] = str(r["graph_id"])
    r["progress"] = _parse_json(r.get("progress"))
    r["member_jobs"] = _parse_json(r.get("member_jobs"))
    r["report_snapshot"] = _parse_json(r.get("report_snapshot"))
    return r


async def get_active_run(graph_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM enterprise_graph_runs
        WHERE graph_id = :graph_id AND user_id = :user_id
          AND status IN ('queued', 'running', 'paused')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"graph_id": graph_id, "user_id": user_id},
    )
    if not row:
        return None
    return await get_run(str(row["id"]), user_id)


async def update_run(run_id: str, user_id: str, **fields: Any) -> None:
    sets = ["updated_at = NOW()"]
    values: Dict[str, Any] = {"id": run_id, "user_id": user_id}
    json_cols = {"progress", "member_jobs", "report_snapshot"}
    for key, val in fields.items():
        if key in json_cols:
            sets.append(f"{key} = CAST(:{key} AS jsonb)")
            values[key] = _json_dumps(val)
        else:
            sets.append(f"{key} = :{key}")
            values[key] = val
    await database.execute(
        f"""
        UPDATE enterprise_graph_runs SET {', '.join(sets)}
        WHERE id = :id AND user_id = :user_id
        """,
        values,
    )


async def set_graph_latest_run(graph_id: str, user_id: str, run_id: str) -> None:
    await database.execute(
        """
        UPDATE enterprise_graphs
        SET latest_run_id = :run_id, updated_at = NOW()
        WHERE id = :id AND user_id = :user_id
        """,
        {"id": graph_id, "user_id": user_id, "run_id": run_id},
    )


async def get_latest_completed_run(graph_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM enterprise_graph_runs
        WHERE graph_id = :graph_id AND user_id = :user_id AND status = 'completed'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"graph_id": graph_id, "user_id": user_id},
    )
    if not row:
        return None
    return await get_run(str(row["id"]), user_id)


async def get_member(graph_id: str, member_id: str) -> Optional[GraphMember]:
    row = await database.fetch_one(
        """
        SELECT * FROM enterprise_graph_members
        WHERE id = :member_id AND graph_id = :graph_id
        """,
        {"member_id": member_id, "graph_id": graph_id},
    )
    return _row_to_member(dict(row)) if row else None


def _row_to_mapping(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    r["id"] = str(r["id"])
    r["graph_id"] = str(r["graph_id"])
    if r.get("canonical_entity_id"):
        r["canonical_entity_id"] = str(r["canonical_entity_id"])
    r["source_member_id"] = str(r["source_member_id"])
    r["evidence"] = _parse_json(r.get("evidence")) or {}
    if r.get("confidence") is not None:
        r["confidence"] = float(r["confidence"])
    return r


async def prune_semantic_mappings_outside_inventory(
    graph_id: str,
    inventory: List[Any],
) -> int:
    """Remove mapping rows whose source entity is not in the current assessment inventory."""
    from services.enterprise_graph.entity_resolution import inventory_entity_keys

    valid = inventory_entity_keys(inventory)
    rows = await list_semantic_mappings(graph_id)
    removed = 0
    for row in rows:
        key = (str(row["source_member_id"]), str(row["source_entity_id"]))
        if key in valid:
            continue
        await database.execute(
            """
            DELETE FROM semantic_entity_mappings
            WHERE id = :id AND graph_id = :graph_id
            """,
            {"id": row["id"], "graph_id": graph_id},
        )
        removed += 1
    return removed


async def list_semantic_mappings(graph_id: str) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT * FROM semantic_entity_mappings
        WHERE graph_id = :graph_id
        ORDER BY confidence DESC NULLS LAST, created_at DESC
        """,
        {"graph_id": graph_id},
    )
    return [_row_to_mapping(dict(r)) for r in rows or []]


async def get_semantic_mapping(
    graph_id: str,
    mapping_id: str,
) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM semantic_entity_mappings
        WHERE id = :id AND graph_id = :graph_id
        """,
        {"id": mapping_id, "graph_id": graph_id},
    )
    return _row_to_mapping(dict(row)) if row else None


async def replace_suggested_mappings(
    graph_id: str,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Replace AI/heuristic suggestions; preserve confirmed mappings."""
    await database.execute(
        """
        DELETE FROM semantic_entity_mappings
        WHERE graph_id = :graph_id AND status = 'suggested'
        """,
        {"graph_id": graph_id},
    )
    if not rows:
        return await list_semantic_mappings(graph_id)

    for row in rows:
        member_id = str(row["source_member_id"])
        entity_id = str(row["source_entity_id"])

        existing = await database.fetch_one(
            """
            SELECT id, status FROM semantic_entity_mappings
            WHERE graph_id = :graph_id
              AND source_member_id = :member_id
              AND source_entity_id = :entity_id
            LIMIT 1
            """,
            {"graph_id": graph_id, "member_id": member_id, "entity_id": entity_id},
        )
        if existing:
            status = (dict(existing).get("status") or "").lower()
            if status == "confirmed":
                continue
            if status == "rejected":
                await database.execute(
                    """
                    DELETE FROM semantic_entity_mappings
                    WHERE id = :id AND graph_id = :graph_id
                    """,
                    {"id": str(dict(existing)["id"]), "graph_id": graph_id},
                )

        await database.execute(
            """
            INSERT INTO semantic_entity_mappings (
                id, graph_id, canonical_entity_id, source_member_id, source_entity_id,
                confidence, mapping_kind, evidence, status
            ) VALUES (
                :id, :graph_id, :canonical_entity_id, :source_member_id, :source_entity_id,
                :confidence, :mapping_kind, CAST(:evidence AS jsonb), :status
            )
            """,
            {
                "id": row["id"],
                "graph_id": graph_id,
                "canonical_entity_id": row["canonical_entity_id"],
                "source_member_id": member_id,
                "source_entity_id": entity_id,
                "confidence": row.get("confidence"),
                "mapping_kind": row.get("mapping_kind") or "heuristic",
                "evidence": _json_dumps(row.get("evidence") or {}),
                "status": row.get("status") or "suggested",
            },
        )
    return await list_semantic_mappings(graph_id)


async def update_mapping_status(
    graph_id: str,
    mapping_id: str,
    status: str,
) -> Optional[Dict[str, Any]]:
    await database.execute(
        """
        UPDATE semantic_entity_mappings
        SET status = :status, updated_at = NOW()
        WHERE id = :id AND graph_id = :graph_id
        """,
        {"id": mapping_id, "graph_id": graph_id, "status": status},
    )
    return await get_semantic_mapping(graph_id, mapping_id)


async def update_mappings_status_by_canonical(
    graph_id: str,
    canonical_entity_id: str,
    status: str,
) -> int:
    result = await database.execute(
        """
        UPDATE semantic_entity_mappings
        SET status = :status, updated_at = NOW()
        WHERE graph_id = :graph_id AND canonical_entity_id = :canonical_entity_id
        """,
        {
            "graph_id": graph_id,
            "canonical_entity_id": canonical_entity_id,
            "status": status,
        },
    )
    return int(result) if result else 0


async def reopen_confirmed_mappings(graph_id: str) -> int:
    """Set all confirmed mappings back to suggested for re-review."""
    result = await database.execute(
        """
        UPDATE semantic_entity_mappings
        SET status = 'suggested', updated_at = NOW()
        WHERE graph_id = :graph_id AND status = 'confirmed'
        """,
        {"graph_id": graph_id},
    )
    return int(result) if result else 0


async def update_canonical_mapping_entities(
    graph_id: str,
    canonical_entity_id: str,
    updates: List[Dict[str, str]],
) -> None:
    """
    Update source_entity_id per member in a canonical cluster.
    Each update: { "source_member_id", "source_entity_id" }.
    """
    for upd in updates:
        member_id = str(upd.get("source_member_id") or "").strip()
        entity_id = str(upd.get("source_entity_id") or "").strip()
        if not member_id or not entity_id:
            continue
        conflict = await database.fetch_one(
            """
            SELECT canonical_entity_id FROM semantic_entity_mappings
            WHERE graph_id = :graph_id
              AND source_member_id = :member_id
              AND source_entity_id = :entity_id
              AND canonical_entity_id IS DISTINCT FROM :canonical_entity_id
            LIMIT 1
            """,
            {
                "graph_id": graph_id,
                "member_id": member_id,
                "entity_id": entity_id,
                "canonical_entity_id": canonical_entity_id,
            },
        )
        if conflict:
            raise ValueError(
                f"Entity '{entity_id}' is already mapped on this connector in another cluster."
            )
        await database.execute(
            """
            UPDATE semantic_entity_mappings
            SET source_entity_id = :entity_id, updated_at = NOW()
            WHERE graph_id = :graph_id
              AND canonical_entity_id = :canonical_entity_id
              AND source_member_id = :member_id
            """,
            {
                "graph_id": graph_id,
                "canonical_entity_id": canonical_entity_id,
                "member_id": member_id,
                "entity_id": entity_id,
            },
        )


def _row_to_canonical(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    r["id"] = str(r["id"])
    r["graph_id"] = str(r["graph_id"])
    r["domain_tags"] = _parse_json(r.get("domain_tags")) or []
    r["attributes"] = _parse_json(r.get("attributes")) or {}
    r["source_lineage"] = _parse_json(r.get("source_lineage")) or []
    return r


async def list_canonical_entities(graph_id: str) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT * FROM canonical_entities
        WHERE graph_id = :graph_id
        ORDER BY name ASC
        """,
        {"graph_id": graph_id},
    )
    return [_row_to_canonical(dict(r)) for r in rows or []]


async def upsert_canonical_entities(
    graph_id: str,
    entities: List[Dict[str, Any]],
) -> None:
    """Insert or update canonical rows (required before semantic_entity_mappings FK insert)."""
    for ent in entities:
        await database.execute(
            """
            INSERT INTO canonical_entities (
                id, graph_id, name, domain_tags, attributes, source_lineage, version, status
            ) VALUES (
                :id, :graph_id, :name, CAST(:domain_tags AS jsonb),
                CAST(:attributes AS jsonb), CAST(:source_lineage AS jsonb),
                :version, :status
            )
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                domain_tags = EXCLUDED.domain_tags,
                attributes = EXCLUDED.attributes,
                source_lineage = EXCLUDED.source_lineage,
                version = EXCLUDED.version,
                status = EXCLUDED.status,
                updated_at = NOW()
            """,
            {
                "id": ent["id"],
                "graph_id": graph_id,
                "name": ent.get("name") or "Entity",
                "domain_tags": _json_dumps(ent.get("domain_tags") or []),
                "attributes": _json_dumps(ent.get("attributes") or {}),
                "source_lineage": _json_dumps(ent.get("source_lineage") or []),
                "version": int(ent.get("version") or 1),
                "status": ent.get("status") or "draft",
            },
        )


async def replace_canonical_entities(
    graph_id: str,
    entities: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Upsert canonical entities and remove orphans not in this assessment pass."""
    await upsert_canonical_entities(graph_id, entities)
    keep_ids = {str(ent["id"]) for ent in entities if ent.get("id")}
    if keep_ids:
        await database.execute(
            """
            DELETE FROM canonical_entities
            WHERE graph_id = :graph_id
              AND id != ALL(:keep_ids)
            """,
            {"graph_id": graph_id, "keep_ids": list(keep_ids)},
        )
    else:
        await delete_canonical_entities_for_graph(graph_id)
    return await list_canonical_entities(graph_id)


def _row_to_warehouse_proposal(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    r["id"] = str(r["id"])
    r["graph_id"] = str(r["graph_id"])
    if r.get("run_id"):
        r["run_id"] = str(r["run_id"])
    r["logical_model"] = _parse_json(r.get("logical_model")) or {}
    r["source_to_warehouse_map"] = _parse_json(r.get("source_to_warehouse_map")) or {}
    r["gaps"] = _parse_json(r.get("gaps")) or []
    return r


async def list_warehouse_proposals(graph_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT * FROM warehouse_schema_proposals
        WHERE graph_id = :graph_id
        ORDER BY version DESC, created_at DESC
        LIMIT :limit
        """,
        {"graph_id": graph_id, "limit": limit},
    )
    return [_row_to_warehouse_proposal(dict(r)) for r in rows or []]


async def list_warehouse_proposal_summaries(
    graph_id: str,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT id, graph_id, run_id, version, dialect, status, created_at, updated_at
        FROM warehouse_schema_proposals
        WHERE graph_id = :graph_id
        ORDER BY version DESC, created_at DESC
        LIMIT :limit
        """,
        {"graph_id": graph_id, "limit": limit},
    )
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        row = dict(r)
        row["id"] = str(row["id"])
        row["graph_id"] = str(row["graph_id"])
        if row.get("run_id"):
            row["run_id"] = str(row["run_id"])
        out.append(row)
    return out


async def get_warehouse_proposal(graph_id: str, proposal_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM warehouse_schema_proposals
        WHERE id = :id AND graph_id = :graph_id
        """,
        {"id": proposal_id, "graph_id": graph_id},
    )
    return _row_to_warehouse_proposal(dict(row)) if row else None


async def get_latest_warehouse_proposal(graph_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM warehouse_schema_proposals
        WHERE graph_id = :graph_id
        ORDER BY version DESC, created_at DESC
        LIMIT 1
        """,
        {"graph_id": graph_id},
    )
    return _row_to_warehouse_proposal(dict(row)) if row else None


async def _next_proposal_version(graph_id: str) -> int:
    row = await database.fetch_one(
        """
        SELECT COALESCE(MAX(version), 0) AS v
        FROM warehouse_schema_proposals
        WHERE graph_id = :graph_id
        """,
        {"graph_id": graph_id},
    )
    return int(dict(row)["v"] if row else 0) + 1


async def create_warehouse_proposal(
    graph_id: str,
    proposal: Dict[str, Any],
    *,
    supersede_drafts: bool = True,
) -> Dict[str, Any]:
    if supersede_drafts:
        await database.execute(
            """
            UPDATE warehouse_schema_proposals
            SET status = 'superseded', updated_at = NOW()
            WHERE graph_id = :graph_id AND status = 'draft'
            """,
            {"graph_id": graph_id},
        )

    version = await _next_proposal_version(graph_id)
    proposal_id = proposal.get("id") or str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO warehouse_schema_proposals (
            id, graph_id, run_id, version, dialect, logical_model,
            source_to_warehouse_map, ddl_text, rationale, gaps, status
        ) VALUES (
            :id, :graph_id, :run_id, :version, :dialect,
            CAST(:logical_model AS jsonb), CAST(:source_to_warehouse_map AS jsonb),
            :ddl_text, :rationale, CAST(:gaps AS jsonb), :status
        )
        """,
        {
            "id": proposal_id,
            "graph_id": graph_id,
            "run_id": proposal.get("run_id"),
            "version": version,
            "dialect": proposal.get("dialect") or "logical",
            "logical_model": _json_dumps(proposal.get("logical_model") or {}),
            "source_to_warehouse_map": _json_dumps(proposal.get("source_to_warehouse_map") or {}),
            "ddl_text": proposal.get("ddl_text"),
            "rationale": proposal.get("rationale"),
            "gaps": _json_dumps(proposal.get("gaps") or []),
            "status": proposal.get("status") or "draft",
        },
    )
    saved = await get_warehouse_proposal(graph_id, proposal_id)
    return saved or proposal


async def approve_warehouse_proposal(graph_id: str, proposal_id: str) -> Optional[Dict[str, Any]]:
    await database.execute(
        """
        UPDATE warehouse_schema_proposals
        SET status = 'superseded', updated_at = NOW()
        WHERE graph_id = :graph_id AND status = 'approved' AND id != :id
        """,
        {"graph_id": graph_id, "id": proposal_id},
    )
    await database.execute(
        """
        UPDATE warehouse_schema_proposals
        SET status = 'approved', updated_at = NOW()
        WHERE id = :id AND graph_id = :graph_id
        """,
        {"id": proposal_id, "graph_id": graph_id},
    )
    return await get_warehouse_proposal(graph_id, proposal_id)


def _row_to_lake_proposal(row: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(row)
    r["id"] = str(r["id"])
    r["graph_id"] = str(r["graph_id"])
    if r.get("run_id"):
        r["run_id"] = str(r["run_id"])
    r["logical_model"] = _parse_json(r.get("logical_model")) or {}
    r["source_to_lake_map"] = _parse_json(r.get("source_to_lake_map")) or {}
    r["load_spec"] = _parse_json(r.get("load_spec")) or []
    r["gaps"] = _parse_json(r.get("gaps")) or []
    r["load_status"] = _parse_json(r.get("load_status"))
    return r


async def list_lake_proposals(graph_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT * FROM lake_layout_proposals
        WHERE graph_id = :graph_id
        ORDER BY version DESC, created_at DESC
        LIMIT :limit
        """,
        {"graph_id": graph_id, "limit": limit},
    )
    return [_row_to_lake_proposal(dict(r)) for r in rows or []]


async def list_lake_proposal_summaries(
    graph_id: str,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT id, graph_id, run_id, version, dialect, status, created_at, updated_at
        FROM lake_layout_proposals
        WHERE graph_id = :graph_id
        ORDER BY version DESC, created_at DESC
        LIMIT :limit
        """,
        {"graph_id": graph_id, "limit": limit},
    )
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        row = dict(r)
        row["id"] = str(row["id"])
        row["graph_id"] = str(row["graph_id"])
        if row.get("run_id"):
            row["run_id"] = str(row["run_id"])
        out.append(row)
    return out


async def get_lake_proposal(graph_id: str, proposal_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM lake_layout_proposals
        WHERE id = :id AND graph_id = :graph_id
        """,
        {"id": proposal_id, "graph_id": graph_id},
    )
    return _row_to_lake_proposal(dict(row)) if row else None


async def get_latest_lake_proposal(graph_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM lake_layout_proposals
        WHERE graph_id = :graph_id
        ORDER BY version DESC, created_at DESC
        LIMIT 1
        """,
        {"graph_id": graph_id},
    )
    return _row_to_lake_proposal(dict(row)) if row else None


async def _next_lake_proposal_version(graph_id: str) -> int:
    row = await database.fetch_one(
        """
        SELECT COALESCE(MAX(version), 0) AS v
        FROM lake_layout_proposals
        WHERE graph_id = :graph_id
        """,
        {"graph_id": graph_id},
    )
    return int(dict(row)["v"] if row else 0) + 1


async def create_lake_proposal(
    graph_id: str,
    proposal: Dict[str, Any],
    *,
    supersede_drafts: bool = True,
) -> Dict[str, Any]:
    if supersede_drafts:
        await database.execute(
            """
            UPDATE lake_layout_proposals
            SET status = 'superseded', updated_at = NOW()
            WHERE graph_id = :graph_id AND status = 'draft'
            """,
            {"graph_id": graph_id},
        )

    version = await _next_lake_proposal_version(graph_id)
    proposal_id = proposal.get("id") or str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO lake_layout_proposals (
            id, graph_id, run_id, version, dialect, logical_model,
            source_to_lake_map, load_spec, ddl_text, rationale, gaps,
            load_status, status
        ) VALUES (
            :id, :graph_id, :run_id, :version, :dialect,
            CAST(:logical_model AS jsonb), CAST(:source_to_lake_map AS jsonb),
            CAST(:load_spec AS jsonb), :ddl_text, :rationale, CAST(:gaps AS jsonb),
            CAST(:load_status AS jsonb), :status
        )
        """,
        {
            "id": proposal_id,
            "graph_id": graph_id,
            "run_id": proposal.get("run_id"),
            "version": version,
            "dialect": proposal.get("dialect") or "snowflake",
            "logical_model": _json_dumps(proposal.get("logical_model") or {}),
            "source_to_lake_map": _json_dumps(proposal.get("source_to_lake_map") or {}),
            "load_spec": _json_dumps(proposal.get("load_spec") or []),
            "ddl_text": proposal.get("ddl_text"),
            "rationale": proposal.get("rationale"),
            "gaps": _json_dumps(proposal.get("gaps") or []),
            "load_status": _json_dumps(proposal.get("load_status"))
            if proposal.get("load_status") is not None
            else None,
            "status": proposal.get("status") or "draft",
        },
    )
    saved = await get_lake_proposal(graph_id, proposal_id)
    return saved or proposal


async def approve_lake_proposal(graph_id: str, proposal_id: str) -> Optional[Dict[str, Any]]:
    await database.execute(
        """
        UPDATE lake_layout_proposals
        SET status = 'superseded', updated_at = NOW()
        WHERE graph_id = :graph_id AND status = 'approved' AND id != :id
        """,
        {"graph_id": graph_id, "id": proposal_id},
    )
    await database.execute(
        """
        UPDATE lake_layout_proposals
        SET status = 'approved', updated_at = NOW()
        WHERE id = :id AND graph_id = :graph_id
        """,
        {"id": proposal_id, "graph_id": graph_id},
    )
    return await get_lake_proposal(graph_id, proposal_id)


async def mark_lake_proposal_ddl_executed(
    graph_id: str, proposal_id: str
) -> Optional[Dict[str, Any]]:
    await database.execute(
        """
        UPDATE lake_layout_proposals
        SET status = 'ddl_executed', updated_at = NOW()
        WHERE id = :id AND graph_id = :graph_id
        """,
        {"id": proposal_id, "graph_id": graph_id},
    )
    return await get_lake_proposal(graph_id, proposal_id)
