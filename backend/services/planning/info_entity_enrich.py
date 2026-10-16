"""Info-path entity hint from connector semantics (not connector binding)."""
from __future__ import annotations

from typing import Any, Dict, List

from services.labeling.catalog_helpers import fetch_semantics_map
from services.planning.label_match import match_entity_in_prompt


async def _entity_summaries_for_connector(
    user_id: str,
    connector_type: str,
    connector_id: str | None,
) -> List[Dict[str, Any]]:
    if not connector_id:
        return []
    try:
        rows = await fetch_semantics_map(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=str(connector_id),
        )
    except Exception:
        return []
    return [
        {
            "entity_key": key,
            "entity_display_name": key,
            "semantics": sem,
        }
        for key, sem in (rows or {}).items()
    ]


async def enrich_info_entity_from_labels(
    out: Dict[str, Any],
    prompt: str,
    user_id: str,
) -> None:
    from services.planning.binding.guards import (
        destination_resolved_by_binding,
        plan_contract_from_classification,
    )

    contract = plan_contract_from_classification(out)
    if destination_resolved_by_binding(contract):
        return
    if (out.get("destination_object_api_name") or "").strip():
        return
    dest_type = (out.get("destination") or "").lower()
    dest_id = out.get("destination_connector_id")
    if dest_type not in ("salesforce", "sql", "google_sheets") or not dest_id:
        return
    entities = await _entity_summaries_for_connector(user_id, dest_type, str(dest_id))
    hit = match_entity_in_prompt(prompt, entities)
    if hit:
        from services.planning.entity_catalog_policy import (
            is_salesforce_metadata_object,
            is_vague_user_data_migration_prompt,
            prefer_user_data_entity_from_catalog,
        )

        if (
            dest_type == "salesforce"
            and is_vague_user_data_migration_prompt(prompt)
            and is_salesforce_metadata_object(hit)
        ):
            catalog_rows = [
                {"api_name": str(e.get("entity_key") or ""), "label": str(e.get("entity_key") or "")}
                for e in entities
                if e.get("entity_key")
            ]
            preferred = prefer_user_data_entity_from_catalog(
                prompt=prompt,
                connector_type="salesforce",
                catalog=catalog_rows,
                current_key=hit,
            )
            if preferred:
                hit = preferred
        if dest_type == "salesforce":
            out["destination_object_api_name"] = hit
        elif dest_type == "sql":
            out["destination_table_name"] = hit
