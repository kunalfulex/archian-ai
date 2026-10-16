"""Load destination schema for Salesforce field mapping (pinned entity first)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.salesforce_mapping_target import lookup_salesforce_object_in_org


@dataclass
class SalesforceMappingSchema:
    base_object_names: List[str] = field(default_factory=list)
    lookup_object_names: List[str] = field(default_factory=list)
    objects_structure: Dict[str, Any] = field(default_factory=dict)
    object_description_block: str = ""
    document_type: str = ""
    document_description: str = ""
    available_objects: Dict[str, Any] = field(default_factory=dict)
    target_object_api_name: Optional[str] = None

    @property
    def all_object_names(self) -> List[str]:
        return list(set(self.base_object_names + self.lookup_object_names))


async def _resolve_base_objects_legacy(
    *,
    user_id: Any,
    org_id: Optional[str],
    document_type: str,
    document_description: str,
    target_hint: str,
    destination_name: Optional[str],
) -> List[str]:
    """Category / label fallback when no explicit pinned target (non-plan flows)."""
    from db.connection import database
    from utils.mapping_user_messages import mapping_destination_clarification_message

    base_objects_query = """
        SELECT object_api_name
        FROM salesforce_objects
        WHERE LOWER(TRIM(category)) = LOWER(TRIM(:category))
          AND LOWER(TRIM(category_description)) = LOWER(TRIM(:category_description))
          AND user_id = :user_id
    """
    base_values: Dict[str, Any] = {
        "category": document_type,
        "category_description": document_description,
        "user_id": str(user_id),
    }
    if org_id:
        base_objects_query += " AND org_id = :org_id"
        base_values["org_id"] = org_id

    base_objects = await database.fetch_all(base_objects_query, values=base_values)
    base_object_names = [r["object_api_name"] for r in base_objects]

    if not base_object_names and target_hint:
        target_first_query = """
            SELECT object_api_name
            FROM salesforce_objects
            WHERE (LOWER(TRIM(label)) = LOWER(TRIM(:target_hint))
               OR LOWER(TRIM(object_api_name)) = LOWER(TRIM(:target_hint)))
              AND user_id = :user_id
        """
        target_first_values: Dict[str, Any] = {
            "target_hint": target_hint,
            "user_id": str(user_id),
        }
        if org_id:
            target_first_query += " AND org_id = :org_id"
            target_first_values["org_id"] = org_id
        target_first = await database.fetch_all(
            target_first_query, values=target_first_values
        )
        if target_first:
            base_object_names = [r["object_api_name"] for r in target_first]

    if not base_object_names:
        fallback_query = """
            SELECT object_api_name
            FROM salesforce_objects
            WHERE (LOWER(TRIM(label)) = LOWER(TRIM(:document_type))
               OR LOWER(TRIM(object_api_name)) = LOWER(TRIM(:document_type)))
              AND user_id = :user_id
        """
        fallback_values: Dict[str, Any] = {
            "document_type": document_type,
            "user_id": str(user_id),
        }
        if org_id:
            fallback_query += " AND org_id = :org_id"
            fallback_values["org_id"] = org_id
        base_objects = await database.fetch_all(fallback_query, values=fallback_values)
        base_object_names = [r["object_api_name"] for r in base_objects]

    if not base_object_names:
        raise ValueError(
            mapping_destination_clarification_message(
                destination_name=destination_name,
                platform="salesforce",
                document_type=document_type,
            )
        )
    return base_object_names


async def _build_objects_structure(
    *,
    user_id: Any,
    org_id: Optional[str],
    all_objects: List[str],
    flat_data: Dict[str, Any],
) -> Dict[str, Any]:
    from db.connection import database

    objects_structure: Dict[str, Any] = {}
    for obj in all_objects:
        obj_meta_query = """
            SELECT label, description FROM salesforce_objects
            WHERE object_api_name = :obj AND user_id = :user_id
        """
        obj_meta_values: Dict[str, Any] = {"obj": obj, "user_id": str(user_id)}
        if org_id:
            obj_meta_query += " AND org_id = :org_id"
            obj_meta_values["org_id"] = org_id
        obj_meta = await database.fetch_one(obj_meta_query, values=obj_meta_values)
        obj_meta_dict = dict(obj_meta) if obj_meta else None

        fields_query = """
            SELECT field_api_name, field_label, data_type, field_description, is_required, is_defaulted_on_create,
                   is_lookup, lookup_object_api_name
            FROM salesforce_object_fields
            WHERE object_api_name = :obj AND user_id = :user_id
        """
        fields_values: Dict[str, Any] = {"obj": obj, "user_id": str(user_id)}
        if org_id:
            fields_query += " AND org_id = :org_id"
            fields_values["org_id"] = org_id
        fields = await database.fetch_all(fields_query, values=fields_values)

        objects_structure[obj] = {
            "label": obj_meta_dict["label"] if obj_meta_dict else obj,
            "description": obj_meta_dict["description"] if obj_meta_dict else "",
            "fields": {},
        }

        for row in fields:
            field_dict = dict(row) if hasattr(row, "keys") else row
            field_info: Dict[str, Any] = {
                "label": field_dict["field_label"],
                "data_type": field_dict["data_type"],
                "description": field_dict["field_description"] or "",
                "is_required": field_dict["is_required"]
                and not field_dict["is_defaulted_on_create"],
            }
            if field_dict.get("is_lookup"):
                field_info["is_lookup"] = True
                if field_dict.get("lookup_object_api_name"):
                    field_info["lookup_object"] = field_dict["lookup_object_api_name"]
            objects_structure[obj]["fields"][field_dict["field_api_name"]] = field_info

    if "RecordTypeName" in flat_data:
        for obj in list(objects_structure.keys()):
            try:
                rt_rows = await database.fetch_all(
                    f"""
                    SELECT rt.record_type_id, rt.name, rt.developer_name
                    FROM salesforce_object_record_types rt
                    JOIN salesforce_objects o ON o.uuid_id = rt.salesforce_object_uuid
                    WHERE o.object_api_name = :object_api_name
                      AND rt.user_id = :user_id
                      AND rt.is_active = true
                      {"AND rt.org_id = :org_id" if org_id else ""}
                    """,
                    {
                        "object_api_name": obj,
                        "user_id": str(user_id),
                        **({"org_id": org_id} if org_id else {}),
                    },
                )
                if rt_rows:
                    valid_names = [row["name"] for row in rt_rows if row["name"]]
                    objects_structure[obj]["fields"]["RecordTypeId"] = {
                        "label": "Record Type",
                        "data_type": "reference",
                        "description": (
                            f"Record type for this {obj}. "
                            f"Valid record type names: {', '.join(valid_names)}. "
                            "Map the source RecordTypeName field to this field -- "
                            "the migration engine will resolve the name to the correct ID."
                        ),
                        "is_required": False,
                    }
            except Exception as exc:
                print(f"⚠️ [RecordType] Failed to enrich objects_structure for {obj}: {exc}")

    return objects_structure


async def _build_available_objects(
    *,
    user_id: Any,
    org_id: Optional[str],
    all_objects: List[str],
) -> Dict[str, Any]:
    from db.connection import database

    available_objects: Dict[str, Any] = {}
    for obj in all_objects:
        fields_query = """
            SELECT field_api_name, data_type, field_description, is_required
            FROM salesforce_object_fields
            WHERE object_api_name = :obj
              AND user_id = :user_id
              AND is_creatable = TRUE
        """
        fields_values: Dict[str, Any] = {"obj": obj, "user_id": str(user_id)}
        if org_id:
            fields_query += " AND org_id = :org_id"
            fields_values["org_id"] = org_id
        fields = await database.fetch_all(fields_query, values=fields_values)
        available_objects[obj] = {
            f["field_api_name"]: {
                "type": f["data_type"],
                "description": f["field_description"],
                "is_required": f["is_required"],
            }
            for f in fields
        }
    return available_objects


async def load_salesforce_mapping_schema(
    *,
    user_id: Any,
    org_id: Optional[str],
    flat_data: Dict[str, Any],
    document_type: str,
    document_description: str,
    explicit_target_object_api_name: Optional[str] = None,
    destination_name: Optional[str] = None,
) -> SalesforceMappingSchema:
    """
    Load base object + lookup schema for mapping.

    When ``explicit_target_object_api_name`` is set (plan-pinned), uses that object only
    and skips category-based object discovery.
    """
    from db.connection import database

    doc_type = document_type
    doc_desc = document_description
    pinned = (explicit_target_object_api_name or "").strip()
    base_object_names: List[str] = []

    if pinned and org_id:
        target_meta = await lookup_salesforce_object_in_org(user_id, org_id, pinned)
        if target_meta:
            base_object_names = [str(target_meta.get("object_api_name") or pinned)]
            doc_type = str(target_meta.get("category") or doc_type or pinned)
            doc_desc = str(target_meta.get("category_description") or doc_desc)
        else:
            base_object_names = [pinned]
            doc_type = doc_type or pinned
    elif pinned:
        base_object_names = [pinned]
        doc_type = doc_type or pinned

    if not base_object_names:
        base_object_names = await _resolve_base_objects_legacy(
            user_id=user_id,
            org_id=org_id,
            document_type=doc_type,
            document_description=doc_desc,
            target_hint="",
            destination_name=destination_name,
        )

    lookup_query = """
        SELECT DISTINCT lookup_object_api_name
        FROM salesforce_object_fields
        WHERE object_api_name = ANY(:base_objects)
          AND is_lookup = true
          AND user_id = :user_id
    """
    lookup_values: Dict[str, Any] = {
        "base_objects": base_object_names,
        "user_id": str(user_id),
    }
    if org_id:
        lookup_query += " AND org_id = :org_id"
        lookup_values["org_id"] = org_id

    lookup_objects = await database.fetch_all(lookup_query, values=lookup_values)
    lookup_object_names = [
        r["lookup_object_api_name"] for r in lookup_objects if r["lookup_object_api_name"]
    ]
    all_objects = list(set(base_object_names + lookup_object_names))

    objects_structure = await _build_objects_structure(
        user_id=user_id,
        org_id=org_id,
        all_objects=all_objects,
        flat_data=flat_data,
    )
    available_objects = await _build_available_objects(
        user_id=user_id,
        org_id=org_id,
        all_objects=all_objects,
    )

    return SalesforceMappingSchema(
        base_object_names=base_object_names,
        lookup_object_names=lookup_object_names,
        objects_structure=objects_structure,
        object_description_block=json.dumps(objects_structure, indent=2),
        document_type=doc_type,
        document_description=doc_desc,
        available_objects=available_objects,
        target_object_api_name=pinned or (base_object_names[0] if base_object_names else None),
    )
