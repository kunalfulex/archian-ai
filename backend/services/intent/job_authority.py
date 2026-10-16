"""Lock source/entity pins from JobContext during continuation planning."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.job_context import JobContext


def apply_job_authority(cls: Dict[str, Any], job: JobContext) -> Dict[str, Any]:
    """
    Re-apply job-derived source pins after destination resolution.

    Prevents destination enrichment from overwriting extract job facts.
    """
    out = dict(cls or {})
    st = (job.source.connector_type or "").strip().lower()
    if st == "gsheets":
        st = "google_sheets"

    if st and st not in ("unknown", ""):
        out["source"] = st
    if job.source.display_name:
        out["source_name"] = job.source.display_name
    if job.source.connector_id:
        out["source_connector_id"] = job.source.connector_id

    entity = (job.source.entity_key or "").strip()
    if entity:
        out["source_entity_key"] = entity
        if st == "salesforce":
            out["source_object_api_name"] = entity
        elif st == "sql":
            out["source_table_name"] = entity
        elif st in ("google_sheets", "gsheets"):
            out["source_sheet_name"] = entity
        elif st == "file":
            out["document_type"] = entity

    contract = out.get("plan_contract")
    if isinstance(contract, dict):
        contract = dict(contract)
        src = dict(contract.get("source") or {})
        if st:
            src["connector_type"] = st
        if job.source.connector_id:
            src["connector_id"] = job.source.connector_id
        if job.source.display_name:
            src["display_name"] = job.source.display_name
        if entity:
            src["entity_key"] = entity
            src["entity_label"] = entity
            src["catalog_label"] = entity
        contract["source"] = src
        out["plan_contract"] = contract

    if job.job_id:
        out["active_job_id"] = job.job_id

    return out
