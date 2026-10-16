from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from post_extract.debug import log_snapshot, post_extract_debug_enabled
from post_extract.plugins import OperationRegistry, default_registry
from post_extract.session import OperationPlan, OperationStep, PostExtractSession
from utils.post_extract_pipeline_llm_summary import build_pipeline_narrative
from utils.post_extract_summaries import summarize_operation_delta

logger = logging.getLogger(__name__)


def _dedupe_operation_steps(steps: List[OperationStep]) -> List[OperationStep]:
    """Keep first occurrence of each operation_id (planner order preserved)."""
    seen: set[str] = set()
    out: List[OperationStep] = []
    for s in steps:
        k = (s.operation_id or "").strip().lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def _derive_pipeline_transform_before_filter(steps: List[OperationStep]) -> bool:
    """
    Default True (transform then filter). False when filter is scheduled before transform.

    Dedupe/validate steps do not participate; only relative positions of filter vs transform matter.
    """
    ids: List[str] = []
    for s in steps:
        k = (s.operation_id or "").strip().lower()
        if k == "transformation":
            k = "transform"
        ids.append(k)
    try:
        i_t = ids.index("transform")
    except ValueError:
        i_t = None
    try:
        i_f = ids.index("filter")
    except ValueError:
        i_f = None
    if i_t is None or i_f is None:
        return True
    return i_t < i_f


_OPERATION_STEP_LABELS = {
    "transform": "Transform columns",
    "transformation": "Transform columns",
    "filter": "Filter rows",
    "dedupe": "Remove duplicates",
    "deduplication": "Remove duplicates",
    "validate": "Validate & clean values",
    "validation": "Validate & clean values",
    "cleanup_values": "Clean values",
}


def _operation_step_label(op_id: str) -> str:
    key = (op_id or "").lower()
    if key in _OPERATION_STEP_LABELS:
        return _OPERATION_STEP_LABELS[key]
    slug = (op_id or "").replace("_", " ").strip()
    return slug.title() if slug else "Operation"


class PostExtractOrchestrator:
    def __init__(self, registry: Optional[OperationRegistry] = None) -> None:
        self.registry = registry or default_registry()

    async def run(
        self,
        plan: OperationPlan,
        user_id: str,
        *,
        conversation_id: Optional[str] = None,
        extraction_rules_override: Optional[Dict[str, Any]] = None,
        data_quality_report: Optional[Dict[str, Any]] = None,
        on_operation_event: Optional[
            Callable[[str, str, Dict[str, Any]], Awaitable[None]]
        ] = None,
    ) -> Dict[str, Any]:
        session = await PostExtractSession.load(
            plan.parent_job_id,
            user_id,
            version=plan.source_version,
            conversation_id=conversation_id,
            extraction_rules_override=extraction_rules_override,
        )

        steps = _dedupe_operation_steps(list(plan.steps))
        session.pipeline_transform_before_filter = _derive_pipeline_transform_before_filter(steps)
        step_summaries: List[str] = []
        for step in steps:
            if on_operation_event:
                await on_operation_event(
                    "started",
                    step.operation_id,
                    {"operation_id": step.operation_id},
                )
            plugin = self.registry.get(step.operation_id)
            if data_quality_report and "data_quality_report" not in step.params:
                step.params = {**step.params, "data_quality_report": data_quality_report}
            delta = await plugin.propose_rules(session, step)
            if post_extract_debug_enabled():
                log_snapshot(
                    f"orchestrator.propose_rules operation={step.operation_id} session_job={session.job_id}",
                    {
                        "operation_id": step.operation_id,
                        "prompt_excerpt": (step.prompt or "")[:800],
                        "delta": delta if delta else {},
                        "delta_empty": not delta,
                        "has_data_quality_in_step": bool(step.params.get("data_quality_report")),
                    },
                )
            if delta:
                session.merge_rules(delta, step.operation_id)
            try:
                from services.pipeline_learning.service import PipelineLearningService

                if PipelineLearningService.enabled():
                    PipelineLearningService.schedule_capture(
                        lambda: PipelineLearningService.capture_data_ops(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            job_id=session.job_id,
                            parent_job_id=plan.parent_job_id,
                            operation_id=step.operation_id,
                            proposed_delta=delta,
                            rules_after_merge=session.extraction_rules,
                            operations_applied=list(session.operations_applied),
                            source_type=session.source_type,
                        )
                    )
            except Exception:
                pass
            if post_extract_debug_enabled():
                log_snapshot(
                    f"orchestrator.rules_after_merge operation={step.operation_id}",
                    {
                        "extraction_rules": session.extraction_rules,
                        "source_schema_len": len(session.source_schema or []),
                    },
                )
            session.operations_applied.append(step.operation_id)
            logger.info(
                "[POST_EXTRACT] merged operation=%s job_id=%s",
                step.operation_id,
                session.job_id,
            )
            summary = summarize_operation_delta(step.operation_id, delta or {})
            step_summaries.append(summary)
            if on_operation_event:
                await on_operation_event(
                    "completed",
                    step.operation_id,
                    {
                        "operation_id": step.operation_id,
                        "rules_updated": bool(delta),
                        "summary": summary,
                    },
                )

        combined_prompt = " ".join((step.prompt or "") for step in steps).strip()
        from utils.extraction_rules_validate import (
            prompt_requests_row_cap,
            strip_ordering_row_limit,
        )

        if not prompt_requests_row_cap(combined_prompt):
            strip_ordering_row_limit(session.extraction_rules)

        if post_extract_debug_enabled():
            log_snapshot(
                "orchestrator.before_apply",
                {
                    "record_count": len(session.records),
                    "schema_sample": (session.source_schema or [])[:60],
                    "extraction_rules": session.extraction_rules,
                    "operations_applied_order": list(session.operations_applied),
                    "pipeline_transform_before_filter": session.pipeline_transform_before_filter,
                },
            )
        row_count_before = len(session.records)
        session.apply()
        row_count_after = len(session.records)
        if post_extract_debug_enabled():
            log_snapshot(
                "orchestrator.after_apply",
                {"record_count": row_count_after},
            )
        save_meta = await session.save()
        try:
            from services.pipeline_learning.service import PipelineLearningService

            if PipelineLearningService.enabled():
                PipelineLearningService.schedule_capture(
                    lambda: PipelineLearningService.capture_data_ops_run_complete(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        job_id=save_meta.get("job_id") or session.job_id,
                        operations_applied=list(session.operations_applied),
                        extraction_rules=session.extraction_rules,
                        row_count_before=row_count_before,
                        row_count_after=row_count_after,
                        source_type=session.source_type,
                    )
                )
        except Exception:
            pass
        logger.info(
            "[POST_EXTRACT] saved new_job_id=%s version=%s ops=%s",
            save_meta.get("job_id"),
            save_meta.get("extraction_version"),
            save_meta.get("operations_applied"),
        )
        user_prompt = ""
        for st in steps:
            if (st.prompt or "").strip():
                user_prompt = (st.prompt or "").strip()
                break
        pipeline_narrative = await build_pipeline_narrative(
            user_prompt=user_prompt,
            operations_applied=list(session.operations_applied),
            row_count_before=row_count_before,
            row_count_after=row_count_after,
            extraction_rules=session.extraction_rules,
            source_schema=session.source_schema,
            step_summaries=step_summaries,
        )
        pipeline_steps = [
            {
                "tool_id": step.operation_id or "transform",
                "label": _operation_step_label(step.operation_id or ""),
                "detail": summary or None,
                "status": "done",
            }
            for step, summary in zip(steps, step_summaries)
        ]
        return {
            **save_meta,
            "extraction_rules": session.extraction_rules,
            "source_schema": session.source_schema,
            "source_type": session.source_type,
            "source_name": session.source_name,
            "destination": None,
            "pipeline_narrative": pipeline_narrative,
            "pipeline_steps": pipeline_steps,
        }
