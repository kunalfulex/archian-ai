"""Continuation destination connector resolution (PLAN_SCOPE + catalog LLM)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.intent.job_context import JobContext
from services.planning.resource_catalog import ResourceCatalog


def needs_map_on_continuation(cont_steps: List[str], job: JobContext) -> bool:
    """Deprecated alias — use continuation_needs_destination_connector_bind with goal."""
    from services.planning.plan_scope import tokens_need_map_destination_llm

    return tokens_need_map_destination_llm(cont_steps, job)


def job_summary_for_connector_llm(job: JobContext) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "job_id": job.job_id,
        "pipeline_stage": job.pipeline_stage,
        "source_type": job.source.connector_type,
        "source_name": job.source.display_name,
        "source_entity": job.source.entity_key,
    }
    if job.destination:
        summary["prior_destination_type"] = job.destination.connector_type
        summary["prior_destination_name"] = job.destination.display_name
        summary["prior_destination_entity"] = job.destination.entity_key
    return summary


async def _resolve_gs_workbook_from_scope(
    classification: Dict[str, Any],
    *,
    goal: Dict[str, Any],
    catalog: ResourceCatalog,
    user_id: str,
    prompt: Optional[str],
) -> Dict[str, Any]:
    from services.planning.google_sheets_destination import (
        gsheets_workbook_destination_resolved,
        normalize_google_sheets_destination_pins,
        resolve_and_apply_google_sheets_workbook,
        workbook_name_hint_from_destination_phrase,
    )
    from services.planning.schemas import normalize_connector_type

    out = dict(classification or {})
    if normalize_connector_type(out.get("destination") or "") not in (
        "google_sheets",
        "gsheets",
    ):
        return out
    out = normalize_google_sheets_destination_pins(out, catalog)
    if gsheets_workbook_destination_resolved(out, catalog):
        return out

    dest_phrase = (
        (goal.get("destination_phrase") or out.get("destination_phrase") or "").strip()
    )
    account = (out.get("destination_account_name") or "").strip() or None
    workbook = workbook_name_hint_from_destination_phrase(
        dest_phrase,
        account_display_name=account,
        catalog=catalog,
    )
    if not workbook:
        return out

    sheet_hint = (out.get("destination_sheet_name") or "").strip() or None
    out = await resolve_and_apply_google_sheets_workbook(
        out,
        workbook_name=workbook,
        sheet_name=sheet_hint,
        catalog=catalog,
        user_id=str(user_id),
    )
    return normalize_google_sheets_destination_pins(out, catalog)


async def enrich_continuation_destination_connectors(
    classification: Dict[str, Any],
    *,
    prompt: Optional[str],
    catalog: ResourceCatalog,
    goal: Dict[str, Any],
    job: JobContext,
    user_id: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """PLAN_SCOPE phrases + job context → destination connector catalog LLM (account/org/DB only)."""
    from services.planning.connector_llm_resolver import (
        apply_destination_connector_pick,
        llm_resolve_destination_connector,
    )
    from services.planning.continuation_policy import (
        apply_continuation_endpoint_pins_from_job,
        continuation_needs_destination_connector_bind,
        continuation_needs_destination_llm_resolution,
        sync_plan_contract_destination_from_classification,
    )
    from services.planning.destination_catalog_validate import (
        ensure_google_sheets_connection_pin,
        mark_destination_validated_from_llm,
        strip_unvalidated_destination_confirm_flags,
        validate_destination_connector_pins,
    )

    scoped = dict(goal or {})
    cont_steps: List[str] = list(classification.get("_continuation_steps") or [])

    from services.planning.continuation_policy import _is_clarification_resume
    from services.planning.destination_catalog_validate import destination_connector_slot_satisfied
    from services.planning.google_sheets_destination import normalize_google_sheets_destination_pins

    if _is_clarification_resume(classification) and destination_connector_slot_satisfied(
        classification, catalog, parsed_goal=scoped
    ):
        return normalize_google_sheets_destination_pins(dict(classification or {}), catalog)

    needs_bind = continuation_needs_destination_connector_bind(
        cont_steps, scoped, job, catalog=catalog
    )

    out = apply_continuation_endpoint_pins_from_job(
        classification, scoped, job, catalog=catalog
    )
    scoped = dict(out.get("_plan_scope") or scoped)
    out["_plan_scope"] = scoped

    if needs_bind and continuation_needs_destination_llm_resolution(
        out,
        scoped,
        job,
        catalog=catalog,
        needs_connector_bind=needs_bind,
        prompt=prompt,
    ):
        dest_phrase = (
            scoped.get("destination_phrase") or out.get("destination_phrase") or ""
        ).strip() or None
        pick = await llm_resolve_destination_connector(
            user_id=str(user_id),
            prompt=prompt or "",
            destination_phrase=dest_phrase,
            catalog=catalog,
            job_summary=job_summary_for_connector_llm(job),
            conversation_id=conversation_id,
        )
        if pick:
            out = apply_destination_connector_pick(out, pick, catalog)
            out = ensure_google_sheets_connection_pin(out, catalog)
            if validate_destination_connector_pins(out, catalog):
                out = mark_destination_validated_from_llm(out, catalog=catalog)
            else:
                out = strip_unvalidated_destination_confirm_flags(out)
            out = sync_plan_contract_destination_from_classification(
                out, resolution_source="llm_bind", confidence=0.9
            )
            out = await _resolve_gs_workbook_from_scope(
                out,
                goal=scoped,
                catalog=catalog,
                user_id=str(user_id),
                prompt=prompt,
            )
            out = sync_plan_contract_destination_from_classification(
                out, resolution_source="llm_bind", confidence=0.92
            )

    from services.planning.google_sheets_destination import (
        normalize_google_sheets_destination_pins,
    )

    return normalize_google_sheets_destination_pins(out, catalog)
