"""Stream row chunks into merged quality profiles."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from services.data_assessment.models import ObjectProfile
from services.data_assessment.scoring import score_entity_profile
from utils.data_quality_analyzer import analyze_data_quality, merge_quality_profiles


def profile_rows_streaming(
    entity_id: str,
    label: str,
    row_chunks: List[List[Dict[str, Any]]],
) -> ObjectProfile:
    merged: Dict[str, Any] | None = None
    total = 0
    for chunk in row_chunks:
        if not chunk:
            continue
        part = analyze_data_quality(chunk, omit_record_indices=True)
        merged = merge_quality_profiles(merged, part) if merged else part
        total += len(chunk)

    if merged is None:
        merged = analyze_data_quality([], omit_record_indices=True)
    merged["total_records"] = total
    dims = score_entity_profile(merged, total)
    issues = (merged.get("summary") or {}).get("recommendations") or []
    return ObjectProfile(
        entity_id=entity_id,
        label=label,
        records_sampled=total,
        quality_report=merged,
        dimension_scores=dims,
        issues_summary=list(issues)[:8],
    )


def profile_rows_single(entity_id: str, label: str, rows: List[Dict[str, Any]]) -> ObjectProfile:
    if not rows:
        qr = analyze_data_quality([], omit_record_indices=True)
        return ObjectProfile(
            entity_id=entity_id,
            label=label,
            records_sampled=0,
            quality_report=qr,
            dimension_scores=score_entity_profile(qr, 0),
            issues_summary=["No records sampled"],
        )
    return profile_rows_streaming(entity_id, label, [rows])


async def profile_rows_streaming_async(
    entity_id: str,
    label: str,
    row_chunks: List[List[Dict[str, Any]]],
) -> ObjectProfile:
    """CPU-bound profiling off the asyncio event loop."""
    return await asyncio.to_thread(profile_rows_streaming, entity_id, label, row_chunks)


async def profile_rows_single_async(
    entity_id: str,
    label: str,
    rows: List[Dict[str, Any]],
) -> ObjectProfile:
    return await asyncio.to_thread(profile_rows_single, entity_id, label, rows)
