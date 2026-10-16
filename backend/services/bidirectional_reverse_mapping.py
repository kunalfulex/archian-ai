"""
Reverse-leg field mapping: invert parent mappings and pair-specific finalization.

Loaded after `bidirectional_flow_service` is initialized; uses lazy imports to reach
SQL↔SQL helpers and DTOs on the main module without import cycles at package load time.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from db.connection import database
from utils.soql_parse import (
    flatten_soql_select_item_to_extracted_key,
    raw_select_parts,
    split_outer_select_clause,
)
from utils.soql_salesforce_field_validation import relationship_token_for_lookup_field

logger = logging.getLogger(__name__)

def _strip_soql_select_alias(expr: str) -> str:
    e = str(expr or "").strip()
    e = re.sub(r"\s+AS\s+[\w\.]+\s*$", "", e, flags=re.IGNORECASE)
    return e.strip()


def _soql_flattened_key_to_best_expression(soql: str) -> Dict[str, str]:
    """
    Map lowercased flattened extract keys to a SELECT expression from the parent query,
    preferring dotted relationship paths over bare fields.
    """
    sel, _ = split_outer_select_clause(soql or "")
    if not sel:
        return {}
    out: Dict[str, str] = {}
    for raw in raw_select_parts(sel):
        expr = _strip_soql_select_alias(raw)
        if not expr or re.match(r"^\s*select\s+", expr, re.IGNORECASE):
            continue
        flat = flatten_soql_select_item_to_extracted_key(expr).strip().lower()
        if not flat:
            continue
        prev = out.get(flat)
        if not prev:
            out[flat] = expr
            continue
        if "." in expr and "." not in prev:
            out[flat] = expr
        elif "." in expr and "." in prev and len(expr) > len(prev):
            out[flat] = expr
    return out


async def _canonical_lookup_of_on_primary(
    user_id: str,
    org_id: str,
    primary_obj: str,
    lookup_of: str,
) -> Optional[str]:
    parts = [p.strip() for p in (lookup_of or "").split(".") if p.strip()]
    if len(parts) != 2:
        return None
    base_guess, fld_guess = parts[0], parts[1]
    if base_guess.lower() != (primary_obj or "").strip().lower():
        return None
    row = await database.fetch_one(
        """
        SELECT object_api_name, field_api_name
        FROM salesforce_object_fields
        WHERE user_id = :uid AND org_id = :org
          AND LOWER(object_api_name) = LOWER(:obj)
          AND LOWER(field_api_name) = LOWER(:fld)
          AND is_lookup IS TRUE
        LIMIT 1
        """,
        {"uid": user_id, "org": org_id, "obj": primary_obj, "fld": fld_guess},
    )
    if not row:
        return None
    d = dict(row)
    o = (d.get("object_api_name") or "").strip()
    f = (d.get("field_api_name") or "").strip()
    if not o or not f:
        return None
    return f"{o}.{f}"


async def _resolve_primary_lookup_field_for_relationship(
    user_id: str,
    org_id: str,
    primary_obj: str,
    relationship_first_segment: str,
    target_object_api: str,
) -> Optional[str]:
    pri = (primary_obj or "").strip()
    tgt = (target_object_api or "").strip()
    seg = (relationship_first_segment or "").strip()
    if not pri or not tgt or not seg:
        return None
    rows = await database.fetch_all(
        """
        SELECT field_api_name, relationship_name
        FROM salesforce_object_fields
        WHERE user_id = :uid AND org_id = :org
          AND object_api_name = :pri
          AND is_lookup IS TRUE
          AND lookup_object_api_name IS NOT NULL
          AND LOWER(lookup_object_api_name) = LOWER(:tgt)
        """,
        {"uid": user_id, "org": org_id, "pri": pri, "tgt": tgt},
    )
    if not rows:
        return None
    want = seg.lower()
    matches: List[str] = []
    for row in rows:
        d = dict(row)
        fn = str(d.get("field_api_name") or "").strip()
        reln = d.get("relationship_name")
        tok = relationship_token_for_lookup_field(fn, reln)
        if tok and tok.lower() == want:
            matches.append(fn)
    if len(matches) == 1:
        return matches[0]
    return matches[0] if matches else None


async def _lookup_target_object_for_primary_lookup_field(
    user_id: str,
    org_id: str,
    primary_obj: str,
    lookup_field_api: str,
) -> Optional[str]:
    row = await database.fetch_one(
        """
        SELECT lookup_object_api_name
        FROM salesforce_object_fields
        WHERE user_id = :uid AND org_id = :org
          AND LOWER(object_api_name) = LOWER(:obj)
          AND LOWER(field_api_name) = LOWER(:fld)
          AND is_lookup IS TRUE
          AND lookup_object_api_name IS NOT NULL
        LIMIT 1
        """,
        {"uid": user_id, "org": org_id, "obj": primary_obj, "fld": lookup_field_api},
    )
    if not row:
        return None
    return str(dict(row).get("lookup_object_api_name") or "").strip() or None


async def _finalize_sf_to_sql_reverse_salesforce_destination_mappings(
    user_id: str,
    org_id: str,
    primary_obj: str,
    parent_soql: str,
    parent_rows: List[Dict[str, Any]],
    inverted: List[Dict[str, Any]],
) -> None:
    """
    Primary SF→SQL reverse: child loads Salesforce. Normalize lookup_of and related
    salesforce_object rows using metadata and parent SOQL.
    """
    pri = (primary_obj or "Contact").strip() or "Contact"
    flat_to_expr = _soql_flattened_key_to_best_expression(parent_soql)
    parent_n = len(parent_rows or [])
    n_rows = len(inverted or [])
    rel_canonical = rel_inferred = lookup_cleared = related_no_infer = 0
    for i, r in enumerate(inverted or []):
        p = parent_rows[i] if i < parent_n else {}
        lo_orig = (r.get("lookup_of") or "").strip()

        canonical_lo: Optional[str] = None
        via = ""
        if lo_orig:
            canonical_lo = await _canonical_lookup_of_on_primary(user_id, org_id, pri, lo_orig)
            if canonical_lo:
                via = "canonical_from_existing_lookup_of"
            else:
                pass

        if not canonical_lo:
            ex_parent = (p.get("extracted_field_name") or "").strip()
            expr = flat_to_expr.get(ex_parent.lower()) if ex_parent else ""
            tgt_obj = (p.get("salesforce_object") or "").strip()
            infer_skip: Optional[str] = None
            if not ex_parent:
                infer_skip = "no_parent_extracted_field_name"
            elif not expr:
                infer_skip = "soql_flat_map_miss_for_extracted_key"
            elif "." not in expr:
                infer_skip = "soql_expr_has_no_dot_not_a_traversal_path"
            elif not tgt_obj:
                infer_skip = "parent_salesforce_object_empty"
            elif tgt_obj.lower() == pri.lower():
                infer_skip = "parent_salesforce_object_is_primary_plain_field"
            else:
                path_parts = [x.strip() for x in expr.split(".") if x.strip()]
                if len(path_parts) < 2:
                    infer_skip = "soql_path_parts_lt_2"
                else:
                    rel_tok = path_parts[0]
                    lk_field = await _resolve_primary_lookup_field_for_relationship(
                        user_id, org_id, pri, rel_tok, tgt_obj
                    )
                    if not lk_field:
                        infer_skip = (
                            "metadata_no_lookup_on_primary_for_target="
                            f"{tgt_obj!r}_rel_token={rel_tok!r}"
                        )
                    else:
                        canonical_lo = await _canonical_lookup_of_on_primary(
                            user_id, org_id, pri, f"{pri}.{lk_field}"
                        )
                        if not canonical_lo:
                            canonical_lo = f"{pri}.{lk_field}"
                        via = "inferred_from_parent_soql"
            _ = infer_skip

        if canonical_lo:
            r["lookup_of"] = canonical_lo
            lk_api = canonical_lo.split(".", 1)[1].strip()
            tgt = await _lookup_target_object_for_primary_lookup_field(
                user_id, org_id, pri, lk_api
            )
            if tgt:
                r["salesforce_object"] = tgt
            else:
                po = (p.get("salesforce_object") or "").strip()
                r["salesforce_object"] = po or pri
            if via == "canonical_from_existing_lookup_of":
                rel_canonical += 1
            elif via == "inferred_from_parent_soql":
                rel_inferred += 1
        else:
            if lo_orig:
                r["lookup_of"] = ""
            r["salesforce_object"] = pri
            if lo_orig:
                lookup_cleared += 1
            elif (p.get("salesforce_object") or "").strip().lower() != pri.lower():
                related_no_infer += 1
    _ = (n_rows, rel_canonical, rel_inferred, lookup_cleared, related_no_infer)


def _parse_migration_rules_dict(migration_rules: Any) -> Optional[Dict[str, Any]]:
    if migration_rules is None:
        return None
    if isinstance(migration_rules, dict):
        return migration_rules
    if isinstance(migration_rules, str):
        try:
            import json

            parsed = json.loads(migration_rules)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _transformation_fields_all_stages(migration_rules: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (extraction_stage_fields, mapping_stage_fields)."""
    d = _parse_migration_rules_dict(migration_rules)
    if not d:
        return [], []
    extraction_fields: List[Dict[str, Any]] = []
    mapping_fields: List[Dict[str, Any]] = []
    er = d.get("extraction_rules")
    if isinstance(er, dict):
        tr = er.get("transformation_rules")
        if isinstance(tr, dict) and isinstance(tr.get("fields"), list):
            extraction_fields = [x for x in tr["fields"] if isinstance(x, dict)]
    tr2 = d.get("transformation_rules")
    if isinstance(tr2, dict) and isinstance(tr2.get("fields"), list):
        mapping_fields = [x for x in tr2["fields"] if isinstance(x, dict)]
    return extraction_fields, mapping_fields


def _merge_output_to_source_fields(migration_rules: Any) -> Dict[str, List[str]]:
    """lower(output_field) -> ordered source field API names from merge/combine rules."""
    out: Dict[str, List[str]] = {}
    extraction_fields, mapping_fields = _transformation_fields_all_stages(migration_rules)
    for rule in extraction_fields + mapping_fields:
        action = (rule.get("action") or "").strip().lower()
        if action not in ("merge", "combine"):
            continue
        srcs = rule.get("source_fields")
        if srcs is None:
            srcs = rule.get("source_field") or []
        if isinstance(srcs, str):
            srcs = [srcs]
        if not isinstance(srcs, list):
            continue
        cleaned = [str(s).strip() for s in srcs if str(s).strip()]
        of = rule.get("output_field")
        if cleaned and of is not None and str(of).strip():
            out[str(of).strip().lower()] = cleaned
    return out


def _split_source_to_output_columns(migration_rules: Any) -> Dict[str, List[str]]:
    """lower(source_field) -> ordered output column names from split* mapping rules."""
    out: Dict[str, List[str]] = {}
    _, mapping_fields = _transformation_fields_all_stages(migration_rules)
    for rule in mapping_fields:
        action = (rule.get("action") or "").strip().lower()
        if not (action.startswith("split") or action == "split_address"):
            continue
        src = rule.get("source_field")
        outs = rule.get("output_fields") or []
        if not src or not isinstance(outs, list):
            continue
        cleaned = [str(o).strip() for o in outs if str(o).strip()]
        if cleaned:
            out[str(src).strip().lower()] = cleaned
    return out


def _apply_sf_to_sql_reverse_child_sf_field_targets(
    parent_rows: List[Dict[str, Any]],
    inverted: List[Dict[str, Any]],
    migration_rules: Any,
    parent_soql: str,
) -> None:
    """
    Primary SF→SQL with reverse child SQL→SF: after invert + SQL key sync, set each row's
    salesforce_field to the Salesforce API field that should receive that SQL column.
    """
    from services.bidirectional_field_mapping_groups import build_field_mapping_display_groups

    merge_map = _merge_output_to_source_fields(migration_rules)
    split_map = _split_source_to_output_columns(migration_rules)
    groups = build_field_mapping_display_groups(parent_rows)
    flat_to_expr = _soql_flattened_key_to_best_expression(parent_soql)

    n = min(len(parent_rows or []), len(inverted or []))
    for i in range(n):
        inv = inverted[i]
        if (inv.get("lookup_of") or "").strip():
            continue
        p = parent_rows[i]
        ex = (p.get("extracted_field_name") or "").strip()
        sql_col = (p.get("salesforce_field") or "").strip()
        sf_field: Optional[str] = None

        merge_srcs = merge_map.get(ex.lower()) if ex else None
        split_cols = split_map.get(ex.lower()) if ex else None

        if merge_srcs and split_cols:
            idx: Optional[int] = None
            for j, sc in enumerate(split_cols):
                if sc.lower() == sql_col.lower():
                    idx = j
                    break
            if idx is None:
                for g in groups:
                    if g.get("kind") == "split" and i in (g.get("row_indices") or []):
                        indices = g["row_indices"]
                        if i in indices:
                            pos = indices.index(i)
                            if pos < len(merge_srcs):
                                idx = pos
                        break
            if idx is not None and idx < len(merge_srcs):
                sf_field = merge_srcs[idx]
        elif merge_srcs and len(merge_srcs) == 1:
            sf_field = merge_srcs[0]
        elif ex:
            expr = flat_to_expr.get(ex.lower())
            if expr and "." not in expr.strip():
                sf_field = expr.strip()
            elif expr:
                sf_field = expr.split(".")[-1].strip()
            else:
                sf_field = ex

        if sf_field:
            inv["salesforce_field"] = sf_field


def _log_sf_to_sql_reverse_mapping_result(
    parent_rows: List[Dict[str, Any]],
    inverted: List[Dict[str, Any]],
) -> None:
    pairs = []
    n = min(len(parent_rows or []), len(inverted or []))
    for i in range(n):
        inv = inverted[i]
        if (inv.get("lookup_of") or "").strip():
            continue
        pairs.append(
            f"{inv.get('extracted_field_name')!r} -> "
            f"{inv.get('salesforce_object')}.{inv.get('salesforce_field')}"
        )
    if pairs:
        logger.info("[REVERSE LEG] SF→SQL child mapping targets after SF field fix: %s", "; ".join(pairs))


def _invert_field_mappings(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Swap extracted_field_name <-> salesforce_field for the reverse leg."""
    out = []
    for r in rows or []:
        d = dict(r)
        ex = d.get("extracted_field_name")
        sf = d.get("salesforce_field")
        d["extracted_field_name"] = sf
        d["salesforce_field"] = ex
        out.append(d)
    return out


def _invert_field_mappings_for_reverse_sf_extract(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Reverse leg when extract is Salesforce: swap direct-field mapping ends for the opposite
    migration direction. Rows with lookup_of keep extracted_field_name and lookup semantics.
    """
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        d = dict(r)
        lo = (d.get("lookup_of") or "").strip()
        if lo:
            out.append(d)
            continue
        ex = d.get("extracted_field_name")
        sf = d.get("salesforce_field")
        d["extracted_field_name"] = sf
        d["salesforce_field"] = ex
        out.append(d)
    return out


def _invert_reverse_leg_field_mappings(
    source_plan: Any,
    parent_schema_type: str,
    fm_list: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    st = (parent_schema_type or "file").lower()
    if getattr(source_plan, "source_type", None) == "salesforce" and st == "salesforce":
        return _invert_field_mappings_for_reverse_sf_extract(fm_list)
    return _invert_field_mappings(fm_list)


def _sync_inverted_extracted_field_names_to_source_keys(
    parent_field_rows: List[Dict[str, Any]],
    inverted: List[Dict[str, Any]],
    source_keys_by_index: List[str],
) -> None:
    """
    After invert + object/table patches, set each row's extracted_field_name to the
    key the child extract will produce at that index (SOQL path or SQL SELECT label).
    """
    n = min(len(parent_field_rows), len(inverted), len(source_keys_by_index))
    for i in range(n):
        k = (source_keys_by_index[i] or "").strip()
        if not k:
            continue
        inverted[i]["extracted_field_name"] = k


async def _reverse_mapping_finalize_parent_salesforce_dest_sql(
    request: Any,
    source_plan: Any,
    inverted: List[Dict[str, Any]],
) -> None:
    from services import bidirectional_flow_service as bfs

    _ = source_plan
    sd = request.parent_schema
    fm_list = request.parent_mappings
    table_name = sd.get("table_name")
    sf_side_object = (str(table_name or "").strip()) or bfs._root_salesforce_object_for_mappings(
        fm_list, "Contact"
    )
    src_token = str(sd.get("connector_id") or "").strip()
    src_org_row = (
        await database.fetch_one(
            """
            SELECT org_id::text AS org_id
            FROM salesforce_tokens
            WHERE user_id = :uid AND id::text = :tid
            LIMIT 1
            """,
            {"uid": request.user_id, "tid": src_token},
        )
        if src_token
        else None
    )
    src_org = str(src_org_row["org_id"]) if src_org_row else ""
    parent_soql = str(sd.get("sql_query") or "").strip()
    pre_lookup = sum(1 for r in inverted if (r.get("lookup_of") or "").strip())
    _ = pre_lookup
    if src_org:
        await _finalize_sf_to_sql_reverse_salesforce_destination_mappings(
            request.user_id,
            src_org,
            sf_side_object,
            parent_soql,
            fm_list,
            inverted,
        )
    else:
        for r in inverted:
            if (r.get("lookup_of") or "").strip():
                continue
            r["salesforce_object"] = sf_side_object
    post_lookup = sum(1 for r in inverted if (r.get("lookup_of") or "").strip())
    _ = post_lookup


async def _reverse_mapping_finalize_parent_sql_dest_salesforce(
    request: Any,
    source_plan: Any,
    inverted: List[Dict[str, Any]],
) -> None:
    _ = source_plan
    sd = request.parent_schema
    table_name = sd.get("table_name")
    sql_tbl_patch = (str(table_name or "").strip()) or "unknown_table"
    for r in inverted:
        r["salesforce_object"] = sql_tbl_patch


async def _reverse_mapping_finalize_parent_sql_dest_sql(
    request: Any,
    source_plan: Any,
    inverted: List[Dict[str, Any]],
) -> None:
    from services import bidirectional_flow_service as bfs

    _ = source_plan
    await bfs._finalize_sql_sql_reverse_field_mappings(
        request.user_id,
        request.parent_flow_id,
        request.parent_schema,
        request.parent_flow,
        request.parent_mappings,
        inverted,
        request.parent_flow.get("migration_rules"),
    )


async def _reverse_mapping_finalize_parent_salesforce_dest_salesforce(
    _request: Any,
    _source_plan: Any,
    _inverted: List[Dict[str, Any]],
) -> None:
    """SF↔SF: invert rules already applied; no shared object/lookup patch here."""
    pass


REVERSE_LEG_MAPPING_FINALIZERS: Dict[
    Tuple[str, str],
    Callable[[Any, Any, List[Dict[str, Any]]], Awaitable[None]],
] = {
    ("salesforce", "sql"): _reverse_mapping_finalize_parent_salesforce_dest_sql,
    ("sql", "salesforce"): _reverse_mapping_finalize_parent_sql_dest_salesforce,
    ("sql", "sql"): _reverse_mapping_finalize_parent_sql_dest_sql,
    ("salesforce", "salesforce"): _reverse_mapping_finalize_parent_salesforce_dest_salesforce,
}


async def generate_reverse_mapping_plan(request: Any, source_plan: Any) -> Any:
    """Build inverted field_mappings for the reverse child flow (see `ReverseMappingPlan`)."""
    from services.bidirectional_flow_service import ReverseMappingPlan

    sd = request.parent_schema
    pd = request.parent_flow
    fm_list = request.parent_mappings
    schema_type = (sd.get("extracted_schema_type") or "file").lower()
    dest_type = (pd.get("destination_type") or "salesforce").lower()

    inverted = _invert_reverse_leg_field_mappings(source_plan, schema_type, fm_list)

    parent_pair_key = (schema_type, dest_type)
    sf_dest_reverse = parent_pair_key == ("salesforce", "sql")
    _ = sf_dest_reverse
    finalizer = REVERSE_LEG_MAPPING_FINALIZERS.get(parent_pair_key)
    if finalizer:
        await finalizer(request, source_plan, inverted)

    if source_plan.source_keys_by_index:
        _sync_inverted_extracted_field_names_to_source_keys(
            fm_list, inverted, source_plan.source_keys_by_index
        )

    if parent_pair_key == ("salesforce", "sql"):
        _apply_sf_to_sql_reverse_child_sf_field_targets(
            fm_list,
            inverted,
            pd.get("migration_rules"),
            str(sd.get("sql_query") or ""),
        )
        _log_sf_to_sql_reverse_mapping_result(fm_list, inverted)

    return ReverseMappingPlan(
        mappings=inverted,
        stats={
            "rows": len(inverted),
            "lookup_rows": sum(1 for r in inverted if (r.get("lookup_of") or "").strip()),
        },
    )
