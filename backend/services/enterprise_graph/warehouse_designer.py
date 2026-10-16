"""Propose star-schema warehouse design from canonical model (no DDL execution)."""
from __future__ import annotations

import re
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

# (percent, sub_step, message, heartbeat?) — real milestones; heartbeat only during long LLM wait.
OnWarehouseProgress = Optional[Callable[..., Awaitable[None]]]

_WAREHOUSE_LLM_HEARTBEAT_SECONDS = 2.0

from db.connection import database
from services.enterprise_graph.config import DEFAULT_WAREHOUSE_DATASET, PRODUCT_DISPLAY_NAME
from services.enterprise_graph.inventory import normalize_entity_name
from services.enterprise_graph.models import GraphMember
from services.enterprise_graph.validation import connector_id_from_ref
from services.sql_connector_service import SQLConnectorService, _snowflake_schema_name

WAREHOUSE_DIALECTS = frozenset({"logical", "bigquery", "snowflake", "postgres"})
FACT_DOMAINS = frozenset({"commerce", "sales"})
DIM_DOMAINS = frozenset({"party", "catalog", "finance", "support"})

_TYPE_MAP = {
    "logical": {"default": "STRING", "id": "STRING", "email": "STRING", "amount": "NUMERIC", "date": "DATE"},
    "bigquery": {"default": "STRING", "id": "STRING", "email": "STRING", "amount": "NUMERIC", "date": "DATE"},
    "snowflake": {"default": "VARCHAR", "id": "VARCHAR", "email": "VARCHAR", "amount": "NUMBER", "date": "DATE"},
    "postgres": {"default": "TEXT", "id": "TEXT", "email": "TEXT", "amount": "NUMERIC", "date": "DATE"},
}


def resolve_warehouse_dialect(
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
) -> str:
    """Dialect from graph settings, warehouse member config, or logical default."""
    settings = graph_settings or {}
    explicit = (settings.get("warehouse_dialect") or "").strip().lower()
    if explicit in WAREHOUSE_DIALECTS:
        return explicit

    for m in members:
        if (m.role or "").lower() != "warehouse":
            continue
        ref = m.connector_ref or {}
        dialect = (ref.get("warehouse_dialect") or ref.get("dialect") or "").strip().lower()
        if dialect in WAREHOUSE_DIALECTS:
            return dialect
        target = (ref.get("target_kind") or ref.get("additional_config", {}).get("target_kind") or "")
        if "bigquery" in str(target).lower():
            return "bigquery"
        if "snowflake" in str(target).lower():
            return "snowflake"

    return "logical"


def _safe_sql_identifier(name: str) -> str:
    """Escape a schema/dataset name for DDL (Snowflake/BQ-style identifiers)."""
    n = (name or "").strip()
    if not n:
        return DEFAULT_WAREHOUSE_DATASET
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", n):
        return n
    return '"' + n.replace('"', '""') + '"'


def resolve_warehouse_target_dataset(
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    dialect: str,
    *,
    connector_config: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Target namespace for warehouse DDL.

    For Snowflake this is the **schema** (not the compute warehouse). Prefer the
    warehouse SQL connector's configured schema; never default to archian_warehouse
    when a Snowflake connection is present.
    """
    settings = graph_settings or {}
    explicit = (settings.get("warehouse_target_schema") or settings.get("target_dataset") or "").strip()
    if explicit:
        return _safe_sql_identifier(explicit)

    dialect = (dialect or "logical").lower()
    if connector_config:
        if dialect == "snowflake":
            schema = _snowflake_schema_name(connector_config)
            if schema:
                return _safe_sql_identifier(schema)
        if dialect == "bigquery":
            db = (connector_config.get("database_name") or "").strip()
            if db:
                return _safe_sql_identifier(db)

    return DEFAULT_WAREHOUSE_DATASET


async def load_warehouse_sql_connector_config(
    user_id: str,
    members: List[GraphMember],
) -> Optional[Dict[str, Any]]:
    """Load prepared config for the graph's warehouse SQL member, if any."""
    warehouse_member = next((m for m in members if (m.role or "").lower() == "warehouse"), None)
    if not warehouse_member or (warehouse_member.connector_type or "").lower() != "sql":
        return None
    connector_id = connector_id_from_ref(warehouse_member.connector_type, warehouse_member.connector_ref)
    if not connector_id:
        return None
    row = await database.fetch_one(
        """
        SELECT * FROM sql_connectors
        WHERE id = :id AND user_id = :user_id AND is_active = true
        LIMIT 1
        """,
        {"id": connector_id, "user_id": str(user_id)},
    )
    if not row:
        return None
    return SQLConnectorService().prepare_connector_config(dict(row))


def normalize_snowflake_proposal_ddl(ddl_text: str, target_schema: str) -> str:
    """
    Rewrite legacy proposals that used DEFAULT_WAREHOUSE_DATASET (archian_warehouse)
    so CREATE TABLE targets the Snowflake schema from the connection.
    """
    if not (ddl_text or "").strip() or not (target_schema or "").strip():
        return ddl_text
    schema = _safe_sql_identifier(target_schema)
    replacements = {
        DEFAULT_WAREHOUSE_DATASET,
        DEFAULT_WAREHOUSE_DATASET.upper(),
        "archian_warehouse",
        "ARCHIAN_WAREHOUSE",
    }
    out = ddl_text
    for old in replacements:
        if old.lower() == schema.lower().strip('"'):
            continue
        pattern = re.compile(rf"\b{re.escape(old)}\.", re.IGNORECASE)
        out = pattern.sub(f"{schema}.", out)
    return out


def _table_slug(name: str, table_type: str, used: Set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (name or "entity").lower()).strip("_")
    if not base:
        base = "entity"
    prefix = "fact" if table_type == "fact" else "dim"
    candidate = f"{prefix}_{base}"
    n = 2
    while candidate in used:
        candidate = f"{prefix}_{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate


def _infer_column_type(col_name: str, dialect: str) -> str:
    norm = normalize_entity_name(col_name)
    types = _TYPE_MAP.get(dialect, _TYPE_MAP["logical"])
    if norm in ("id", "key", "uuid", "external_id") or norm.endswith("_id"):
        return types["id"]
    if "email" in norm:
        return types["email"]
    if any(x in norm for x in ("amount", "price", "total", "revenue")):
        return types["amount"]
    if any(x in norm for x in ("date", "time", "created", "updated")):
        return types["date"]
    return types["default"]


def _classify_table_type(entity: Dict[str, Any], out_degree: int, in_degree: int) -> str:
    tags = set(entity.get("domain_tags") or [])
    if tags & FACT_DOMAINS:
        return "fact"
    if tags & DIM_DOMAINS:
        return "dimension"
    if out_degree >= 1 and in_degree == 0:
        return "fact"
    if in_degree >= 1:
        return "dimension"
    return "dimension"


def get_silver_column_catalog(
    lake_logical_model: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Map canonical_entity_id → silver view name and column names from bronze lineage."""
    bronze_by_name = {
        t["name"]: t for t in (lake_logical_model.get("bronze_tables") or []) if t.get("name")
    }
    zones = lake_logical_model.get("zones") or {}
    silver_schema = zones.get("silver_schema") or ""
    catalog: Dict[str, Dict[str, Any]] = {}
    for view in lake_logical_model.get("silver_views") or []:
        cid = str(view.get("canonical_entity_id") or "").strip()
        if not cid:
            continue
        cols: Set[str] = set()
        for bt_name in view.get("source_bronze_tables") or []:
            bronze = bronze_by_name.get(bt_name)
            if not bronze:
                continue
            for col in bronze.get("columns") or []:
                if col.get("lineage"):
                    continue
                name = col.get("name")
                if name:
                    cols.add(str(name))
        catalog[cid] = {
            "view_name": view.get("name"),
            "silver_schema": silver_schema,
            "columns": sorted(cols),
        }
    return catalog


def build_logical_model_with_lake_silver(
    *,
    canonical_entities: List[Dict[str, Any]],
    canonical_graph: Dict[str, Any],
    lake_logical_model: Dict[str, Any],
) -> Dict[str, Any]:
    """Star schema with column lineage from lake silver views (not live connectors)."""
    catalog = get_silver_column_catalog(lake_logical_model)
    edges = canonical_graph.get("edges") or []
    out_deg: Dict[str, int] = {}
    in_deg: Dict[str, int] = {}
    for e in edges:
        src = e.get("source_id")
        tgt = e.get("target_id")
        if src and tgt:
            out_deg[src] = out_deg.get(src, 0) + 1
            in_deg[tgt] = in_deg.get(tgt, 0) + 1

    used_names: Set[str] = set()
    table_by_canonical: Dict[str, Dict[str, Any]] = {}

    for ent in canonical_entities:
        cid = ent["id"]
        silver = catalog.get(str(cid)) or {}
        ttype = _classify_table_type(ent, out_deg.get(cid, 0), in_deg.get(cid, 0))
        tname = _table_slug(ent.get("name") or "entity", ttype, used_names)
        columns: List[Dict[str, Any]] = [
            {
                "name": f"{tname}_key",
                "type": "STRING",
                "role": "surrogate_key",
                "nullable": False,
            }
        ]
        lineage_cols: List[Dict[str, Any]] = []
        silver_view = silver.get("view_name") or ""
        silver_schema = silver.get("silver_schema") or ""
        for cname in silver.get("columns") or []:
            norm = re.sub(r"[^a-zA-Z0-9_]+", "_", str(cname)).strip("_").lower()
            if not norm or norm.endswith("_key"):
                continue
            if any(c["name"] == norm for c in columns):
                continue
            columns.append(
                {
                    "name": norm,
                    "type": _infer_column_type(norm, "logical"),
                    "role": "attribute",
                    "nullable": True,
                }
            )
            lineage_cols.append(
                {
                    "warehouse_column": norm,
                    "source_layer": "silver",
                    "silver_view": silver_view,
                    "silver_schema": silver_schema,
                    "source_column": cname,
                    "canonical_entity_id": cid,
                }
            )

        table = {
            "id": str(uuid.uuid4()),
            "name": tname,
            "table_type": ttype,
            "canonical_entity_id": cid,
            "canonical_name": ent.get("name"),
            "domain_tags": ent.get("domain_tags") or [],
            "grain": _grain_for_type(ttype, ent.get("name")),
            "columns": columns,
            "foreign_keys": [],
            "lineage": lineage_cols,
            "source_catalog": "lake_silver",
        }
        table_by_canonical[cid] = table

    relationships: List[Dict[str, Any]] = []
    for e in edges:
        src_c = e.get("source_id")
        tgt_c = e.get("target_id")
        src_t = table_by_canonical.get(src_c or "")
        tgt_t = table_by_canonical.get(tgt_c or "")
        if not src_t or not tgt_t:
            continue
        fk_col = f"{tgt_t['name']}_key"
        if not any(fk.get("column") == fk_col for fk in src_t["foreign_keys"]):
            src_t["foreign_keys"].append(
                {
                    "column": fk_col,
                    "references_table": tgt_t["name"],
                    "references_column": f"{tgt_t['name']}_key",
                    "relationship_label": e.get("label"),
                }
            )
        relationships.append(
            {
                "from_table": src_t["name"],
                "to_table": tgt_t["name"],
                "cardinality": "many_to_one",
                "via_column": fk_col,
            }
        )

    tables = list(table_by_canonical.values())
    facts = [t for t in tables if t["table_type"] == "fact"]
    dims = [t for t in tables if t["table_type"] == "dimension"]
    return {
        "model_type": "star",
        "tables": tables,
        "facts": [t["name"] for t in facts],
        "dimensions": [t["name"] for t in dims],
        "relationships": relationships,
        "summary": {
            "table_count": len(tables),
            "fact_count": len(facts),
            "dimension_count": len(dims),
            "relationship_count": len(relationships),
        },
        "source_catalog": "lake_silver",
    }


def build_logical_model(
    *,
    canonical_entities: List[Dict[str, Any]],
    canonical_graph: Dict[str, Any],
) -> Dict[str, Any]:
    """Star-oriented logical model: fact/dimension tables with columns and FKs."""
    edges = canonical_graph.get("edges") or []
    out_deg: Dict[str, int] = {}
    in_deg: Dict[str, int] = {}
    for e in edges:
        src = e.get("source_id")
        tgt = e.get("target_id")
        if src and tgt:
            out_deg[src] = out_deg.get(src, 0) + 1
            in_deg[tgt] = in_deg.get(tgt, 0) + 1

    used_names: Set[str] = set()
    entity_by_id = {e["id"]: e for e in canonical_entities}
    table_by_canonical: Dict[str, Dict[str, Any]] = {}

    for ent in canonical_entities:
        cid = ent["id"]
        ttype = _classify_table_type(ent, out_deg.get(cid, 0), in_deg.get(cid, 0))
        tname = _table_slug(ent.get("name") or "entity", ttype, used_names)
        attrs = ent.get("attributes") or {}
        raw_cols = attrs.get("columns") or []
        columns: List[Dict[str, Any]] = [
            {
                "name": f"{tname}_key",
                "type": "STRING",
                "role": "surrogate_key",
                "nullable": False,
            }
        ]
        lineage_cols: List[Dict[str, Any]] = []
        for col in raw_cols[:25]:
            if isinstance(col, dict):
                cname = re.sub(
                    r"[^a-zA-Z0-9_]+", "_", str(col.get("name") or "")
                ).strip("_").lower()
                col_type = col.get("data_type") or "STRING"
            else:
                cname = re.sub(r"[^a-zA-Z0-9_]+", "_", str(col)).strip("_").lower()
                col_type = _infer_column_type(cname, "logical")
            if not cname or cname.endswith("_key"):
                continue
            if cname in ("by_field", "percentages", "total_missing") or "inconsistency" in cname:
                continue
            if any(c["name"] == cname for c in columns):
                continue
            columns.append(
                {
                    "name": cname,
                    "type": col_type if isinstance(col, dict) else _infer_column_type(cname, "logical"),
                    "role": "attribute",
                    "nullable": col.get("nullable", True) if isinstance(col, dict) else True,
                }
            )
            for lin in ent.get("source_lineage") or []:
                lineage_cols.append(
                    {
                        "warehouse_column": cname,
                        "source_member_id": lin.get("member_id"),
                        "source_entity_id": lin.get("entity_id"),
                        "source_column": cname,
                    }
                )

        table = {
            "id": str(uuid.uuid4()),
            "name": tname,
            "table_type": ttype,
            "canonical_entity_id": cid,
            "canonical_name": ent.get("name"),
            "domain_tags": ent.get("domain_tags") or [],
            "grain": _grain_for_type(ttype, ent.get("name")),
            "columns": columns,
            "foreign_keys": [],
            "lineage": lineage_cols,
        }
        table_by_canonical[cid] = table

    relationships: List[Dict[str, Any]] = []
    for e in edges:
        src_c = e.get("source_id")
        tgt_c = e.get("target_id")
        src_t = table_by_canonical.get(src_c or "")
        tgt_t = table_by_canonical.get(tgt_c or "")
        if not src_t or not tgt_t:
            continue
        fk_col = f"{tgt_t['name']}_key"
        if not any(fk.get("column") == fk_col for fk in src_t["foreign_keys"]):
            src_t["foreign_keys"].append(
                {
                    "column": fk_col,
                    "references_table": tgt_t["name"],
                    "references_column": f"{tgt_t['name']}_key",
                    "relationship_label": e.get("label"),
                }
            )
        relationships.append(
            {
                "from_table": src_t["name"],
                "to_table": tgt_t["name"],
                "cardinality": "many_to_one",
                "via_column": fk_col,
            }
        )

    tables = list(table_by_canonical.values())
    facts = [t for t in tables if t["table_type"] == "fact"]
    dims = [t for t in tables if t["table_type"] == "dimension"]

    return {
        "model_type": "star",
        "tables": tables,
        "facts": [t["name"] for t in facts],
        "dimensions": [t["name"] for t in dims],
        "relationships": relationships,
        "summary": {
            "table_count": len(tables),
            "fact_count": len(facts),
            "dimension_count": len(dims),
            "relationship_count": len(relationships),
        },
    }


def _grain_for_type(table_type: str, name: Optional[str]) -> str:
    label = name or "entity"
    if table_type == "fact":
        return f"One row per {label.lower()} transaction or event"
    return f"One row per {label.lower()}"


def build_source_to_warehouse_map(logical_model: Dict[str, Any]) -> Dict[str, Any]:
    """Column-level lineage from canonical source entities to warehouse columns."""
    mappings: List[Dict[str, Any]] = []
    for table in logical_model.get("tables") or []:
        for lin in table.get("lineage") or []:
            entry = {
                "warehouse_table": table["name"],
                "warehouse_column": lin.get("warehouse_column"),
                "source_member_id": lin.get("source_member_id"),
                "source_entity_id": lin.get("source_entity_id"),
                "source_column": lin.get("source_column"),
                "canonical_entity_id": table.get("canonical_entity_id"),
            }
            if lin.get("source_layer") == "silver":
                entry["source_layer"] = "silver"
                entry["silver_view"] = lin.get("silver_view")
                entry["silver_schema"] = lin.get("silver_schema")
            mappings.append(entry)
        sk = next((c["name"] for c in table.get("columns") or [] if c.get("role") == "surrogate_key"), None)
        if sk:
            mappings.append(
                {
                    "warehouse_table": table["name"],
                    "warehouse_column": sk,
                    "generated": True,
                    "canonical_entity_id": table.get("canonical_entity_id"),
                }
            )
    return {"mappings": mappings, "mapping_count": len(mappings)}


def identify_gaps(
    *,
    canonical_entities: List[Dict[str, Any]],
    logical_model: Dict[str, Any],
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    modeled_ids = {t.get("canonical_entity_id") for t in logical_model.get("tables") or []}
    for ent in canonical_entities:
        if ent["id"] not in modeled_ids:
            gaps.append(
                {
                    "category": "unmapped_entity",
                    "severity": "medium",
                    "message": f"No warehouse table for canonical entity {ent.get('name')}",
                    "canonical_entity_id": ent["id"],
                }
            )
    for table in logical_model.get("tables") or []:
        attrs = [c for c in table.get("columns") or [] if c.get("role") == "attribute"]
        if len(attrs) == 0:
            gaps.append(
                {
                    "category": "sparse_columns",
                    "severity": "low",
                    "message": f"Table {table['name']} has no source attributes inferred",
                    "warehouse_table": table["name"],
                }
            )
    return gaps


def generate_ddl(
    logical_model: Dict[str, Any],
    dialect: str,
    *,
    dataset: str = DEFAULT_WAREHOUSE_DATASET,
) -> str:
    """Generate dialect-specific DDL text (preview only — not executed)."""
    dialect = (dialect or "logical").lower()
    if dialect not in WAREHOUSE_DIALECTS:
        dialect = "logical"

    lines: List[str] = [
        f"-- {PRODUCT_DISPLAY_NAME} warehouse proposal ({dialect})",
        "-- Preview only: not executed automatically",
        "",
    ]

    for table in logical_model.get("tables") or []:
        tname = table["name"]
        cols = table.get("columns") or []
        col_defs: List[str] = []

        for col in cols:
            cname = col["name"]
            ctype = _infer_column_type(cname, dialect) if col.get("role") != "surrogate_key" else _TYPE_MAP[dialect]["id"]
            null_sql = "" if col.get("nullable") is False else ""
            if dialect == "bigquery":
                col_defs.append(f"  {cname} {ctype}{' NOT NULL' if not col.get('nullable', True) else ''}")
            elif dialect == "snowflake":
                col_defs.append(f"  {cname} {ctype}{' NOT NULL' if not col.get('nullable', True) else ''}")
            elif dialect == "postgres":
                col_defs.append(f"  {cname} {ctype}{' NOT NULL' if not col.get('nullable', True) else ''}")
            else:
                col_defs.append(f"  -- {cname} {ctype}{null_sql}")

        if dialect == "bigquery":
            lines.append(f"CREATE TABLE IF NOT EXISTS `{dataset}.{tname}` (")
            lines.append(",\n".join(col_defs) or "  placeholder STRING")
            lines.append(");")
        elif dialect == "snowflake":
            schema = _safe_sql_identifier(dataset)
            lines.append(f"CREATE TABLE IF NOT EXISTS {schema}.{tname} (")
            lines.append(",\n".join(col_defs) or "  placeholder VARCHAR")
            lines.append(");")
        elif dialect == "postgres":
            lines.append(f"CREATE TABLE IF NOT EXISTS {dataset}.{tname} (")
            lines.append(",\n".join(col_defs) or "  placeholder TEXT")
            lines.append(");")
        else:
            lines.append(f"-- TABLE {tname} ({table.get('table_type')})")
            lines.extend(col_defs or ["  -- no columns inferred"])
        lines.append("")

    return "\n".join(lines).strip()


def build_rationale(logical_model: Dict[str, Any], dialect: str, gaps: List[Dict[str, Any]]) -> str:
    summary = logical_model.get("summary") or {}
    parts = [
        f"Proposed {summary.get('model_type', 'star')} schema with "
        f"{summary.get('fact_count', 0)} fact and {summary.get('dimension_count', 0)} dimension tables "
        f"({dialect} dialect preview).",
    ]
    facts = logical_model.get("facts") or []
    dims = logical_model.get("dimensions") or []
    if facts:
        parts.append(f"Facts: {', '.join(facts)}.")
    if dims:
        parts.append(f"Dimensions: {', '.join(dims)}.")
    if gaps:
        parts.append(f"{len(gaps)} gap(s) noted for review.")
    return " ".join(parts)


async def _emit_warehouse_progress(
    on_progress: OnWarehouseProgress,
    percent: int,
    sub_step: str,
    message: str,
    *,
    heartbeat: bool = False,
) -> None:
    if on_progress:
        await on_progress(percent, sub_step, message, heartbeat)


async def _run_with_warehouse_llm_heartbeat(
    on_progress: OnWarehouseProgress,
    coro_factory,
) -> Any:
    """Keep SSE alive during long LLM call without advancing fake percent steps."""
    import asyncio

    if not on_progress:
        return await coro_factory()

    stop = asyncio.Event()
    msg = "AI is proposing star-schema warehouse tables (may take 1–2 min)"

    async def _heartbeat() -> None:
        while not stop.is_set():
            await on_progress(93, "warehouse_ai", msg, True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=_WAREHOUSE_LLM_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(_heartbeat())
    try:
        return await coro_factory()
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def build_warehouse_proposal(
    *,
    graph_id: str,
    run_id: Optional[str],
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    canonical_entities: List[Dict[str, Any]],
    canonical_graph: Dict[str, Any],
    graph_name: str = "Enterprise graph",
    semantic_mappings: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[str] = None,
    on_progress: OnWarehouseProgress = None,
    lake_proposal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Full proposal payload ready for persistence."""
    await _emit_warehouse_progress(
        on_progress,
        92,
        "warehouse_context",
        "Loading canonical model and semantic mappings",
    )
    dialect = resolve_warehouse_dialect(graph_settings, members)
    design_method = "rules"
    llm_rationale: Optional[str] = None
    logical_model: Dict[str, Any]

    from services.enterprise_graph.config import should_use_lake_silver_for_warehouse

    use_lake_silver = should_use_lake_silver_for_warehouse(graph_settings, lake_proposal)
    if use_lake_silver:
        await _emit_warehouse_progress(
            on_progress,
            93,
            "warehouse_rules",
            "Building warehouse model from lake silver catalog",
        )
        logical_model = build_logical_model_with_lake_silver(
            canonical_entities=canonical_entities,
            canonical_graph=canonical_graph,
            lake_logical_model=(lake_proposal or {}).get("logical_model") or {},
        )
        design_method = "lake_silver"

    from services.semantic_mapping.config import warehouse_llm_fallback_enabled

    if not use_lake_silver:
        try:
            from services.semantic_mapping.warehouse_designer_llm import propose_logical_model_llm

            async def _llm() -> Any:
                return await propose_logical_model_llm(
                    graph_name=graph_name,
                    graph_settings=graph_settings,
                    members=members,
                    canonical_entities=canonical_entities,
                    canonical_graph=canonical_graph,
                    semantic_mappings=semantic_mappings,
                    dialect=dialect,
                )

            await _emit_warehouse_progress(
                on_progress,
                93,
                "warehouse_ai",
                "AI is proposing star-schema warehouse tables (may take 1–2 min)",
            )
            logical_model, llm_rationale = await _run_with_warehouse_llm_heartbeat(
                on_progress, _llm
            )
            design_method = "llm"
        except Exception as exc:
            if not warehouse_llm_fallback_enabled():
                raise
            import logging

            logging.getLogger(__name__).warning(
                "Warehouse LLM failed, using rules fallback: %s", exc
            )
            await _emit_warehouse_progress(
                on_progress,
                93,
                "warehouse_rules",
                "Building warehouse model using rule-based fallback",
            )
            logical_model = build_logical_model(
                canonical_entities=canonical_entities,
                canonical_graph=canonical_graph,
            )
            design_method = "rules_fallback"

    logical_model["design_method"] = design_method
    await _emit_warehouse_progress(
        on_progress,
        93,
        "warehouse_map",
        "Mapping sources to warehouse tables",
    )
    source_map = build_source_to_warehouse_map(logical_model)
    gaps = identify_gaps(canonical_entities=canonical_entities, logical_model=logical_model)

    connector_config = None
    if user_id:
        connector_config = await load_warehouse_sql_connector_config(user_id, members)
    target_dataset = resolve_warehouse_target_dataset(
        graph_settings, members, dialect, connector_config=connector_config
    )
    await _emit_warehouse_progress(
        on_progress,
        94,
        "warehouse_ddl",
        f"Generating {dialect} DDL statements",
    )
    ddl = (
        generate_ddl(logical_model, dialect, dataset=target_dataset)
        if dialect != "logical"
        else generate_ddl(logical_model, "logical", dataset=target_dataset)
    )
    if llm_rationale:
        rationale = llm_rationale
        if gaps:
            rationale = f"{rationale} ({len(gaps)} gap(s) noted for review.)"
    else:
        rationale = build_rationale(logical_model, dialect, gaps)

    return {
        "id": str(uuid.uuid4()),
        "graph_id": graph_id,
        "run_id": run_id,
        "dialect": dialect,
        "logical_model": logical_model,
        "source_to_warehouse_map": source_map,
        "ddl_text": ddl,
        "target_dataset": target_dataset,
        "rationale": rationale,
        "gaps": gaps,
        "status": "draft",
        "design_method": design_method,
    }
