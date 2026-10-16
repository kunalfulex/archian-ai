"""LLM source connector resolution — fresh + continuation (catalog validate after apply)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.job_context import JobContext
from services.planning.resource_catalog import ResourceCatalog
from services.planning.source_catalog_validate import (
    mark_source_validated_from_llm,
    read_needs_source,
    source_connector_slot_satisfied,
    strip_unvalidated_source_confirm_flags,
    validate_source_connector_pins,
)


async def resolve_source_connector_via_llm(
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
    Run catalog source LLM when PLAN_SCOPE requires source and slot is open.

    Architecture order: connector type + instance (SF / SQL / GS account) first,
    then GS workbook catalog only when source is already google_sheets.
    """
    del job
    goal = dict(parsed_goal or {})
    out = strip_unvalidated_source_confirm_flags(dict(classification or {}))

    if not read_needs_source(out, goal):
        return out
    if source_connector_slot_satisfied(out, catalog, parsed_goal=goal):
        return out

    out["_source_connector_llm_attempted"] = True

    source_phrase = (
        (goal.get("source_phrase") or out.get("source_phrase") or "").strip() or None
    )

    from services.planning.connector_guards import is_known_connector
    from services.planning.connector_llm_resolver import (
        apply_source_connector_pick,
        llm_resolve_source_connector,
    )
    from services.planning.google_sheets_source import normalize_google_sheets_source_pins
    from services.planning.schemas import normalize_connector_type

    src = normalize_connector_type(out.get("source") or "")

    if not is_known_connector(src):
        pick = await llm_resolve_source_connector(
            user_id=str(user_id),
            prompt=prompt or "",
            source_phrase=source_phrase,
            catalog=catalog,
            conversation_id=conversation_id,
        )
        if pick:
            applied = apply_source_connector_pick(out, pick, catalog)
            applied = normalize_google_sheets_source_pins(applied, catalog)
            if validate_source_connector_pins(applied, catalog):
                return mark_source_validated_from_llm(applied, catalog=catalog)
            out = strip_unvalidated_source_confirm_flags(applied)

    return out
