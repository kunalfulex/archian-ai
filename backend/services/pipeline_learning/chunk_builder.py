from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


def source_fingerprint_from_schema(schema: Optional[List[str]]) -> str:
    cols = sorted({str(c).strip().lower() for c in (schema or []) if c})
    raw = "|".join(cols)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def source_fingerprint_from_record(record: Optional[Dict[str, Any]]) -> str:
    if not record or not isinstance(record, dict):
        return ""
    keys = [k for k in record.keys() if not k.startswith("_")]
    return source_fingerprint_from_schema(keys)


def build_user_intent_text(
    *,
    prompt: str = "",
    mapping_context_summary: Optional[str] = None,
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
) -> str:
    parts = [
        f"source:{source_type or 'unknown'}",
        f"destination:{destination_type or 'unknown'}",
    ]
    if prompt.strip():
        parts.append(f"prompt:{prompt.strip()[:2000]}")
    if mapping_context_summary and mapping_context_summary.strip():
        parts.append(f"mapping_intent:{mapping_context_summary.strip()[:1500]}")
    return "\n".join(parts)


def build_extraction_rules_text(rules: Optional[Dict[str, Any]]) -> str:
    if not rules:
        return ""
    safe = {k: v for k, v in rules.items() if k not in ("extracted_data", "records")}
    return json.dumps(safe, sort_keys=True, default=str)[:6000]


def build_data_ops_recipe_text(
    *,
    operations_applied: List[str],
    extraction_rules: Optional[Dict[str, Any]] = None,
    row_count_before: Optional[int] = None,
    row_count_after: Optional[int] = None,
) -> str:
    parts = [f"ops:{','.join(operations_applied)}"]
    if row_count_before is not None and row_count_after is not None:
        parts.append(f"rows:{row_count_before}->{row_count_after}")
    rules_txt = build_extraction_rules_text(extraction_rules)
    if rules_txt:
        parts.append(rules_txt[:4000])
    return "\n".join(parts)


def build_tool_trace_text(
    *,
    prompt: str = "",
    steps: List[Dict[str, Any]],
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    mode: Optional[str] = None,
    partial: bool = False,
    job_id: Optional[str] = None,
) -> str:
    """Embed-friendly summary of an ordered tool execution trace."""
    parts: List[str] = [
        f"trace_type:{'tool_trace_partial' if partial else 'tool_trace'}",
        f"source:{source_type or 'unknown'}",
        f"destination:{destination_type or 'unknown'}",
    ]
    if mode:
        parts.append(f"mode:{mode}")
    if job_id:
        parts.append(f"job_id:{job_id}")
    if prompt.strip():
        parts.append(f"goal:{prompt.strip()[:1500]}")
    tool_ids = [str(s.get("tool_id") or "") for s in steps if s.get("tool_id")]
    if tool_ids:
        parts.append(f"tools:{' -> '.join(tool_ids)}")
    for i, step in enumerate(steps[:12], 1):
        tid = step.get("tool_id") or "unknown"
        ok = step.get("ok")
        status = step.get("status")
        line = f"step{i}:{tid}"
        if ok is not None:
            line += f":ok={bool(ok)}"
        elif status:
            line += f":status={status}"
        rationale = (step.get("rationale") or "").strip()
        if rationale:
            line += f":{rationale[:120]}"
        parts.append(line)
    return "\n".join(parts)


def build_platform_tool_pattern_text(
    *,
    steps: List[Dict[str, Any]],
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    mode: Optional[str] = None,
) -> str:
    """Anonymized cross-user pattern — no goal text, job ids, or user-specific fields."""
    parts: List[str] = [
        "trace_type:platform_tool_pattern",
        f"source:{source_type or 'unknown'}",
        f"destination:{destination_type or 'unknown'}",
    ]
    if mode:
        parts.append(f"mode:{mode}")
    tool_ids = [str(s.get("tool_id") or "") for s in steps if s.get("tool_id")]
    if tool_ids:
        parts.append(f"tools:{' -> '.join(tool_ids)}")
    for i, step in enumerate(steps[:12], 1):
        tid = step.get("tool_id") or "unknown"
        ok = step.get("ok")
        line = f"step{i}:{tid}"
        if ok is not None:
            line += f":ok={bool(ok)}"
        parts.append(line)
    return "\n".join(parts)


def build_tool_pick_text(
    *,
    prompt: str = "",
    pick_source: str = "runtime",
    actual_tool_ids: Optional[List[str]] = None,
    shadow_tool_ids: Optional[List[str]] = None,
    mode: Optional[str] = None,
    first_match: Optional[bool] = None,
) -> str:
    parts: List[str] = [f"pick_source:{pick_source}"]
    if mode:
        parts.append(f"mode:{mode}")
    if prompt.strip():
        parts.append(f"goal:{prompt.strip()[:800]}")
    if actual_tool_ids:
        parts.append(f"actual:{' -> '.join(actual_tool_ids)}")
    if shadow_tool_ids:
        parts.append(f"shadow:{' -> '.join(shadow_tool_ids)}")
    if first_match is not None:
        parts.append(f"first_match:{first_match}")
    return "\n".join(parts)


def build_mapping_template_text(
    *,
    destination_object: Optional[str] = None,
    mapping_config: Optional[Dict[str, Any]] = None,
    mapping_plan: Optional[Dict[str, Any]] = None,
    transformation_rules: Optional[Dict[str, Any]] = None,
) -> str:
    parts: List[str] = []
    if destination_object:
        parts.append(f"destination_object:{destination_object}")
    if mapping_plan:
        parts.append(f"plan:{json.dumps(mapping_plan, sort_keys=True, default=str)[:2000]}")
    if mapping_config:
        parts.append(f"mapping:{json.dumps(mapping_config, sort_keys=True, default=str)[:4000]}")
    if transformation_rules:
        parts.append(
            f"transforms:{json.dumps(transformation_rules, sort_keys=True, default=str)[:2000]}"
        )
    return "\n".join(parts)


def build_plan_snapshot_text(
    *,
    prompt: str = "",
    pipeline_label: Optional[str] = None,
    pipeline_steps: Optional[List[str]] = None,
    step_labels: Optional[List[str]] = None,
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    source_name: Optional[str] = None,
    destination_name: Optional[str] = None,
    plan_status: Optional[str] = None,
    approved: Optional[bool] = None,
    rejected: Optional[bool] = None,
) -> str:
    parts: List[str] = []
    if prompt.strip():
        parts.append(f"prompt:{prompt.strip()[:2000]}")
    if pipeline_label:
        parts.append(f"pipeline_label:{pipeline_label}")
    if pipeline_steps:
        parts.append(f"pipeline_steps:{' → '.join(pipeline_steps[:12])}")
    if source_type or source_name:
        parts.append(f"source:{source_type or 'unknown'}/{source_name or ''}".strip("/"))
    if destination_type or destination_name:
        parts.append(
            f"destination:{destination_type or 'unknown'}/{destination_name or ''}".strip("/")
        )
    if step_labels:
        parts.append(f"steps:{' | '.join(step_labels[:12])}")
    if plan_status:
        parts.append(f"plan_status:{plan_status}")
    if approved is True:
        parts.append("approved:true")
    if rejected is True:
        parts.append("rejected:true")
    return "\n".join(parts)


def build_pipeline_recipe_text(
    *,
    prompt: str = "",
    pipeline_label: Optional[str] = None,
    step_labels: Optional[List[str]] = None,
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    destination_name: Optional[str] = None,
    success_rate: Optional[float] = None,
) -> str:
    parts = [
        build_plan_snapshot_text(
            prompt=prompt,
            pipeline_label=pipeline_label,
            step_labels=step_labels,
            source_type=source_type,
            destination_type=destination_type,
            destination_name=destination_name,
            approved=True,
        )
    ]
    if success_rate is not None:
        parts.append(f"outcome_success_rate:{success_rate:.3f}")
    return "\n".join(p for p in parts if p)


def build_migration_outcome_text(
    *,
    success_rate: float,
    total_records: int,
    failed_fields: Optional[List[str]] = None,
    error_samples: Optional[List[str]] = None,
) -> str:
    parts = [
        f"success_rate:{success_rate:.3f}",
        f"total_records:{total_records}",
    ]
    if failed_fields:
        parts.append(f"failed_fields:{','.join(failed_fields[:30])}")
    if error_samples:
        samples = [e[:200] for e in error_samples[:5]]
        parts.append(f"errors:{json.dumps(samples)}")
    return "\n".join(parts)


def build_tool_pick_text(
    *,
    prompt: str = "",
    pick_source: str = "runtime",
    actual_tool_ids: Optional[List[str]] = None,
    shadow_tool_ids: Optional[List[str]] = None,
    mode: Optional[str] = None,
    first_match: Optional[bool] = None,
) -> str:
    parts: List[str] = [f"pick_source:{pick_source}"]
    if mode:
        parts.append(f"mode:{mode}")
    if prompt.strip():
        parts.append(f"goal:{prompt.strip()[:800]}")
    if actual_tool_ids:
        parts.append(f"actual:{' -> '.join(actual_tool_ids)}")
    if shadow_tool_ids:
        parts.append(f"shadow:{' -> '.join(shadow_tool_ids)}")
    if first_match is not None:
        parts.append(f"first_match:{first_match}")
    return "\n".join(parts)


def chunk_metadata(
    *,
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    document_type: Optional[str] = None,
    destination_object: Optional[str] = None,
    success_rate: Optional[float] = None,
    flow_id: Optional[str] = None,
    source_fingerprint: Optional[str] = None,
    job_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    prior_batch_id: Optional[str] = None,
    outcome: Optional[str] = None,
    requires_write: Optional[bool] = None,
    **extra: Any,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if source_type:
        meta["source_type"] = source_type
    if destination_type:
        meta["destination_type"] = destination_type
    if document_type:
        meta["document_type"] = document_type
    if destination_object:
        meta["destination_object"] = destination_object
    if success_rate is not None:
        meta["success_rate"] = success_rate
    if flow_id:
        meta["flow_id"] = flow_id
    if source_fingerprint:
        meta["source_fingerprint"] = source_fingerprint
    if job_id:
        meta["job_id"] = job_id
    if plan_id:
        meta["plan_id"] = plan_id
    if batch_id:
        meta["batch_id"] = batch_id
    if prior_batch_id:
        meta["prior_batch_id"] = prior_batch_id
    if outcome:
        meta["outcome"] = outcome
    if requires_write is not None:
        meta["requires_write"] = requires_write
    for key, val in extra.items():
        if val is not None and key not in meta:
            meta[key] = val
    return meta
