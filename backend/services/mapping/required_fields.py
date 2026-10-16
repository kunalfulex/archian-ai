"""Deterministic mapping post-processing (required fields, primary object filter)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


async def inject_required_salesforce_fields(
    mapping: Dict[str, Any],
    flat_data: Dict[str, Any],
    *,
    base_objects: List[str],
    lookup_objects: List[str],
    user_id: Any,
    org_id: Optional[str] = None,
    primary_base_object: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Filter orphaned base-object mappings and add missing required lookup fields.
    Replaces LLM ``evaluate_and_refine_mapping`` on plan-pinned executor paths.
    """
    from db.connection import database

    used_lookup_objects: Set[str] = set()
    base_object_source_field_counts = {obj: 0 for obj in base_objects}
    source_field_names = set(flat_data.keys()) if isinstance(flat_data, dict) else set()

    refined_mapping: Dict[str, Any] = {}
    for key, field_value in mapping.items():
        if isinstance(field_value, list):
            filtered_array = []
            for field_obj in field_value:
                if isinstance(field_obj, dict):
                    filtered_array.append(field_obj)
                    obj_name = field_obj.get("object")
                    if obj_name in lookup_objects:
                        used_lookup_objects.add(obj_name)
                    elif obj_name in base_objects and key in source_field_names:
                        base_object_source_field_counts[obj_name] += 1
            if filtered_array:
                refined_mapping[key] = filtered_array
        elif isinstance(field_value, dict):
            obj_name = field_value.get("object")
            if obj_name in base_objects and key in source_field_names:
                base_object_source_field_counts[obj_name] += 1
            refined_mapping[key] = field_value
            if obj_name in lookup_objects:
                used_lookup_objects.add(obj_name)

    pinned_primary = (primary_base_object or "").strip()
    if pinned_primary:
        primary_base_object = pinned_primary
    elif base_objects:
        max_count = max(base_object_source_field_counts.values()) if base_object_source_field_counts else 0
        primary_base_object = base_objects[0]
        if max_count > 0:
            for obj, count in base_object_source_field_counts.items():
                if count == max_count:
                    primary_base_object = obj
                    break
    else:
        primary_base_object = None

    if primary_base_object:
        filtered_refined_mapping: Dict[str, Any] = {}
        for key, field_value in refined_mapping.items():
            if isinstance(field_value, list):
                filtered_array = []
                for field_obj in field_value:
                    obj_name = field_obj.get("object")
                    if obj_name in lookup_objects or obj_name == primary_base_object:
                        filtered_array.append(field_obj)
                if filtered_array:
                    filtered_refined_mapping[key] = filtered_array
            elif isinstance(field_value, dict):
                obj_name = field_value.get("object")
                if obj_name in lookup_objects or obj_name == primary_base_object:
                    filtered_refined_mapping[key] = field_value
        refined_mapping = filtered_refined_mapping

    for lookup_object in used_lookup_objects:
        required_fields_query = """
            SELECT field_api_name
            FROM salesforce_object_fields
            WHERE object_api_name = :obj
              AND is_required = TRUE
              AND is_defaulted_on_create = FALSE
              AND is_creatable = TRUE
              AND user_id = :user_id
        """
        required_fields_values: Dict[str, Any] = {
            "obj": lookup_object,
            "user_id": str(user_id),
        }
        if org_id:
            required_fields_query += " AND org_id = :org_id"
            required_fields_values["org_id"] = org_id

        required_fields = await database.fetch_all(
            required_fields_query,
            values=required_fields_values,
        )

        for field_row in required_fields:
            field_name = field_row["field_api_name"]
            already_mapped = False
            for mapping_value in refined_mapping.values():
                if isinstance(mapping_value, list):
                    already_mapped = any(
                        isinstance(item, dict)
                        and item.get("object") == lookup_object
                        and item.get("field") == field_name
                        for item in mapping_value
                    )
                elif isinstance(mapping_value, dict):
                    if (
                        mapping_value.get("object") == lookup_object
                        and mapping_value.get("field") == field_name
                    ):
                        already_mapped = True
                if already_mapped:
                    break

            if not already_mapped:
                field_mapping: Dict[str, Any] = {
                    "object": lookup_object,
                    "field": field_name,
                    "value": "Unknown",
                    "missing": True,
                    "is_required": True,
                    "is_custom": False,
                }
                if primary_base_object and lookup_object != primary_base_object:
                    field_mapping["lookup_of"] = f"{primary_base_object}.{lookup_object}Id"
                refined_mapping[field_name] = field_mapping

    return refined_mapping


async def annotate_mapping_field_metadata(
    mapping: Dict[str, Any],
    *,
    user_id: Any,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach is_custom / is_required from synced catalog to mapping entries."""
    from db.connection import database

    for key, value in mapping.items():
        targets: List[Dict[str, Any]] = []
        if isinstance(value, list):
            targets = [item for item in value if isinstance(item, dict)]
        elif isinstance(value, dict):
            targets = [value]

        for item in targets:
            obj = item.get("object")
            field = item.get("field")
            if not obj or not field:
                continue
            field_meta_query = """
                SELECT is_custom, is_required FROM salesforce_object_fields
                WHERE object_api_name = :obj AND field_api_name = :field AND user_id = :user_id
            """
            field_meta_values: Dict[str, Any] = {
                "obj": obj,
                "field": field,
                "user_id": str(user_id),
            }
            if org_id:
                field_meta_query += " AND org_id = :org_id"
                field_meta_values["org_id"] = org_id
            result = await database.fetch_one(field_meta_query, values=field_meta_values)
            item["is_custom"] = result["is_custom"] if result else True
            item["is_required"] = bool(result["is_required"]) if result else False

    return mapping
