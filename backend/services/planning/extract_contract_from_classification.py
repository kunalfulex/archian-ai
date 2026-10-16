"""Build connector extract contracts from plan_contract binding + migration_intent."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.planning.binding.guards import (
    plan_contract_from_classification,
    source_resolved_by_binding,
)
from services.planning.gsheets_extract_contract import build_gsheets_extract_contract
from services.planning.soql_extract_contract import build_soql_extract_contract
from services.planning.sql_extract_contract import build_sql_extract_contract


def _migration_intent(classification: Dict[str, Any]) -> Dict[str, Any]:
    plan = plan_contract_from_classification(classification)
    if plan and plan.migration_intent and isinstance(plan.migration_intent, dict):
        return dict(plan.migration_intent)
    raw = classification.get("migration_intent")
    return dict(raw) if isinstance(raw, dict) else {}


def _plan_source(classification: Dict[str, Any]) -> Dict[str, Any]:
    plan = plan_contract_from_classification(classification)
    if plan and plan.source.is_resolved():
        return plan.source.model_dump(mode="json")
    contract = classification.get("plan_contract") or {}
    src = contract.get("source") if isinstance(contract, dict) else None
    return dict(src) if isinstance(src, dict) else {}


def _field_scope_from_mi(mi: Dict[str, Any]) -> Dict[str, Any]:
    raw = mi.get("extract_field_scope")
    if isinstance(raw, dict):
        scope = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        scope = {"extract_field_scope": raw.strip()}
    else:
        scope = {}
    if mi.get("field_hints_strict"):
        scope["field_hints_strict"] = True
    if mi.get("field_hints"):
        scope.setdefault("field_hints", list(mi["field_hints"]))
    if mi.get("include_related_data") is not None and "include_related_data" not in scope:
        scope["include_related_data"] = mi.get("include_related_data")
    parent_keys = list(mi.get("related_entity_keys") or mi.get("parent_entity_keys") or [])
    if mi.get("parent_entity_key"):
        parent_keys.append(str(mi["parent_entity_key"]))
    if parent_keys:
        scope.setdefault("parent_entity_keys", parent_keys)
    return scope


def _source_entity_key(classification: Dict[str, Any], src: Dict[str, Any], conn: str) -> str:
    key = (src.get("entity_key") or "").strip()
    if key:
        return key
    if conn == "sql":
        return (
            (classification.get("source_table_name") or "")
            or (classification.get("source_object_api_name") or "")
            or (classification.get("source_entity_key") or "")
        ).strip()
    if conn == "salesforce":
        return (
            (classification.get("source_object_api_name") or classification.get("source_entity_key") or "")
        ).strip()
    if conn == "google_sheets":
        return (classification.get("source_sheet_name") or classification.get("source_entity_key") or "").strip()
    return (classification.get("source_entity_key") or "").strip()


def _fields_confirmed(classification: Dict[str, Any], mi: Dict[str, Any]) -> bool:
    return bool(
        mi.get("extract_fields_confirmed") or classification.get("extract_fields_confirmed")
    )


def _binding_authoritative(classification: Dict[str, Any], mi: Dict[str, Any], hints: List[str]) -> bool:
    plan = plan_contract_from_classification(classification)
    if not plan or not source_resolved_by_binding(plan):
        return False
    if not _fields_confirmed(classification, mi):
        return False
    scope = (mi.get("extract_field_scope") or "").strip().lower()
    if scope == "all_descriptive":
        return True
    return bool(hints)


def _collect_field_hints(classification: Dict[str, Any], mi: Dict[str, Any]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for container in (mi, _plan_source(classification), classification):
        if not isinstance(container, dict):
            continue
        for key in ("field_hints", "selected_fields", "columns"):
            raw = container.get(key)
            if not isinstance(raw, list):
                continue
            for item in raw:
                s = str(item or "").strip()
                key_norm = s.lower()
                if s and key_norm not in seen:
                    seen.add(key_norm)
                    out.append(s)
    return out


def _relationship_prefix(field_path: str) -> Optional[str]:
    if "." not in field_path:
        return None
    return field_path.split(".", 1)[0]


def _sf_object_result_from_classification(classification: Dict[str, Any]) -> Dict[str, Any]:
    from services.planning.related_select_fields import (
        merge_primary_and_related_fields,
        resolve_sf_related_select_paths,
    )

    mi = _migration_intent(classification)
    src = _plan_source(classification)
    object_api = _source_entity_key(classification, src, "salesforce")
    primary_selected = _collect_field_hints(classification, mi)

    related_paths, related_metadata, related_lookup_rels = resolve_sf_related_select_paths(mi)
    selected = merge_primary_and_related_fields(
        primary_selected,
        related_paths,
        ensure_id=True,
        primary_object=object_api,
    )

    field_metadata: List[Dict[str, Any]] = []
    lookup_relationships: Dict[str, List[Dict[str, str]]] = dict(related_lookup_rels)
    for field in selected:
        prefix = _relationship_prefix(field)
        existing = next(
            (m for m in related_metadata if (m.get("api_name") or "") == field),
            None,
        )
        if existing:
            field_metadata.append(dict(existing))
            continue
        entry: Dict[str, Any] = {"api_name": field, "label": field}
        if prefix:
            entry["is_relationship_field"] = True
            entry["relationship_name"] = prefix
            entry["lookup_object"] = prefix
            lookup_relationships.setdefault(prefix, [])
        field_metadata.append(entry)

    scope = _field_scope_from_mi(mi)

    return {
        "object_api_name": object_api or None,
        "object_label": (src.get("entity_label") or src.get("catalog_label") or object_api or "").strip()
        or None,
        "selected_fields": selected,
        "field_metadata": field_metadata,
        "lookup_relationships": lookup_relationships,
        "extract_field_scope": scope,
        "filters": classification.get("extract_filters"),
    }


def _sql_table_result_from_classification(classification: Dict[str, Any]) -> Dict[str, Any]:
    from services.planning.related_select_fields import (
        resolve_sql_related_columns_from_intent,
        resolve_sql_related_tables_from_intent,
    )

    mi = _migration_intent(classification)
    src = _plan_source(classification)
    primary = _source_entity_key(classification, src, "sql")
    table_names = resolve_sql_related_tables_from_intent(mi, classification, primary_table=primary)
    if not table_names and primary:
        table_names = [primary]

    if mi.get("include_related_data") and mi.get("related_field_hints"):
        selected, column_metadata = resolve_sql_related_columns_from_intent(table_names, mi)
        if not selected:
            selected = _collect_field_hints(classification, mi)
            column_metadata = [
                {"table_name": primary or "", "column_name": col.split(".")[-1], "name": col.split(".")[-1]}
                for col in selected
                if col and primary
            ]
    else:
        selected = _collect_field_hints(classification, mi)
        column_metadata = [
            {"table_name": primary or "", "column_name": col.split(".")[-1], "name": col.split(".")[-1]}
            for col in selected
            if col and primary
        ]

    scope = _field_scope_from_mi(mi)
    return {
        "primary_table": primary or None,
        "table_names": table_names,
        "selected_columns": [c.split(".")[-1] for c in selected],
        "column_metadata": column_metadata,
        "extract_field_scope": scope,
        "fk_relationships": [],
    }


def _tag_authoritative(contract: Dict[str, Any], classification: Dict[str, Any], connector: str) -> Dict[str, Any]:
    if not contract:
        return contract
    mi = _migration_intent(classification)
    hints = _collect_field_hints(classification, mi)
    if _binding_authoritative(classification, mi, hints):
        contract["binding_authoritative"] = True
        contract["fields_confirmed"] = True
    contract["connector"] = connector
    return contract


def build_extract_contract_from_classification(
    classification: Dict[str, Any],
    connector: str,
) -> Dict[str, Any]:
    """Route to connector-specific extract contract builder."""
    conn = (connector or "").strip().lower()
    mi = _migration_intent(classification)

    if conn == "google_sheets":
        contract = build_gsheets_extract_contract(classification, migration_intent=mi)
        src = _plan_source(classification)
        if src.get("entity_key") and not contract.get("entity_key"):
            contract["entity_key"] = src["entity_key"]
        if src.get("entity_label") and not contract.get("sheet_name"):
            contract.setdefault("sheet_name", src["entity_key"])
        return _tag_authoritative(contract, classification, conn)

    if conn == "salesforce":
        obj = _sf_object_result_from_classification(classification)
        if not obj.get("object_api_name") and not obj.get("selected_fields"):
            return {}
        contract = build_soql_extract_contract(
            obj,
            extract_field_scope=obj.get("extract_field_scope"),
            migration_intent=mi,
        )
        return _tag_authoritative(contract, classification, conn)

    if conn == "sql":
        table = _sql_table_result_from_classification(classification)
        if not table.get("primary_table") and not table.get("selected_columns"):
            return {}
        contract = build_sql_extract_contract(
            table,
            extract_field_scope=table.get("extract_field_scope"),
            migration_intent=mi,
        )
        return _tag_authoritative(contract, classification, conn)

    return {}
