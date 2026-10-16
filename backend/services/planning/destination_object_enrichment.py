"""Defer catalog destination-object resolution until source context is ready."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.mapping_plan.catalog_target_resolver import (
    resolve_google_sheets_destination_from_catalog,
    resolve_salesforce_destination_from_catalog,
    resolve_sql_destination_table_from_catalog,
)
from utils.destination_connector_resolve import (
    resolve_salesforce_token_record,
    resolve_sql_connector_record,
)


def should_defer_destination_object_enrichment_early(
    classification: Dict[str, Any],
) -> bool:
    """
    Defer catalog destination-object resolution until extract source is pinned.

    Round-trip (destination_same_as_source) and fresh extract→migrate turns defer until
    the user has picked source connector/entity/fields.
    """
    from services.planning.connector_guards import classification_source_endpoint_resolved

    if classification.get("destination_same_as_source"):
        return True
    tokens = classification.get("_pipeline_tokens") or []
    if "extract" in tokens and not classification_source_endpoint_resolved(classification):
        return True
    from services.planning.extract_field_scope import extract_portability_context

    if extract_portability_context(classification).get("heterogeneous"):
        src_entity = (
            (classification.get("source_table_name") or "")
            or (classification.get("source_object_api_name") or "")
            or (classification.get("source_sheet_name") or "")
        ).strip()
        if not src_entity and not classification.get("planner_source_entity_confirmed"):
            return True
    return False


def _enrich_prompt_with_migration_context(
    prompt: Optional[str],
    migration_intent: Any = None,
) -> str:
    base = (prompt or "").strip()
    if not migration_intent:
        return base
    parts = [base] if base else []
    label = (
        getattr(migration_intent, "driving_entity_label", None)
        or getattr(migration_intent, "driving_entity_key", None)
        or ""
    )
    if str(label).strip():
        parts.append(f"source entity: {str(label).strip()}")
    hints = getattr(migration_intent, "field_hints", None) or []
    if hints and getattr(migration_intent, "field_hints_strict", False):
        parts.append("columns: " + ", ".join(str(h) for h in hints if str(h).strip()))
    dest_phrase = getattr(migration_intent, "destination_entity_phrase", None)
    if dest_phrase and str(dest_phrase).strip():
        parts.append(f"destination: {str(dest_phrase).strip()}")
    return " ".join(parts).strip()


async def enrich_destination_target_from_prompt(
    classification: Dict[str, Any],
    *,
    user_id: str,
    prompt: Optional[str],
    migration_intent: Any = None,
) -> Dict[str, Any]:
    """
    For compound extract→migrate prompts, persist destination object/table/sheet early
    using classifier output validated against DB catalog, then catalog LLM if needed.
    """
    out = dict(classification or {})
    dest = (out.get("destination") or "").strip().lower()
    if dest not in ("salesforce", "sql", "google_sheets", "gsheets"):
        return out
    if out.get("planner_destination_entity_confirmed"):
        return out
    effective_prompt = _enrich_prompt_with_migration_context(prompt, migration_intent)
    if not effective_prompt:
        return out

    dest_name = (out.get("destination_name") or "").strip() or None
    hint_override = None
    if migration_intent:
        hint_override = (
            getattr(migration_intent, "destination_entity_key", None)
            or getattr(migration_intent, "destination_entity_phrase", None)
        )
        if hint_override:
            hint_override = str(hint_override).strip() or None

    if dest == "salesforce":
        tr = await resolve_salesforce_token_record(user_id, destination_name=dest_name)
        org_id = tr.get("org_id") if tr else None
        resolved = await resolve_salesforce_destination_from_catalog(
            user_id=user_id,
            org_id=org_id,
            prompt=effective_prompt,
            hint=hint_override
            or (out.get("destination_object_api_name") or "").strip()
            or None,
        )
        if resolved:
            from services.planning.entity_catalog_policy import (
                is_salesforce_metadata_object,
                is_vague_user_data_migration_prompt,
                prefer_user_data_entity_from_catalog,
            )

            if is_vague_user_data_migration_prompt(effective_prompt) and is_salesforce_metadata_object(
                resolved
            ):
                from services.mapping_plan.catalog_target_resolver import (
                    _load_salesforce_org_objects,
                )

                catalog = await _load_salesforce_org_objects(user_id, org_id)
                preferred = prefer_user_data_entity_from_catalog(
                    prompt=effective_prompt,
                    connector_type="salesforce",
                    catalog=[
                        {
                            "api_name": (row.get("object_api_name") or "").strip(),
                            "label": (row.get("label") or row.get("object_api_name") or "").strip(),
                        }
                        for row in catalog
                        if (row.get("object_api_name") or "").strip()
                    ],
                    current_key=resolved,
                )
                if preferred:
                    resolved = preferred[0]
            out["destination_object_api_name"] = resolved
        return out

    if dest == "sql":
        tr = await resolve_sql_connector_record(user_id, destination_name=dest_name)
        connector_id = str(tr.get("id")) if tr and tr.get("id") else None
        hint = (
            (out.get("destination_table_name") or out.get("destination_table") or "")
            .strip()
            or None
        )
        resolved = await resolve_sql_destination_table_from_catalog(
            user_id=user_id,
            connector_id=connector_id,
            prompt=effective_prompt,
            hint=hint_override or hint,
        )
        if resolved:
            out["destination_table_name"] = resolved
        return out

    # google_sheets / gsheets
    spreadsheet_id = (out.get("destination_spreadsheet_id") or out.get("destination_connector_id") or "").strip() or None
    hint = (out.get("destination_sheet_name") or "").strip() or None
    resolved = await resolve_google_sheets_destination_from_catalog(
        user_id=user_id,
        spreadsheet_name=dest_name,
        spreadsheet_id=spreadsheet_id,
        prompt=effective_prompt,
        hint=hint_override or hint,
    )
    if resolved:
        out["destination_sheet_name"] = resolved
    return out


# Backward-compatible alias
async def enrich_salesforce_destination_object_from_prompt(
    classification: Dict[str, Any],
    *,
    user_id: str,
    prompt: Optional[str],
) -> Dict[str, Any]:
    return await enrich_destination_target_from_prompt(
        classification, user_id=user_id, prompt=prompt
    )
