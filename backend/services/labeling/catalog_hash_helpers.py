"""Schema hash helpers for semantics-aware labeling scheduling."""
from __future__ import annotations

from typing import Dict, List

from db.connection import database
from services.connector_metadata.drift import fingerprint_salesforce_object


async def update_salesforce_object_hashes(user_id: str, org_id: str) -> Dict[str, str]:
    """Recompute and persist schema_hash for SF objects with fields; return current map."""
    rows = await database.fetch_all(
        """
        SELECT object_api_name FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
          AND COALESCE(is_deleted, FALSE) = FALSE
          AND EXISTS (
            SELECT 1 FROM salesforce_object_fields f
            WHERE f.user_id = salesforce_objects.user_id
              AND f.org_id = salesforce_objects.org_id
              AND f.object_api_name = salesforce_objects.object_api_name
          )
        ORDER BY object_api_name
        """,
        {"user_id": str(user_id), "org_id": str(org_id)},
    )
    current: Dict[str, str] = {}
    for r in rows or []:
        name = dict(r).get("object_api_name")
        if not name:
            continue
        fields = await database.fetch_all(
            """
            SELECT field_api_name, data_type, is_required, is_lookup, lookup_object_api_name
            FROM salesforce_object_fields
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
            """,
            {"user_id": str(user_id), "org_id": str(org_id), "obj": name},
        )
        field_dicts = [dict(f) for f in fields or []]
        h = fingerprint_salesforce_object(field_dicts)
        current[str(name)] = h
        await database.execute(
            """
            UPDATE salesforce_objects
            SET schema_hash = :schema_hash
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :obj
            """,
            {"user_id": str(user_id), "org_id": str(org_id), "obj": name, "schema_hash": h},
        )
    return current


async def salesforce_legacy_uncategorized_keys(user_id: str, org_id: str) -> List[str]:
    """Objects with legacy category unset (backward compat for old categorization column)."""
    rows = await database.fetch_all(
        """
        SELECT object_api_name FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
          AND category IS NULL
          AND COALESCE(is_deleted, FALSE) = FALSE
          AND EXISTS (
            SELECT 1 FROM salesforce_object_fields f
            WHERE f.user_id = salesforce_objects.user_id
              AND f.org_id = salesforce_objects.org_id
              AND f.object_api_name = salesforce_objects.object_api_name
          )
        ORDER BY object_api_name
        """,
        {"user_id": str(user_id), "org_id": str(org_id)},
    )
    return [dict(r)["object_api_name"] for r in rows or [] if dict(r).get("object_api_name")]
