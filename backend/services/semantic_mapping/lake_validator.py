"""Validate and normalize LLM lake logical model output."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.lake_designer import _map_key, bronze_table_name
from services.enterprise_graph.models import GraphMember


def _valid_identifier(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", (name or "").lower()).strip("_")
    return slug or "entity"


def validate_lake_logical_model(
    raw: Dict[str, Any],
    inventory: List[SourceEntity],
    *,
    members: Optional[List[GraphMember]] = None,
    bronze_schema: str,
    silver_schema: str,
    dialect: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """Returns (logical_model compatible with lake_designer, warnings)."""
    warnings: List[str] = []
    inv_keys = {_map_key(e.member_id, e.entity_id) for e in inventory}
    inv_by_key = {_map_key(e.member_id, e.entity_id): e for e in inventory}
    member_by_id = {m.id: m for m in (members or [])}

    bronze_tables: List[Dict[str, Any]] = []
    used_bronze: Set[str] = set()
    for idx, table in enumerate(raw.get("bronze_tables") or []):
        if not isinstance(table, dict):
            continue
        mid = str(table.get("source_member_id") or "").strip()
        eid = str(table.get("source_entity_id") or "").strip()
        key = _map_key(mid, eid)
        if key not in inv_keys:
            warnings.append(f"bronze[{idx}]: unknown source {key}")
            continue
        ent = inv_by_key.get(key)
        member = member_by_id.get(mid)
        if ent:
            name = _valid_identifier(bronze_table_name(ent, member))
        else:
            name = _valid_identifier(table.get("name") or f"raw_{mid[:8]}_{eid}")
        if name in used_bronze:
            name = f"{name}_{idx}"
        used_bronze.add(name)
        columns: List[Dict[str, Any]] = []
        seen_cols: Set[str] = set()
        for col in table.get("columns") or []:
            if isinstance(col, dict) and col.get("name"):
                cname = _valid_identifier(col["name"])
                if cname in seen_cols:
                    continue
                seen_cols.add(cname)
                columns.append(
                    {
                        "name": cname,
                        "type": col.get("type") or "VARCHAR",
                        "source_column": col.get("source_column") or col["name"],
                        "nullable": True,
                    }
                )
        if ent and not columns:
            for c in (ent.column_names or [])[:40]:
                cname = _valid_identifier(c)
                if cname not in seen_cols:
                    seen_cols.add(cname)
                    columns.append(
                        {
                            "name": cname,
                            "type": "VARCHAR",
                            "source_column": c,
                            "nullable": True,
                        }
                    )
        for lc_name, lc_type in (
            ("_archian_source_member_id", "VARCHAR"),
            ("_archian_source_entity_id", "VARCHAR"),
            ("_archian_loaded_at", "TIMESTAMP_NTZ"),
        ):
            columns.append({"name": lc_name, "type": lc_type, "lineage": True, "nullable": True})
        bronze_tables.append(
            {
                "name": name,
                "bronze_table": name,
                "source_member_id": mid,
                "source_entity_id": eid,
                "source_connector_type": ent.connector_type if ent else "",
                "label": table.get("label") or (ent.label if ent else eid),
                "columns": columns,
            }
        )

    bronze_by_name = {t["name"]: t for t in bronze_tables}
    silver_views: List[Dict[str, Any]] = []
    used_views: Set[str] = set()
    for idx, view in enumerate(raw.get("silver_views") or []):
        if not isinstance(view, dict):
            continue
        vname = _valid_identifier(view.get("name") or f"stg_view_{idx}")
        if vname in used_views:
            vname = f"{vname}_{idx}"
        used_views.add(vname)
        source_bronze = [
            str(n) for n in (view.get("source_bronze_tables") or []) if str(n) in bronze_by_name
        ]
        branches = list(view.get("branch_select_lists") or [])
        if dialect == "snowflake" and branches:
            body = "\nUNION ALL\n".join(branches)
            view_sql = f"CREATE OR REPLACE VIEW {silver_schema}.{vname} AS\n{body};"
        elif dialect == "snowflake" and source_bronze:
            parts = [
                f"SELECT * FROM {bronze_schema}.{bn}" for bn in source_bronze[:6]
            ]
            view_sql = (
                f"CREATE OR REPLACE VIEW {silver_schema}.{vname} AS\n"
                + "\nUNION ALL\n".join(parts)
                + ";"
            )
        else:
            view_sql = view.get("view_sql") or f"-- VIEW {vname}"
        silver_views.append(
            {
                "name": vname,
                "canonical_entity_id": view.get("canonical_entity_id"),
                "canonical_name": view.get("canonical_name"),
                "view_sql": view_sql,
                "source_bronze_tables": source_bronze,
                "branch_count": len(source_bronze) or len(branches),
            }
        )

    logical_model = {
        "bronze_tables": bronze_tables,
        "silver_views": silver_views,
        "zones": {
            "bronze_schema": bronze_schema.strip('"'),
            "silver_schema": silver_schema.strip('"'),
        },
        "summary": {
            "bronze_table_count": len(bronze_tables),
            "silver_view_count": len(silver_views),
            "design_method": "llm",
        },
    }
    if not bronze_tables:
        raise ValueError("LLM lake model has no valid bronze tables")
    return logical_model, warnings
