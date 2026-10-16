"""
Resolve the Salesforce object the user asked to map *to* (may differ from extraction category).

All destination object resolution uses the synced salesforce_objects catalog (+ LLM selection
from that catalog). No hardcoded standard-object lists or regex keyword fallbacks.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from db.connection import database


async def lookup_salesforce_object_in_org(
    user_id: Any,
    org_id: Optional[str],
    object_hint: str,
) -> Optional[dict]:
    """Resolve object_api_name + category metadata for a user/org."""
    hint = (object_hint or "").strip()
    if not hint or not org_id:
        return None
    row = await database.fetch_one(
        """
        SELECT object_api_name, label, category, category_description
        FROM salesforce_objects
        WHERE user_id = :user_id
          AND org_id = :org_id
          AND (
            LOWER(TRIM(object_api_name)) = LOWER(TRIM(:hint))
            OR LOWER(TRIM(label)) = LOWER(TRIM(:hint))
          )
        LIMIT 1
        """,
        values={"user_id": str(user_id), "org_id": org_id, "hint": hint},
    )
    return dict(row) if row else None


async def apply_mapping_target_object_override(
    *,
    user_id: Any,
    org_id: Optional[str],
    mapping_context_summary: Optional[str],
    base_object_names: List[str],
    document_type: str,
    document_description: str,
) -> Tuple[List[str], str, str, Optional[str]]:
    """
    When the user names a destination object in the map prompt, use it as the sole base object.

    Returns (base_object_names, document_type, document_description, resolved_api_name).
    """
    from services.mapping_plan.catalog_target_resolver import (
        resolve_salesforce_destination_from_catalog,
    )

    resolved_api = await resolve_salesforce_destination_from_catalog(
        user_id=user_id,
        org_id=org_id,
        prompt=mapping_context_summary,
    )
    if not resolved_api:
        return base_object_names, document_type, document_description, None

    meta = await lookup_salesforce_object_in_org(user_id, org_id, resolved_api)
    if not meta:
        return base_object_names, document_type, document_description, None

    api_name = str(meta.get("object_api_name") or resolved_api)
    new_type = str(meta.get("category") or document_type or api_name)
    new_desc = str(meta.get("category_description") or document_description or "")

    print(
        f"✅ Mapping target object from user prompt (catalog): {api_name} "
        f"(was base_objects={base_object_names}, document_type={document_type!r})"
    )
    return [api_name], new_type, new_desc, api_name
