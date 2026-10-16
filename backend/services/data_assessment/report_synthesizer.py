"""LLM narrative for assessment reports (structured input only)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.data_quality_analyzer import format_quality_report_as_text

logger = logging.getLogger(__name__)

MAX_BY_ENTITY = 50
TOP_MISSING_FIELDS = 15


async def synthesize_report_sections(
    *,
    connector_type: str,
    scores: Dict[str, Any],
    entity_profiles: List[Dict[str, Any]],
    gap_findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return summary, risks, data_quality, data_gaps, scoring sections."""
    scoring_section = {
        "overall": scores.get("overall"),
        "dimensions": scores.get("dimensions"),
        "entities_assessed": scores.get("entities_assessed"),
    }
    data_quality = await asyncio.to_thread(_build_data_quality_section, entity_profiles)
    data_gaps = _build_data_gaps_section(gap_findings)

    summary, risks = await _llm_narrative(
        connector_type, scores, entity_profiles, gap_findings, data_quality
    )
    if not summary:
        summary = _fallback_summary(scores, entity_profiles, data_quality)
    if not risks:
        risks = _fallback_risks(scores, gap_findings, data_quality)

    return {
        "summary": summary,
        "risks": risks,
        "data_quality": data_quality,
        "data_gaps": data_gaps,
        "scoring": scoring_section,
    }


def _entity_display_name(ep: Dict[str, Any]) -> str:
    label = (ep.get("label") or "").strip()
    return label or ep.get("entity_id") or "Unknown"


def _dup_count(qr: Dict[str, Any], key: str) -> int:
    block = (qr.get("duplicates") or {}).get(key) or {}
    return int(block.get("count") or 0) if isinstance(block, dict) else 0


def _dup_fields_checked(qr: Dict[str, Any], key: str) -> List[str]:
    block = (qr.get("duplicates") or {}).get(key) or {}
    if not isinstance(block, dict):
        return []
    raw = block.get("fields_checked") or []
    return [str(f) for f in raw if f]


def _format_issue_count(qr: Dict[str, Any]) -> int:
    fmt = qr.get("format_issues") or {}
    total = 0
    for v in fmt.values():
        if isinstance(v, list):
            total += len(v)
    return total


def _entity_issue_count(ep: Dict[str, Any]) -> int:
    if ep.get("error"):
        return 1000
    qr = ep.get("quality_report") or {}
    missing = int((qr.get("missing_values") or {}).get("total_missing") or 0)
    dups = _dup_count(qr, "exact_duplicates") + _dup_count(qr, "key_field_duplicates")
    fmt = _format_issue_count(qr)
    inc = len(qr.get("inconsistencies") or {})
    return missing + dups + fmt + inc


def _truncate_missing_by_field(by_field: Dict[str, Any]) -> Dict[str, int]:
    if not by_field:
        return {}
    items = sorted(by_field.items(), key=lambda x: int(x[1]), reverse=True)
    return {str(k): int(v) for k, v in items[:TOP_MISSING_FIELDS]}


def _build_entity_quality_row(ep: Dict[str, Any]) -> Dict[str, Any]:
    display = _entity_display_name(ep)
    eid = ep.get("entity_id") or ""
    if ep.get("error"):
        return {
            "entity_id": eid,
            "label": display,
            "records_sampled": int(ep.get("records_sampled") or 0),
            "score": 0,
            "error": ep.get("error"),
            "issue_count": _entity_issue_count(ep),
        }

    qr = ep.get("quality_report") or {}
    summary = qr.get("summary") or {}
    score = summary.get("data_quality_score")
    missing = qr.get("missing_values") or {}
    by_field = missing.get("by_field") or {}

    row: Dict[str, Any] = {
        "entity_id": eid,
        "label": display,
        "records_sampled": int(ep.get("records_sampled") or 0),
        "score": score,
        "duplicates": {
            "exact_duplicates": _dup_count(qr, "exact_duplicates"),
            "key_field_duplicates": _dup_count(qr, "key_field_duplicates"),
            "key_fields_checked": _dup_fields_checked(qr, "key_field_duplicates"),
        },
        "missing_values": {
            "total_missing": int(missing.get("total_missing") or 0),
            "by_field": _truncate_missing_by_field(by_field),
            "percentages": {
                str(k): v
                for k, v in list((missing.get("percentages") or {}).items())[:TOP_MISSING_FIELDS]
            },
        },
        "format_issue_count": _format_issue_count(qr),
        "format_issues": {
            k: v[:5] if isinstance(v, list) else v
            for k, v in list((qr.get("format_issues") or {}).items())[:10]
        },
        "inconsistency_count": len(qr.get("inconsistencies") or {}),
        "recommendations": (summary.get("recommendations") or ep.get("issues_summary") or [])[:5],
        "issue_count": _entity_issue_count(ep),
    }
    try:
        text = format_quality_report_as_text(qr)
        if text and len(text) > 4000:
            text = text[:4000] + "\n…"
        row["summary_text"] = text
    except Exception:
        row["summary_text"] = ""
    return row


def _build_data_quality_section(entity_profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = {
        "exact_duplicates": 0,
        "key_field_duplicates": 0,
        "key_fields_checked": [],
        "total_missing": 0,
        "format_issue_count": 0,
        "entities_with_issues": 0,
    }
    key_fields_union: set[str] = set()
    highlights: List[Dict[str, Any]] = []
    entity_rows: List[Dict[str, Any]] = []

    for ep in entity_profiles:
        row = _build_entity_quality_row(ep)
        entity_rows.append(row)
        icount = row.get("issue_count") or 0
        if icount > 0 or row.get("error"):
            totals["entities_with_issues"] += 1
        if ep.get("error"):
            highlights.append(
                {
                    "entity_id": row["entity_id"],
                    "label": row["label"],
                    "issue": row["error"],
                    "severity": "high",
                }
            )
            continue
        qr = ep.get("quality_report") or {}
        totals["exact_duplicates"] += _dup_count(qr, "exact_duplicates")
        totals["key_field_duplicates"] += _dup_count(qr, "key_field_duplicates")
        for field in _dup_fields_checked(qr, "key_field_duplicates"):
            key_fields_union.add(field)
        totals["total_missing"] += int((qr.get("missing_values") or {}).get("total_missing") or 0)
        totals["format_issue_count"] += _format_issue_count(qr)
        score = row.get("score")
        if score is not None and float(score) < 80:
            highlights.append(
                {
                    "entity_id": row["entity_id"],
                    "label": row["label"],
                    "score": score,
                    "recommendations": row.get("recommendations") or [],
                    "severity": "medium" if float(score) >= 60 else "high",
                }
            )

    entity_rows.sort(key=lambda r: r.get("issue_count") or 0, reverse=True)
    totals["key_fields_checked"] = sorted(key_fields_union)
    by_entity = []
    for row in entity_rows[:MAX_BY_ENTITY]:
        slim = {k: v for k, v in row.items() if k != "issue_count"}
        by_entity.append(slim)

    return {
        "connector_totals": totals,
        "by_entity": by_entity,
        "highlights": highlights[:20],
        "entities": len(entity_profiles),
    }


def _build_data_gaps_section(gap_findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_category: Dict[str, int] = {}
    for g in gap_findings:
        cat = (g.get("category") or "other").strip() or "other"
        by_category[cat] = by_category.get(cat, 0) + 1
    return {
        "findings": gap_findings,
        "count": len(gap_findings),
        "by_category": by_category,
    }


def _fallback_summary(
    scores: Dict[str, Any],
    entities: List[Dict[str, Any]],
    data_quality: Dict[str, Any],
) -> str:
    overall = scores.get("overall", 0)
    n = scores.get("entities_assessed", len(entities))
    t = data_quality.get("connector_totals") or {}
    parts = [
        f"Assessment completed across {n} entities with an overall readiness score of {overall}/100.",
    ]
    if t.get("exact_duplicates") or t.get("key_field_duplicates"):
        parts.append(
            f"Found {t.get('exact_duplicates', 0)} exact duplicate rows and "
            f"{t.get('key_field_duplicates', 0)} key-field duplicate rows across samples."
        )
    if t.get("total_missing"):
        parts.append(f"Detected {t['total_missing']} missing values in sampled data.")
    parts.append("Review per-entity data quality and data gaps for remediation priorities.")
    return " ".join(parts)


def _fallback_risks(
    scores: Dict[str, Any],
    gaps: List[Dict[str, Any]],
    data_quality: Dict[str, Any],
) -> List[str]:
    risks = []
    if float(scores.get("overall") or 0) < 70:
        risks.append(
            "Overall readiness is below recommended threshold for migration or warehouse load."
        )
    t = data_quality.get("connector_totals") or {}
    if int(t.get("exact_duplicates") or 0) > 0:
        risks.append(
            f"Exact duplicate records ({t['exact_duplicates']}) may inflate metrics and break idempotent loads."
        )
    if int(t.get("total_missing") or 0) > 0:
        risks.append(
            f"Widespread missing values ({t['total_missing']} in sample) can block reporting and automation."
        )
    for g in gaps[:8]:
        msg = g.get("message", "")
        if msg:
            risks.append(msg)
    return [r for r in risks if r]


def _narrative_payload(
    connector_type: str,
    scores: Dict[str, Any],
    entity_profiles: List[Dict[str, Any]],
    gap_findings: List[Dict[str, Any]],
    data_quality: Dict[str, Any],
) -> Dict[str, Any]:
    compact_entities = []
    for ep in entity_profiles[:25]:
        qr = ep.get("quality_report") or {}
        compact_entities.append(
            {
                "entity_id": ep.get("entity_id"),
                "label": _entity_display_name(ep),
                "records_sampled": ep.get("records_sampled"),
                "score": (qr.get("summary") or {}).get("data_quality_score"),
                "exact_duplicates": _dup_count(qr, "exact_duplicates"),
                "key_field_duplicates": _dup_count(qr, "key_field_duplicates"),
                "total_missing": int((qr.get("missing_values") or {}).get("total_missing") or 0),
                "top_issues": (ep.get("issues_summary") or [])[:3],
                "error": ep.get("error"),
            }
        )
    return {
        "connector_type": connector_type,
        "scores": scores,
        "connector_quality_totals": data_quality.get("connector_totals"),
        "entities": compact_entities,
        "top_entities_by_issues": (data_quality.get("by_entity") or [])[:10],
        "gaps": gap_findings[:15],
    }


async def _llm_narrative(
    connector_type: str,
    scores: Dict[str, Any],
    entity_profiles: List[Dict[str, Any]],
    gap_findings: List[Dict[str, Any]],
    data_quality: Dict[str, Any],
) -> tuple:
    from services.llm import tasks as llm_tasks
    from services.llm.service import llm_complete, task_has_api_key
    from services.llm.types import LLMRequest

    if not await task_has_api_key(llm_tasks.ASSESSMENT_NARRATIVE):
        return None, None

    try:
        payload = _narrative_payload(
            connector_type, scores, entity_profiles, gap_findings, data_quality
        )
        resp = await llm_complete(
            LLMRequest(
                task_key=llm_tasks.ASSESSMENT_NARRATIVE,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You write data readiness assessment reports for a single connector. "
                            'Return JSON only: {"summary": "...", "risks": ["..."]}. '
                            "Reference duplicate counts, missing values, and gap findings when provided. "
                            "Do not invent statistics; use only provided metrics."
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, default=str)},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        )
        data = json.loads(resp.content or "{}")
        return data.get("summary"), data.get("risks")
    except Exception as exc:
        logger.warning("Assessment LLM synthesizer failed: %s", exc)
        return None, None
