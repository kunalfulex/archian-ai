"""Detect multiple extract sources in one prompt for multi_source_extract plans."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.connectors.registry import connector_aliases
from services.planning.resource_catalog import ResourceCatalog


def _normalize_connector_type(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    return connector_aliases().get(raw, raw)


def classification_suggests_multi_source(classification: Optional[Dict[str, Any]]) -> bool:
    c = classification or {}
    if c.get("multi_source_pipeline") or c.get("planner_additional_sources"):
        return True
    if c.get("multi_source_extract") is True:
        return True
    raw = c.get("extract_sources")
    return isinstance(raw, list) and len(raw) >= 2


def prompt_or_classification_suggests_multi_source(
    classification: Optional[Dict[str, Any]],
    prompt: Optional[str],
) -> bool:
    del prompt
    return classification_suggests_multi_source(classification)


def _resolve_catalog_source(
    connector_type: Optional[str],
    display_name: str,
    catalog: ResourceCatalog,
) -> Optional[Dict[str, Any]]:
    dn = (display_name or "").strip()
    if not dn:
        return None

    ct = _normalize_connector_type(connector_type)
    tried: List[str] = []
    if ct:
        tried.append(ct)
    for fallback in ("salesforce", "sql", "google_sheets"):
        if fallback not in tried:
            tried.append(fallback)

    for kind in tried:
        if kind == "salesforce":
            for name in catalog.salesforce_names:
                if name.lower() == dn.lower():
                    return {"connector_type": "salesforce", "display_name": name}
        elif kind == "sql":
            for name in catalog.sql_names:
                if name.lower() == dn.lower():
                    return {"connector_type": "sql", "display_name": name}
        elif kind == "google_sheets":
            for sn in catalog.spreadsheet_names:
                if sn.lower() == dn.lower():
                    return {"connector_type": "google_sheets", "display_name": sn}
    return None


def _sheet_name_for_workbook(
    display_name: str,
    catalog: ResourceCatalog,
    preferred: Optional[str] = None,
) -> Optional[str]:
    pref = (preferred or "").strip()
    for row in catalog.google_sheet_rows:
        sn = (row.spreadsheet_name or "").strip()
        if sn.lower() != display_name.strip().lower():
            continue
        tab = (row.sheet_name or "").strip() or "Sheet1"
        if pref and tab.lower() == pref.lower():
            return tab
    for row in catalog.google_sheet_rows:
        sn = (row.spreadsheet_name or "").strip()
        if sn.lower() == display_name.strip().lower():
            return (row.sheet_name or "").strip() or "Sheet1"
    return pref or None


def _source_entry_key(entry: Dict[str, Any]) -> Tuple[str, str]:
    return (
        (entry.get("connector_type") or "").strip().lower(),
        (entry.get("display_name") or "").strip().lower(),
    )


def _validate_llm_extract_sources(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> List[Dict[str, Any]]:
    """
    Normalize LLM extract_sources against the user's catalog.
    Returns deduped list of valid extract sources (may include primary).
    """
    raw = classification.get("extract_sources")
    if not isinstance(raw, list) or not raw:
        return []

    primary_type = (classification.get("source") or "").strip().lower()
    primary_name = (classification.get("source_name") or "").strip().lower()
    dest_name = (classification.get("destination_name") or "").strip().lower()
    validated: List[Dict[str, Any]] = []
    seen: set = set()

    for item in raw:
        if not isinstance(item, dict):
            continue
        resolved = _resolve_catalog_source(
            item.get("connector_type"),
            str(item.get("display_name") or ""),
            catalog,
        )
        if not resolved:
            continue

        ct = resolved["connector_type"]
        dn = resolved["display_name"]
        key = _source_entry_key(resolved)
        if key in seen:
            continue
        if dest_name and dn.strip().lower() == dest_name:
            continue

        entry: Dict[str, Any] = dict(resolved)
        sheet_hint = item.get("sheet_name")
        if ct == "google_sheets":
            tab = _sheet_name_for_workbook(dn, catalog, preferred=sheet_hint)
            if tab:
                entry["sheet_name"] = tab
        seen.add(key)
        validated.append(entry)

    if not validated and primary_type and primary_name:
        primary_resolved = _resolve_catalog_source(primary_type, primary_name, catalog)
        if primary_resolved:
            validated.append(primary_resolved)

    return validated[:5]


def _extras_from_llm_classification(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> Optional[List[Dict[str, Any]]]:
    """
    Return additional extract sources from PLAN_SCOPE fields on classification.
    None = scope did not signal multi-source.
    """
    multi_flag = classification.get("multi_source_extract")
    raw = classification.get("extract_sources")

    if multi_flag is None and raw is None:
        return None
    if multi_flag is False and (not isinstance(raw, list) or len(raw) < 2):
        return None

    validated = _validate_llm_extract_sources(classification, prompt, catalog)
    primary_type = (classification.get("source") or "").strip().lower()
    primary_name = (classification.get("source_name") or "").strip().lower()
    primary_key = (primary_type, primary_name)

    extras: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in validated:
        key = _source_entry_key(entry)
        if key == primary_key:
            continue
        if key in seen:
            continue
        seen.add(key)
        extras.append(entry)

    distinct_sources = len({ _source_entry_key(e) for e in validated })
    if distinct_sources >= 2 and extras:
        return extras[:4]
    if multi_flag is True and extras:
        return extras[:4]
    if multi_flag is True:
        return []
    if distinct_sources >= 2 and extras:
        return extras[:4]
    return None


def detect_additional_sources(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> List[Dict[str, Any]]:
    """Multi-source detection from PLAN_SCOPE LLM fields only."""
    extras = _extras_from_llm_classification(classification, prompt, catalog)
    return list(extras or [])


def apply_multi_source_hints(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    out = dict(classification or {})
    extras = detect_additional_sources(out, prompt, catalog)
    if extras:
        out["planner_additional_sources"] = extras
        out["multi_source_pipeline"] = True
        if "extract" not in (out.get("operations") or []):
            ops = list(out.get("operations") or [])
            if "extract" not in ops:
                ops.insert(0, "extract")
            out["operations"] = ops
    out.pop("multi_source_extract", None)
    out.pop("extract_sources", None)
    return out

# --- Multi-source combine (merged P4.9) ---

from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from services.extraction_results_service import ExtractionResultsService
from services.google_sheets.join_ranges import join_records


def _resolve_join_key(
    *,
    join_key: Optional[str],
    left_headers: List[str],
    right_headers: List[str],
) -> Tuple[str, str]:
    """Use classifier/PLAN_SCOPE join_key only — no prompt heuristics."""
    key = (join_key or "").strip()
    if not key:
        raise ValueError(
            "Join key is required. Classifier must set join_key (e.g. combine on Email)."
        )
    if key not in left_headers:
        raise ValueError(f"Join key column '{key}' not found in left source headers")
    if key not in right_headers:
        # Allow case-insensitive header match
        right_match = next((h for h in right_headers if h.lower() == key.lower()), None)
        if not right_match:
            raise ValueError(f"Join key column '{key}' not found in right source headers")
        key = right_match
    left_match = next((h for h in left_headers if h.lower() == key.lower()), key)
    return left_match, key


async def load_job_records(
    user_id: str,
    job_id: str,
) -> Tuple[List[str], List[Dict[str, Any]], Dict[str, Any]]:
    row = await ExtractionResultsService.get_extraction_result(job_id, user_id)
    if not row:
        raise ValueError(f"Extraction job {job_id} not found")
    data = row.get("extracted_data") or []
    if not data:
        raise ValueError(f"No records on extraction job {job_id}")
    headers = sorted({k for r in data[:50] if isinstance(r, dict) for k in r.keys()})
    return headers, data, row


async def combine_extraction_jobs(
    user_id: str,
    job_ids: List[str],
    *,
    join_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Left-join job_ids in order (first job is left, each next job is right)."""
    if len(job_ids) < 2:
        raise ValueError("Need at least two extraction jobs to combine")

    left_headers, left_rows, left_meta = await load_job_records(user_id, job_ids[0])
    combined_headers = list(left_headers)
    combined_rows = list(left_rows)
    sources_meta: List[Dict[str, Any]] = [
        {
            "job_id": job_ids[0],
            "source_type": left_meta.get("source_type"),
            "source_name": left_meta.get("source_name"),
            "record_count": len(left_rows),
        }
    ]

    for job_id in job_ids[1:]:
        right_headers, right_rows, right_meta = await load_job_records(user_id, job_id)
        lk, rk = _resolve_join_key(
            join_key=join_key,
            left_headers=combined_headers,
            right_headers=right_headers,
        )
        combined_headers, combined_rows = join_records(
            combined_headers,
            combined_rows,
            right_headers,
            right_rows,
            lk,
            rk,
            how="left",
        )
        sources_meta.append(
            {
                "job_id": job_id,
                "source_type": right_meta.get("source_type"),
                "source_name": right_meta.get("source_name"),
                "record_count": len(right_rows),
                "join_key": lk,
            }
        )

    return {
        "headers": combined_headers,
        "rows": combined_rows,
        "joined_count": len(combined_rows),
        "sources": sources_meta,
        "join_key": sources_meta[-1].get("join_key") if len(sources_meta) > 1 else None,
    }


async def stream_combine_multi_source_extractions(
    *,
    user_id: str,
    conversation_id: Optional[str],
    prompt: str,
    classification: Dict[str, Any],
    job_ids: List[str],
) -> AsyncGenerator[str, None]:
    """Merge prior extraction jobs and persist as a new extraction for map/migrate."""
    from services.google_sheets.context_resolver import resolve_spreadsheet_context
    from services.google_sheets.extraction_persist import persist_gsheets_operation_as_extraction
    from services.google_sheets.response_summary import build_error_response

    try:
        join_key = (classification.get("join_key") or "").strip() or None
        result = await combine_extraction_jobs(user_id, job_ids, join_key=join_key)
    except ValueError as e:
        for chunk in build_error_response("Combine failed", str(e)):
            yield chunk
        return

    rows = result.get("rows") or []
    headers = result.get("headers") or []
    sources = result.get("sources") or []
    join_key = result.get("join_key") or "key"

    source_labels = " + ".join(
        f"**{s.get('source_name') or s.get('source_type')}** ({s.get('record_count', 0)} rows)"
        for s in sources
    )
    dest_name = (classification.get("destination_name") or "").strip()
    dest_line = f"\n**Target:** {dest_name}" if dest_name else ""

    chunks = [
        f"**✅ Combined {len(sources)} sources** ({result.get('joined_count', len(rows))} row(s))\n\n",
        f"**Sources:** {source_labels}\n",
        f"**Join key:** {join_key}\n",
    ]
    if dest_line:
        chunks.append(dest_line + "\n")
    chunks.append(
        "\n_Next: mapping and export to your destination will run if included in your plan._\n"
    )

    dest_ctx = None
    if dest_name:
        dest_ctx = await resolve_spreadsheet_context(
            prompt,
            user_id,
            None,
            destination_name=dest_name,
            destination_sheet_name=classification.get("destination_sheet_name"),
        )

    ctx = dest_ctx
    if not ctx or not ctx.found:
        ctx = await resolve_spreadsheet_context(
            prompt,
            user_id,
            None,
            source_name=sources[0].get("source_name") if sources else None,
        )

    if not ctx or not ctx.found:
        for chunk in chunks:
            yield chunk
        return

    _, finished_sse = await persist_gsheets_operation_as_extraction(
        user_id=user_id,
        conversation_id=conversation_id,
        classification=classification,
        ctx=ctx,
        rows=rows,
        extraction_goals=prompt,
        document_type="Combined Data",
        document_description=f"Multi-source combine ({len(sources)} extractions) on {join_key}",
        extraction_metadata={
            "operation": "multi_source_combine",
            "multi_source_job_ids": job_ids,
            "join_key": join_key,
            "sources": sources,
            "destination_name": dest_name or None,
        },
        operation_summary="".join(chunks),
    )
    for chunk in chunks:
        yield chunk
    yield finished_sse

