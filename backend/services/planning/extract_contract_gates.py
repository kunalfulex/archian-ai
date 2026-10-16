"""Gates and helpers — authoritative extract contracts skip handler re-pick."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def extract_contract_is_authoritative(contract: Optional[Dict[str, Any]]) -> bool:
    """True when binding validated entity + fields; handlers must not re-pick."""
    if not contract or not isinstance(contract, dict):
        return False
    if not contract.get("binding_authoritative"):
        return False
    conn = (contract.get("connector") or "").strip().lower()
    if conn == "salesforce":
        return bool(contract.get("primary_object")) and bool(
            contract.get("allowed_select_fields") or contract.get("selected_fields")
        )
    if conn == "sql":
        return bool(contract.get("primary_table")) and bool(
            contract.get("allowed_select_columns") or contract.get("selected_columns")
        )
    if conn in ("google_sheets", "gsheets"):
        return bool(contract.get("sheet_name") or contract.get("entity_key"))
    return False


async def sf_object_result_from_extract_contract(
    contract: Dict[str, Any],
    *,
    user_id: str,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build determine_salesforce_object_and_fields-shaped result from plan contract."""
    primary = (contract.get("primary_object") or "").strip()
    selected = list(
        contract.get("allowed_select_fields") or contract.get("selected_fields") or []
    )
    scope = dict(contract.get("field_scope") or {})
    lookup_relationships = dict(contract.get("lookup_relationships") or {})

    field_metadata: List[Dict[str, Any]] = []
    if primary and user_id:
        try:
            from db.connection import database

            async def _fetch_object_fields(object_api_name: str) -> List[Dict[str, Any]]:
                params: Dict[str, Any] = {
                    "object_api_name": object_api_name,
                    "user_id": str(user_id),
                }
                org_filter = ""
                if org_id:
                    params["org_id"] = org_id
                    org_filter = "AND org_id = :org_id"
                rows = await database.fetch_all(
                    f"""
                    SELECT field_api_name, field_label, data_type, is_required,
                           is_lookup, lookup_object_api_name
                    FROM salesforce_object_fields
                    WHERE object_api_name = :object_api_name
                      AND user_id = :user_id
                      {org_filter}
                    ORDER BY field_api_name
                    LIMIT 300
                    """,
                    params,
                )
                return [dict(row) if hasattr(row, "keys") else row for row in (rows or [])]

            def _append_matching_fields(
                rows: List[Dict[str, Any]],
                *,
                relationship_prefix: Optional[str] = None,
                lookup_object: Optional[str] = None,
            ) -> None:
                selected_lower = {s.lower() for s in selected}
                for rd in rows:
                    api = (rd.get("field_api_name") or "").strip()
                    if not api:
                        continue
                    path = f"{relationship_prefix}.{api}" if relationship_prefix else api
                    if selected_lower and path.lower() not in selected_lower:
                        if not relationship_prefix:
                            rel_prefix = api.split(".", 1)[0] if "." in api else None
                            if not rel_prefix or rel_prefix.lower() not in selected_lower:
                                if not any(
                                    s.lower() == api.lower() or s.lower().startswith(f"{api.lower()}.")
                                    for s in selected
                                ):
                                    continue
                        else:
                            continue
                    entry: Dict[str, Any] = {
                        "api_name": path,
                        "label": rd.get("field_label") or path,
                        "data_type": rd.get("data_type") or "String",
                        "is_required": bool(rd.get("is_required")),
                        "is_lookup": bool(rd.get("is_lookup")),
                        "lookup_object": lookup_object or rd.get("lookup_object_api_name"),
                    }
                    if relationship_prefix:
                        entry["is_relationship_field"] = True
                        entry["relationship_name"] = relationship_prefix
                    field_metadata.append(entry)

            primary_rows = await _fetch_object_fields(primary)
            _append_matching_fields(primary_rows)

            relationship_paths = [s for s in selected if "." in (s or "")]
            parent_objects: Dict[str, str] = {}
            for path in relationship_paths:
                rel_prefix = path.split(".", 1)[0].strip()
                if not rel_prefix:
                    continue
                lookup_obj = rel_prefix
                for lookup_key, rels in lookup_relationships.items():
                    for rel in rels or []:
                        if (rel.get("relationship_name") or "").strip() == rel_prefix:
                            lookup_obj = (lookup_key or "").strip() or rel_prefix
                            break
                parent_objects.setdefault(lookup_obj, rel_prefix)

            for lookup_obj, rel_prefix in parent_objects.items():
                parent_rows = await _fetch_object_fields(lookup_obj)
                _append_matching_fields(
                    parent_rows,
                    relationship_prefix=rel_prefix,
                    lookup_object=lookup_obj,
                )
        except Exception:
            field_metadata = []

    if not field_metadata:
        for field in selected:
            entry: Dict[str, Any] = {"api_name": field, "label": field}
            if "." in field:
                prefix = field.split(".", 1)[0]
                entry["is_relationship_field"] = True
                entry["relationship_name"] = prefix
                entry["lookup_object"] = prefix
            field_metadata.append(entry)

    return {
        "needs_clarification": False,
        "object_api_name": primary or None,
        "object_label": contract.get("object_label") or primary,
        "selected_fields": selected,
        "field_metadata": field_metadata,
        "lookup_relationships": lookup_relationships,
        "extract_field_scope": scope,
        "filters": contract.get("filters_hint"),
        "org_id": org_id,
    }


async def resolve_sql_from_extract_contract(
    extract_contract: Dict[str, Any],
    connector_data: dict,
    prompt: str,
    conversation_history: Optional[list],
    user_id: str,
    *,
    migration_intent: Optional[dict] = None,
) -> Dict[str, Any]:
    """Build determine_sql_table_and_query result from authoritative extract contract."""
    from services.architect_agent import create_optimized_schema, get_user_extraction_limit
    from handlers.sql_handler import generate_sql_query_for_tables
    from services.planning.sql_extract_contract import (
        collect_sql_fk_relationships,
        filter_tables_to_catalog,
    )

    primary = (extract_contract.get("primary_table") or "").strip()
    table_names = list(extract_contract.get("table_names") or ([primary] if primary else []))
    table_names = filter_tables_to_catalog(table_names, connector_data) or table_names
    if not table_names:
        return {
            "needs_clarification": True,
            "clarification_message": "Plan extract contract tables are not in this database connection.",
        }

    table_schemas: Dict[str, Any] = {}
    for table in connector_data.get("tables", []):
        table_dict = table if isinstance(table, dict) else dict(table)
        tname = (table_dict.get("table_name") or "").strip()
        if tname not in table_names:
            continue
        schema_data = table_dict.get("schema_data") or {}
        if isinstance(schema_data, str):
            import json

            try:
                schema_data = json.loads(schema_data)
            except (json.JSONDecodeError, TypeError):
                schema_data = {}
        table_schemas[tname] = create_optimized_schema(schema_data)

    selected_columns = list(
        extract_contract.get("allowed_select_columns")
        or extract_contract.get("selected_columns")
        or []
    )
    scope = dict(extract_contract.get("field_scope") or {})
    fk_relationships = collect_sql_fk_relationships(
        table_names,
        connector_data.get("tables") or [],
    )
    column_result = {
        "primary_table": primary or table_names[0],
        "table_names": table_names,
        "selected_columns": selected_columns,
        "column_metadata": extract_contract.get("column_metadata")
        or [
            {"table_name": primary or table_names[0], "column_name": c, "name": c}
            for c in selected_columns
        ],
        "extract_field_scope": scope,
        "fk_relationships": fk_relationships,
    }

    extraction_limit = await get_user_extraction_limit(str(user_id))
    result = await generate_sql_query_for_tables(
        prompt,
        table_names,
        connector_data,
        conversation_history,
        user_id=str(user_id),
        extraction_limit=extraction_limit,
        extract_field_scope=scope,
        migration_intent=migration_intent,
        column_selection=column_result,
    )
    if result:
        result.setdefault("table_names", table_names)
        result.setdefault("table_schemas", table_schemas)
    return result or {
        "needs_clarification": True,
        "clarification_message": "Could not build SQL from the plan extract contract.",
    }
