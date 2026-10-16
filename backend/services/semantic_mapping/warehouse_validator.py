"""Validate and normalize LLM warehouse logical model output."""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Set, Tuple

from services.semantic_mapping.config import warehouse_max_columns_per_table, warehouse_max_tables

_FORBIDDEN_COL_FRAGMENTS = (
    "inconsistency",
    "fuzzy_duplicate",
    "total_missing",
    "by_field",
    "percentages",
)


def _allowed_source_columns(bundle: Dict[str, Any]) -> Set[str]:
    allowed: Set[str] = set()
    for ent in bundle.get("canonical_entities") or []:
        for col in ent.get("columns") or []:
            name = (col.get("name") if isinstance(col, dict) else col) or ""
            norm = re.sub(r"[^a-z0-9_]+", "_", str(name).strip()).strip("_").lower()
            if norm:
                allowed.add(norm)
    return allowed


def _valid_table_name(name: str) -> bool:
    return bool(re.match(r"^(fact|dim|bridge)_[a-z0-9_]+$", (name or "").lower()))


def _clean_column_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", (name or "").strip()).strip("_").lower()


def _column_allowed(cname: str, allowed: Set[str], *, is_surrogate: bool = False) -> bool:
    if not cname:
        return False
    if is_surrogate:
        return True
    if any(frag in cname for frag in _FORBIDDEN_COL_FRAGMENTS):
        return False
    if cname in ("by_field", "percentages", "total_missing", "summary"):
        return False
    if not allowed:
        return True
    return cname in allowed


def validate_and_normalize(
    raw: Dict[str, Any],
    bundle: Dict[str, Any],
    *,
    dialect: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Returns (logical_model compatible with warehouse_designer.generate_ddl, warnings).
    """
    warnings: List[str] = []
    allowed_cols = _allowed_source_columns(bundle)
    canonical_ids = {str(e.get("id")) for e in bundle.get("canonical_entities") or [] if e.get("id")}

    tables_in: List[Dict[str, Any]] = list(raw.get("tables") or [])[: warehouse_max_tables()]
    tables_out: List[Dict[str, Any]] = []
    used_names: Set[str] = set()

    for t in tables_in:
        tname = (t.get("name") or "").strip().lower()
        if not _valid_table_name(tname):
            warnings.append(f"Skipped invalid table name: {tname}")
            continue
        if tname in used_names:
            warnings.append(f"Skipped duplicate table: {tname}")
            continue
        used_names.add(tname)

        sk_name = f"{tname}_key"
        columns: List[Dict[str, Any]] = [
            {
                "name": sk_name,
                "type": "STRING",
                "role": "surrogate_key",
                "nullable": False,
            }
        ]
        lineage: List[Dict[str, Any]] = []
        col_limit = warehouse_max_columns_per_table()

        for col in (t.get("columns") or [])[:col_limit]:
            if not isinstance(col, dict):
                continue
            cname = _clean_column_name(col.get("name") or "")
            if not cname or cname == sk_name:
                continue
            if not _column_allowed(cname, allowed_cols, is_surrogate=False):
                continue
            if any(c["name"] == cname for c in columns):
                continue
            columns.append(
                {
                    "name": cname,
                    "type": str(col.get("type") or "STRING").upper(),
                    "role": col.get("role") or "attribute",
                    "nullable": bool(col.get("nullable", True)),
                }
            )
            for ref in col.get("source_refs") or []:
                if not isinstance(ref, dict):
                    continue
                lineage.append(
                    {
                        "warehouse_column": cname,
                        "source_member_id": ref.get("member_id"),
                        "source_entity_id": ref.get("entity_id"),
                        "source_column": ref.get("source_column") or cname,
                    }
                )

        cid = None
        cids = t.get("canonical_entity_ids") or []
        if cids:
            cid = str(cids[0]) if cids[0] in canonical_ids else None
        if not cid and len(cids) == 1:
            cid = str(cids[0])

        fks = []
        for fk in t.get("foreign_keys") or []:
            if not isinstance(fk, dict):
                continue
            ref_t = (fk.get("references_table") or "").strip().lower()
            if ref_t and _valid_table_name(ref_t):
                fks.append(
                    {
                        "column": fk.get("column") or f"{ref_t}_key",
                        "references_table": ref_t,
                        "references_column": fk.get("references_column") or f"{ref_t}_key",
                        "relationship_label": fk.get("relationship_label"),
                    }
                )

        tables_out.append(
            {
                "id": str(uuid.uuid4()),
                "name": tname,
                "table_type": t.get("table_type") if t.get("table_type") in ("fact", "dimension") else "dimension",
                "canonical_entity_id": cid,
                "canonical_name": tname.replace("fact_", "").replace("dim_", ""),
                "domain_tags": [],
                "grain": t.get("grain") or f"One row per {tname}",
                "columns": columns,
                "foreign_keys": fks,
                "lineage": lineage,
            }
        )

    facts = [t["name"] for t in tables_out if t["table_type"] == "fact"]
    dims = [t["name"] for t in tables_out if t["table_type"] == "dimension"]
    relationships = []
    for rel in raw.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        ft = (rel.get("from_table") or "").lower()
        tt = (rel.get("to_table") or "").lower()
        if ft in used_names and tt in used_names:
            relationships.append(
                {
                    "from_table": ft,
                    "to_table": tt,
                    "cardinality": rel.get("cardinality") or "many_to_one",
                    "via_column": rel.get("via_column") or f"{tt}_key",
                }
            )

    logical_model = {
        "model_type": raw.get("model_type") or "star",
        "tables": tables_out,
        "facts": facts,
        "dimensions": dims,
        "relationships": relationships,
        "summary": {
            "table_count": len(tables_out),
            "fact_count": len(facts),
            "dimension_count": len(dims),
            "relationship_count": len(relationships),
        },
        "design_method": "llm",
        "llm_warnings": warnings,
        "excluded_entities": raw.get("excluded_entities") or [],
    }
    return logical_model, warnings
