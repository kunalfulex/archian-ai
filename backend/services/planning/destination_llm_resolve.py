"""LLM destination connector resolution — fresh + continuation (catalog validate after apply)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.job_context import JobContext
from services.planning.destination_catalog_validate import (
    destination_connector_slot_satisfied,
    ensure_google_sheets_connection_pin,
    mark_destination_validated_from_llm,
    read_needs_destination,
    strip_unvalidated_destination_confirm_flags,
    validate_destination_connector_pins,
)
from services.planning.resource_catalog import ResourceCatalog


def _job_summary(classification: Dict[str, Any], job: Optional[JobContext]) -> Dict[str, Any]:
    if job is not None:
        from services.planning.continuation_destination import job_summary_for_connector_llm

        return job_summary_for_connector_llm(job)
    return {
        "source_type": classification.get("source"),
        "source_name": classification.get("source_name"),
        "prior_destination_type": classification.get("destination"),
        "prior_destination_name": classification.get("destination_name"),
    }


async def resolve_destination_connector_via_llm(
    classification: Dict[str, Any],
    *,
    prompt: str,
    parsed_goal: Dict[str, Any],
    catalog: ResourceCatalog,
    user_id: str,
    job: Optional[JobContext] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run catalog destination LLM when PLAN_SCOPE requires destination and slot is open.
    Applies pick only when pins validate against catalog.
    """
    goal = dict(parsed_goal or {})
    out = strip_unvalidated_destination_confirm_flags(dict(classification or {}))

    from services.planning.create_metadata import (
        classification_requests_new_spreadsheet,
        clear_catalog_workbook_pins_for_create,
    )

    if classification_requests_new_spreadsheet(out, goal):
        return clear_catalog_workbook_pins_for_create(out, goal)

    if not read_needs_destination(out, goal):
        return out
    if destination_connector_slot_satisfied(out, catalog, parsed_goal=goal):
        return out

    out["_destination_connector_llm_attempted"] = True

    from services.planning.connector_llm_resolver import (
        apply_destination_connector_pick,
        llm_resolve_destination_connector,
    )

    dest_phrase = (
        (goal.get("destination_phrase") or out.get("destination_phrase") or "").strip() or None
    )
    pick = await llm_resolve_destination_connector(
        user_id=str(user_id),
        prompt=prompt or "",
        destination_phrase=dest_phrase,
        catalog=catalog,
        job_summary=_job_summary(out, job),
        conversation_id=conversation_id,
    )
    if not pick:
        return out

    applied = apply_destination_connector_pick(out, pick, catalog)
    from services.planning.google_sheets_destination import normalize_google_sheets_destination_pins

    applied = normalize_google_sheets_destination_pins(applied, catalog)
    applied = ensure_google_sheets_connection_pin(applied, catalog)
    if validate_destination_connector_pins(applied, catalog):
        return mark_destination_validated_from_llm(applied, catalog=catalog)
    return strip_unvalidated_destination_confirm_flags(applied)
