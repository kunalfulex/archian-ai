"""Validate PLAN_SCOPE ops — strip incoherent pipeline tokens; ensure map before migrate."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _normalize_ops(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [str(o).lower() for o in raw if str(o).strip()]


def validate_plan_scope_ops(
    operations: List[str],
    *,
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Keep PLAN_SCOPE pipeline_ops that are structurally valid for classification state.

    Does not add ops — only removes tokens the LLM cannot execute yet (e.g. extract
    on compound create/export flows without a resolved source). Fresh extract-only
    scopes with needs_source / source_phrase keep extract so source binding runs next.
    """
    ops = list(operations or [])
    original = list(ops)
    cls = dict(classification or {})
    goal = dict(parsed_goal or {})
    active_job = bool((cls.get("active_job_id") or "").strip())

    from services.planning.connector_guards import classification_source_endpoint_resolved
    from services.file_extraction.pending_upload import is_file_source

    source_ready = classification_source_endpoint_resolved(cls, pipeline_tokens=ops)
    file_attached = is_file_source(cls)

    if "extract" in ops and not source_ready and not active_job:
        keep_extract = (
            file_attached
            or goal.get("needs_source") is True
            or bool((goal.get("source_phrase") or "").strip())
        )
        if not keep_extract:
            ops = [o for o in ops if o != "extract"]
        else:
            logger.info(
                "[plan_scope] keeping extract pending source bind "
                "needs_source=%s source_phrase=%r file_attached=%s",
                goal.get("needs_source"),
                (goal.get("source_phrase") or "")[:80] or None,
                file_attached,
            )

    extract_stripped = "extract" in original and "extract" not in ops
    if not active_job and "extract" not in ops:
        # Attached chat files keep map/migrate even if extract was pending bind.
        if file_attached and ("map" in ops or "migrate" in ops):
            return ops
        standalone_map = set(ops) <= {"map", "migrate", "data_ops"} and any(
            o in ops for o in ("map", "migrate")
        )
        if extract_stripped or "create" in ops:
            ops = [o for o in ops if o not in ("map", "migrate")]
        elif not standalone_map and ("map" in ops or "migrate" in ops):
            ops = [o for o in ops if o not in ("map", "migrate")]

    return ops


def _strip_create_for_existing_gsheet_dest(
    goal: Dict[str, Any],
    classification: Dict[str, Any],
) -> Dict[str, Any]:
    from services.planning.goal_reconciliation import catalog_proved_existing_gsheet_destination

    out = dict(goal or {})
    if not catalog_proved_existing_gsheet_destination(classification, None):
        return out
    ops = _normalize_ops(out.get("operations"))
    if not ops:
        ops = _normalize_ops(out.get("pipeline_ops"))
    if "create" in ops:
        ops = [o for o in ops if o != "create"]
        out["operations"] = ops
        if "pipeline_ops" in out or ops:
            out["pipeline_ops"] = ops
        out["create_destination"] = False
    return out


def _coerce_spreadsheet_header_source(goal: Dict[str, Any]) -> Dict[str, Any]:
    """Repair incoherent PLAN_SCOPE header policy vs spreadsheet_headers list."""
    from services.planning.source_column_headers import (
        HEADER_SOURCE_FROM_EXTRACTION,
        HEADER_SOURCE_FROM_USER,
        HEADER_SOURCE_USER_SPECIFIED_INLINE,
        normalize_spreadsheet_header_source,
    )

    out = dict(goal or {})
    source = normalize_spreadsheet_header_source(out.get("spreadsheet_header_source"))
    headers: List[str] = []
    if isinstance(out.get("spreadsheet_headers"), list):
        headers = [str(h).strip() for h in out["spreadsheet_headers"] if str(h).strip()]

    if source == HEADER_SOURCE_FROM_EXTRACTION:
        out.pop("spreadsheet_headers", None)
        out["spreadsheet_header_source"] = HEADER_SOURCE_FROM_EXTRACTION
    elif source == HEADER_SOURCE_USER_SPECIFIED_INLINE and not headers:
        out["spreadsheet_header_source"] = HEADER_SOURCE_FROM_USER
    elif headers and not source:
        out["spreadsheet_header_source"] = HEADER_SOURCE_USER_SPECIFIED_INLINE
    elif source:
        out["spreadsheet_header_source"] = source
    return out


def validate_plan_scope_goal(
    goal: Dict[str, Any],
    classification: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate scope goal dict in place on operations / pipeline_ops."""
    out = _strip_create_for_existing_gsheet_dest(goal, classification)
    out = _coerce_spreadsheet_header_source(out)
    ops = _normalize_ops(out.get("operations"))
    if not ops:
        ops = _normalize_ops(out.get("pipeline_ops"))
    ops_before = list(ops)
    ops = validate_plan_scope_ops(ops, classification=classification, parsed_goal=out)
    if ops != ops_before:
        from services.planning.observability import log_plan_scope_trace

        log_plan_scope_trace(
            "validator_stripped",
            operations=ops,
            needs_source=out.get("needs_source"),
            needs_destination=out.get("needs_destination"),
            source_phrase=out.get("source_phrase"),
            destination_phrase=out.get("destination_phrase"),
            reason=f"{ops_before} -> {ops}",
        )

    cls = dict(classification or {})
    active_job = bool((cls.get("active_job_id") or "").strip())
    if active_job and "map" in ops and "migrate" not in ops:
        if not out.get("needs_destination"):
            dest_named = bool((cls.get("destination_name") or "").strip())
            dest_entity = bool(
                (cls.get("destination_object_api_name") or "").strip()
                or (cls.get("destination_table_name") or "").strip()
                or (cls.get("destination_sheet_name") or "").strip()
                or cls.get("planner_destination_entity_confirmed")
            )
            if not dest_named and not dest_entity:
                ops = [o for o in ops if o != "map"]

    progress = cls.get("_session_progress") or {}
    mapping_ready = bool(
        progress.get("mapping_ready")
        or cls.get("mapping_ready")
    )
    from services.planning.plan_scope import ensure_map_for_migrate_goal, finalize_plan_scope_goal

    out["operations"] = ops
    if "pipeline_ops" in out or ops:
        out["pipeline_ops"] = ops
    out = ensure_map_for_migrate_goal(out, mapping_ready=mapping_ready)
    return finalize_plan_scope_goal(out)


def validate_and_normalize_plan_scope(
    scope: Dict[str, Any],
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Validate PLAN_SCOPE output against connector / job state (no keyword rewriting)."""
    del prompt
    out_scope = validate_plan_scope_goal(dict(scope or {}), classification)
    return out_scope, dict(classification or {})
