"""Propose bronze + silver lake layout from assessment inventory and canonical mappings."""
from __future__ import annotations

import re
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from services.enterprise_graph.config import (
    DEFAULT_LAKE_BRONZE_SCHEMA,
    DEFAULT_LAKE_SILVER_SCHEMA,
    LAKE_DIALECTS,
    PRODUCT_DISPLAY_NAME,
)
from services.enterprise_graph.inventory import SourceEntity, normalize_entity_name
from services.enterprise_graph.models import GraphMember
from services.enterprise_graph.warehouse_designer import (
    _safe_sql_identifier,
    load_warehouse_sql_connector_config,
    resolve_warehouse_target_dataset,
)

OnLakeProgress = Optional[Callable[..., Awaitable[None]]]

_LINEAGE_COLUMNS = (
    ("_archian_source_member_id", "VARCHAR"),
    ("_archian_source_entity_id", "VARCHAR"),
    ("_archian_loaded_at", "TIMESTAMP_NTZ"),
)


def resolve_lake_dialect(graph_settings: Dict[str, Any], members: List[GraphMember]) -> str:
    settings = graph_settings or {}
    explicit = (settings.get("lake_dialect") or "").strip().lower()
    if explicit in LAKE_DIALECTS:
        return explicit
    lake_member = next((m for m in members if (m.role or "").lower() == "lake"), None)
    if lake_member and (lake_member.connector_type or "").lower() == "sql":
        return "snowflake"
    return "logical"


def resolve_lake_schemas(
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    *,
    connector_config: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    settings = graph_settings or {}
    bronze = (settings.get("lake_bronze_schema") or DEFAULT_LAKE_BRONZE_SCHEMA).strip()
    silver = (settings.get("lake_silver_schema") or DEFAULT_LAKE_SILVER_SCHEMA).strip()
    if connector_config:
        from services.sql_connector_service import _snowflake_schema_name

        conn_schema = _snowflake_schema_name(connector_config)
        if conn_schema and bronze == DEFAULT_LAKE_BRONZE_SCHEMA:
            bronze = conn_schema
    return _safe_sql_identifier(bronze), _safe_sql_identifier(silver)


def normalize_lake_proposal_ddl(
    ddl_text: str,
    bronze_schema: str,
    silver_schema: str,
) -> str:
    """Rewrite bronze/silver schema qualifiers to match the lake Snowflake connection."""
    if not (ddl_text or "").strip():
        return ddl_text
    out = ddl_text
    for default, target in (
        (DEFAULT_LAKE_BRONZE_SCHEMA, bronze_schema),
        (DEFAULT_LAKE_SILVER_SCHEMA, silver_schema),
    ):
        if not (target or "").strip():
            continue
        for old in (default, default.upper(), default.lower()):
            if old.lower() == target.lower().strip('"'):
                continue
            pattern = re.compile(rf"\b{re.escape(old)}\.", re.IGNORECASE)
            out = pattern.sub(f"{target}.", out)
    return out


def _member_slug(member: GraphMember) -> str:
    raw = (member.display_name or member.id or "source")[:40]
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return slug or "source"


def _entity_slug(entity: SourceEntity) -> str:
    ct = (entity.connector_type or "").lower()
    label = (entity.label or "").strip()
    eid = entity.entity_id or ""

    if ct in ("google_sheets", "gsheets"):
        if " / " in label:
            workbook, sheet = label.rsplit(" / ", 1)
            base = normalize_entity_name(f"{workbook}_{sheet}")
        elif label:
            base = normalize_entity_name(label)
        elif "::" in eid:
            base = normalize_entity_name(eid.split("::", 1)[1])
        else:
            base = normalize_entity_name(eid)
    else:
        physical = normalize_entity_name(eid.split("::")[-1] if "::" in eid else eid)
        base = entity.normalized_name or normalize_entity_name(label) or physical

    slug = re.sub(r"[^a-z0-9]+", "_", (base or "entity").lower()).strip("_")
    return slug or "entity"


def bronze_table_name(
    entity: SourceEntity,
    member: Optional[GraphMember] = None,
) -> str:
    """Deterministic bronze table name without raw spreadsheet ids."""
    mslug = _member_slug(member) if member else re.sub(
        r"[^a-z0-9]+", "_", (entity.connector_type or "source").lower()
    ).strip("_") or "source"
    return f"raw__{mslug}__{_entity_slug(entity)}"


def _map_key(member_id: str, entity_id: str) -> str:
    return f"{member_id}:{entity_id}"


def _infer_sql_type(column_name: str) -> str:
    n = (column_name or "").lower()
    if n in ("id", "external_id", "record_id") or n.endswith("_id"):
        return "VARCHAR"
    if "email" in n:
        return "VARCHAR"
    if any(x in n for x in ("amount", "total", "price", "qty", "quantity", "count")):
        return "NUMBER"
    if any(x in n for x in ("date", "time", "timestamp", "created", "modified")):
        return "TIMESTAMP_NTZ"
    return "VARCHAR"


def build_bronze_tables(
    inventory: List[SourceEntity],
    members: List[GraphMember],
) -> List[Dict[str, Any]]:
    member_by_id = {m.id: m for m in members}
    tables: List[Dict[str, Any]] = []
    used_names: Set[str] = set()

    for ent in inventory:
        member = member_by_id.get(ent.member_id)
        if not member or (member.role or "").lower() != "source":
            continue
        base_name = bronze_table_name(ent, member)
        name = base_name
        suffix = 2
        while name in used_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(name)

        mslug = _member_slug(member)
        eslug = _entity_slug(ent)

        columns: List[Dict[str, Any]] = []
        seen_cols: Set[str] = set()
        for col in ent.column_names or []:
            cname = re.sub(r"[^a-zA-Z0-9_]+", "_", str(col)).strip("_") or "col"
            if cname.lower() in seen_cols:
                continue
            seen_cols.add(cname.lower())
            columns.append(
                {
                    "name": cname,
                    "type": _infer_sql_type(cname),
                    "source_column": col,
                    "nullable": True,
                }
            )
        if not columns:
            columns.append({"name": "payload", "type": "VARIANT", "nullable": True})

        for lc_name, lc_type in _LINEAGE_COLUMNS:
            columns.append({"name": lc_name, "type": lc_type, "lineage": True, "nullable": True})

        tables.append(
            {
                "name": name,
                "bronze_table": name,
                "source_member_id": ent.member_id,
                "source_entity_id": ent.entity_id,
                "source_connector_type": ent.connector_type,
                "member_slug": mslug,
                "entity_slug": eslug,
                "label": ent.label,
                "columns": columns,
            }
        )
    return tables


def build_source_to_lake_map(
    bronze_tables: List[Dict[str, Any]],
) -> Dict[str, Any]:
    mappings: List[Dict[str, Any]] = []
    for table in bronze_tables:
        column_map = {
            (c.get("source_column") or c["name"]): c["name"]
            for c in table.get("columns") or []
            if not c.get("lineage")
        }
        mappings.append(
            {
                "map_key": _map_key(table["source_member_id"], table["source_entity_id"]),
                "source_member_id": table["source_member_id"],
                "source_entity_id": table["source_entity_id"],
                "bronze_table": table["name"],
                "column_map": column_map,
            }
        )
    return {"mappings": mappings, "mapping_count": len(mappings)}


def build_load_spec(source_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for m in source_map.get("mappings") or []:
        entries.append(
            {
                "map_key": m.get("map_key"),
                "source_member_id": m.get("source_member_id"),
                "source_entity_id": m.get("source_entity_id"),
                "bronze_table": m.get("bronze_table"),
                "mode": "full",
                "operation": "insert",
                "batch_size": 1000,
                "extraction_job_id": None,
            }
        )
    return entries


def build_silver_views(
    *,
    canonical_entities: List[Dict[str, Any]],
    semantic_mappings: List[Dict[str, Any]],
    bronze_tables: List[Dict[str, Any]],
    bronze_schema: str,
    silver_schema: str,
    dialect: str,
) -> List[Dict[str, Any]]:
    bronze_by_key = {
        _map_key(t["source_member_id"], t["source_entity_id"]): t for t in bronze_tables
    }
    clusters: Dict[str, List[Dict[str, Any]]] = {}
    for row in semantic_mappings:
        status = (row.get("status") or "").lower()
        if status == "rejected":
            continue
        cid = str(row.get("canonical_entity_id") or "").strip()
        if not cid:
            continue
        clusters.setdefault(cid, []).append(row)

    canon_by_id = {str(e["id"]): e for e in canonical_entities if e.get("id")}
    views: List[Dict[str, Any]] = []
    used_view_names: Set[str] = set()

    for cid, rows in clusters.items():
        canon = canon_by_id.get(cid) or {}
        name_base = normalize_entity_name(canon.get("name") or cid[:8])
        domain = ""
        tags = canon.get("domain_tags") or []
        if isinstance(tags, list) and tags:
            domain = str(tags[0]).lower()
        view_name = f"stg__{domain or 'core'}__{name_base}" if domain else f"stg__{name_base}"
        view_name = re.sub(r"[^a-z0-9_]+", "_", view_name.lower()).strip("_")
        vname = view_name
        n = 2
        while vname in used_view_names:
            vname = f"{view_name}_{n}"
            n += 1
        used_view_names.add(vname)

        branches: List[str] = []
        source_bronze: List[str] = []
        for row in rows:
            key = _map_key(str(row["source_member_id"]), str(row["source_entity_id"]))
            bronze = bronze_by_key.get(key)
            if not bronze:
                continue
            source_bronze.append(bronze["name"])
            data_cols = [
                c["name"]
                for c in bronze.get("columns") or []
                if not c.get("lineage")
            ]
            if not data_cols:
                data_cols = ["payload"]
            select_cols = ", ".join(data_cols)
            if dialect == "snowflake":
                branches.append(
                    f"SELECT {select_cols}, "
                    f"'{row['source_member_id']}' AS _archian_source_member_id, "
                    f"'{row['source_entity_id']}' AS _archian_source_entity_id "
                    f"FROM {bronze_schema}.{bronze['name']}"
                )
            else:
                branches.append(
                    f"-- SELECT from {bronze['name']} ({row['source_entity_id']})"
                )

        if not branches:
            continue

        if dialect == "snowflake":
            body = "\nUNION ALL\n".join(branches)
            view_sql = (
                f"CREATE OR REPLACE VIEW {silver_schema}.{vname} AS\n{body};"
            )
        else:
            view_sql = f"-- VIEW {vname}\n" + "\n".join(branches)

        views.append(
            {
                "name": vname,
                "canonical_entity_id": cid,
                "canonical_name": canon.get("name"),
                "view_sql": view_sql,
                "source_bronze_tables": source_bronze,
                "branch_count": len(branches),
            }
        )
    return views


def build_logical_model(
    *,
    bronze_tables: List[Dict[str, Any]],
    silver_views: List[Dict[str, Any]],
    bronze_schema: str,
    silver_schema: str,
) -> Dict[str, Any]:
    return {
        "bronze_tables": bronze_tables,
        "silver_views": silver_views,
        "zones": {
            "bronze_schema": bronze_schema.strip('"'),
            "silver_schema": silver_schema.strip('"'),
        },
        "summary": {
            "bronze_table_count": len(bronze_tables),
            "silver_view_count": len(silver_views),
            "design_method": "rules",
        },
    }


def identify_lake_gaps(
    *,
    inventory: List[SourceEntity],
    bronze_tables: List[Dict[str, Any]],
    canonical_entities: List[Dict[str, Any]],
    silver_views: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    source_keys = {
        _map_key(t["source_member_id"], t["source_entity_id"]) for t in bronze_tables
    }
    for ent in inventory:
        key = _map_key(ent.member_id, ent.entity_id)
        if key not in source_keys:
            gaps.append(
                {
                    "category": "unmapped_source",
                    "severity": "medium",
                    "message": f"No bronze table for {ent.label}",
                    "source_member_id": ent.member_id,
                    "source_entity_id": ent.entity_id,
                }
            )
    modeled_canon = {v.get("canonical_entity_id") for v in silver_views}
    for ent in canonical_entities:
        cid = str(ent.get("id") or "")
        if cid and cid not in modeled_canon:
            gaps.append(
                {
                    "category": "unmapped_canonical",
                    "severity": "medium",
                    "message": f"No silver view for canonical entity {ent.get('name')}",
                    "canonical_entity_id": cid,
                }
            )
    return gaps


def generate_lake_ddl(
    logical_model: Dict[str, Any],
    dialect: str,
    *,
    bronze_schema: str,
    silver_schema: str,
) -> str:
    dialect = (dialect or "logical").lower()
    lines: List[str] = [
        f"-- {PRODUCT_DISPLAY_NAME} lake proposal ({dialect})",
        "-- Preview only: bronze tables + silver views",
        "",
    ]
    if dialect == "snowflake":
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {bronze_schema};")
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {silver_schema};")
        lines.append("")

    for table in logical_model.get("bronze_tables") or []:
        tname = table["name"]
        col_defs: List[str] = []
        for col in table.get("columns") or []:
            ctype = col.get("type") or "VARCHAR"
            col_defs.append(f"  {col['name']} {ctype}")
        if dialect == "snowflake":
            lines.append(f"CREATE TABLE IF NOT EXISTS {bronze_schema}.{tname} (")
            lines.append(",\n".join(col_defs) or "  placeholder VARCHAR")
            lines.append(");")
        else:
            lines.append(f"-- TABLE {bronze_schema}.{tname}")
            lines.extend(col_defs or ["  -- no columns"])
        lines.append("")

    for view in logical_model.get("silver_views") or []:
        if dialect == "snowflake" and view.get("view_sql"):
            lines.append(view["view_sql"])
        else:
            lines.append(view.get("view_sql") or f"-- VIEW {view.get('name')}")
        lines.append("")

    return "\n".join(lines).strip()


def build_lake_rationale(logical_model: Dict[str, Any], dialect: str, gaps: List[Dict[str, Any]]) -> str:
    summary = logical_model.get("summary") or {}
    parts = [
        f"Proposed lake layout with {summary.get('bronze_table_count', 0)} bronze table(s) "
        f"and {summary.get('silver_view_count', 0)} silver view(s) ({dialect} preview).",
    ]
    if gaps:
        parts.append(f"{len(gaps)} gap(s) noted for review.")
    return " ".join(parts)


async def _emit_lake_progress(
    on_progress: OnLakeProgress,
    percent: int,
    sub_step: str,
    message: str,
) -> None:
    if on_progress:
        await on_progress(percent, sub_step, message)


async def build_lake_proposal(
    *,
    graph_id: str,
    run_id: Optional[str],
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    inventory: List[SourceEntity],
    canonical_entities: List[Dict[str, Any]],
    semantic_mappings: List[Dict[str, Any]],
    user_id: Optional[str] = None,
    graph_name: str = "Enterprise graph",
    on_progress: OnLakeProgress = None,
) -> Dict[str, Any]:
    """Full lake proposal payload ready for persistence (LLM-first, rules fallback)."""
    await _emit_lake_progress(
        on_progress, 88, "lake_context", "Loading inventory for lake design"
    )
    dialect = resolve_lake_dialect(graph_settings, members)
    connector_config = None
    if user_id:
        connector_config = await load_warehouse_sql_connector_config(user_id, members)
        if not connector_config:
            from services.enterprise_graph.validation import connector_id_from_ref

            lake_member = next((m for m in members if (m.role or "").lower() == "lake"), None)
            if lake_member and user_id:
                from db.connection import database

                cid = connector_id_from_ref(
                    lake_member.connector_type, lake_member.connector_ref
                )
                if cid:
                    from services.sql_connector_service import SQLConnectorService

                    row = await database.fetch_one(
                        """
                        SELECT * FROM sql_connectors
                        WHERE id = :id AND user_id = :user_id AND is_active = true
                        LIMIT 1
                        """,
                        {"id": cid, "user_id": str(user_id)},
                    )
                    if row:
                        connector_config = SQLConnectorService().prepare_connector_config(
                            dict(row)
                        )

    bronze_schema, silver_schema = resolve_lake_schemas(
        graph_settings, members, connector_config=connector_config
    )

    import logging

    from services.semantic_mapping.config import lake_llm_enabled, lake_llm_fallback_enabled

    logger = logging.getLogger(__name__)
    logical_model: Dict[str, Any]
    llm_rationale: Optional[str] = None
    design_method = "rules"

    if lake_llm_enabled() and user_id:
        try:
            from services.semantic_mapping.lake_designer_llm import propose_lake_logical_model_llm

            await _emit_lake_progress(
                on_progress, 89, "lake_ai", "AI is designing medallion lake layout"
            )
            logical_model, llm_rationale = await propose_lake_logical_model_llm(
                graph_name=graph_name,
                graph_settings=graph_settings,
                members=members,
                inventory=inventory,
                user_id=user_id,
                semantic_mappings=semantic_mappings,
                canonical_entities=canonical_entities,
                dialect=dialect,
                bronze_schema=bronze_schema,
                silver_schema=silver_schema,
            )
            design_method = "llm"
        except Exception as exc:
            if not lake_llm_fallback_enabled():
                raise
            logger.warning("Lake LLM failed, using rules fallback: %s", exc)
            design_method = "rules_fallback"

    if design_method in ("rules", "rules_fallback"):
        await _emit_lake_progress(on_progress, 89, "lake_bronze", "Designing bronze tables")
        bronze_tables = build_bronze_tables(inventory, members)
        await _emit_lake_progress(on_progress, 90, "lake_silver", "Designing silver views")
        silver_views = build_silver_views(
            canonical_entities=canonical_entities,
            semantic_mappings=semantic_mappings,
            bronze_tables=bronze_tables,
            bronze_schema=bronze_schema,
            silver_schema=silver_schema,
            dialect=dialect,
        )
        logical_model = build_logical_model(
            bronze_tables=bronze_tables,
            silver_views=silver_views,
            bronze_schema=bronze_schema,
            silver_schema=silver_schema,
        )
        if design_method == "rules_fallback":
            summary = dict(logical_model.get("summary") or {})
            summary["design_method"] = "rules_fallback"
            logical_model["summary"] = summary

    bronze_tables = logical_model.get("bronze_tables") or []
    silver_views = logical_model.get("silver_views") or []
    source_map = build_source_to_lake_map(bronze_tables)
    load_spec = build_load_spec(source_map)
    gaps = identify_lake_gaps(
        inventory=inventory,
        bronze_tables=bronze_tables,
        canonical_entities=canonical_entities,
        silver_views=silver_views,
    )

    await _emit_lake_progress(on_progress, 90, "lake_ddl", f"Generating {dialect} DDL")
    ddl_text = generate_lake_ddl(
        logical_model,
        dialect,
        bronze_schema=bronze_schema,
        silver_schema=silver_schema,
    )
    rationale = llm_rationale or build_lake_rationale(logical_model, dialect, gaps)

    return {
        "id": str(uuid.uuid4()),
        "graph_id": graph_id,
        "run_id": run_id,
        "dialect": dialect,
        "logical_model": logical_model,
        "source_to_lake_map": source_map,
        "load_spec": load_spec,
        "ddl_text": ddl_text,
        "rationale": rationale,
        "gaps": gaps,
        "load_status": None,
        "status": "draft",
        "design_method": "rules",
    }
