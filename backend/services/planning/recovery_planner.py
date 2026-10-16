"""LLM-first recovery execution plan builder (map → migrate remediation)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agent.observation import ExecutionObservation, FailureReport

logger = logging.getLogger(__name__)


class ErrorCluster(BaseModel):
    theme: str
    count: int = 1
    sample_message: str = ""
    fields: List[str] = Field(default_factory=list)
    resolvable: bool = False
    category: Optional[str] = None


class RecoveryPlanStep(BaseModel):
    step_type: str
    label: str
    tool_id: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class RecoveryExecutionPlanDoc(BaseModel):
    what_went_wrong: str = ""
    error_clusters: List[ErrorCluster] = Field(default_factory=list)
    relationship_notice: Optional[str] = None
    steps: List[RecoveryPlanStep] = Field(default_factory=list)
    mapping_context_summary: Optional[str] = None
    ask_user: bool = False
    rationale: str = ""
    confidence: str = "medium"


def _cluster_errors(report: FailureReport) -> List[ErrorCluster]:
    buckets: Dict[str, ErrorCluster] = {}
    for err in report.top_errors or []:
        msg = (err.message or "").strip()
        key = msg[:80].lower() if msg else "unknown"
        if key not in buckets:
            buckets[key] = ErrorCluster(
                theme=_theme_from_message(msg),
                count=int(err.count or 1),
                sample_message=msg[:240],
                fields=list(report.failed_fields or [])[:6],
            )
        else:
            buckets[key].count += int(err.count or 1)
    if not buckets and report.failed_fields:
        buckets["field_errors"] = ErrorCluster(
            theme="Field mapping errors",
            count=report.failed_count or len(report.failed_fields),
            sample_message=f"Failures tied to: {', '.join(report.failed_fields[:5])}",
            fields=list(report.failed_fields[:8]),
        )
    return list(buckets.values())


def _theme_from_message(message: str) -> str:
    msg = (message or "").lower()
    if "required" in msg or "missing" in msg:
        return "Missing required values"
    if "lookup" in msg or "foreign key" in msg or "account" in msg:
        return "Lookup / relationship resolution"
    if "duplicate" in msg:
        return "Duplicate records"
    if "invalid" in msg or "bad value" in msg:
        return "Invalid field values"
    if "permission" in msg or "access" in msg:
        return "Permission or access"
    return "Migration errors"


def _relationship_notice_from_session(session) -> Optional[str]:
    snap = getattr(session, "classification_snapshot", None) or {}
    contract = snap.get("plan_contract") or {}
    intent = contract.get("migration_intent") if isinstance(contract, dict) else None
    if not isinstance(intent, dict):
        intent = snap.get("migration_intent") if isinstance(snap.get("migration_intent"), dict) else {}
    if not intent:
        return None
    if intent.get("include_related_data") and intent.get("parent_entity_key"):
        child = intent.get("driving_entity_label") or intent.get("driving_entity_key") or "child records"
        parent = intent.get("parent_entity_label") or intent.get("parent_entity_key")
        return (
            f"This migration used related {parent} fields on {child}. "
            "Lookup failures may mean parent records were not resolved in the destination org."
        )
    return None


def _default_what_went_wrong(report: FailureReport) -> str:
    ok = report.successful_records
    total = report.total_records
    fail = report.failed_count
    rate = int(report.success_rate * 100)
    base = f"{fail} of {total} records failed ({rate}% success)."
    if report.failed_fields:
        base += f" Fields involved: {', '.join(report.failed_fields[:6])}."
    elif report.top_errors:
        base += f" Top error: {report.top_errors[0].message[:160]}"
    return base


def _clusters_from_classification(classification) -> List[ErrorCluster]:
    clusters: List[ErrorCluster] = []
    for item in classification.clusters or []:
        if not isinstance(item, dict):
            continue
        clusters.append(
            ErrorCluster(
                theme=str(item.get("theme") or "Errors"),
                count=int(item.get("count") or 1),
                sample_message=str(item.get("sample_message") or "")[:240],
                fields=[str(f) for f in (item.get("fields") or []) if f],
                resolvable=bool(item.get("resolvable")),
                category=item.get("category"),
            )
        )
    return clusters


def _what_went_wrong_from_classification(classification, report: FailureReport) -> str:
    ok = report.successful_records
    total = report.total_records
    fail = report.failed_count
    rate = int(report.success_rate * 100)
    parts = [f"{fail} of {total} records failed ({rate}% success)."]
    if classification.recoverable_count:
        parts.append(
            f"{classification.recoverable_count} can be fixed via mapping; "
            f"{classification.unrecoverable_count} need manual review or org changes."
        )
    elif classification.unrecoverable_notice:
        parts.append(classification.unrecoverable_notice + ".")
    return " ".join(parts)


def _rule_based_recovery_plan(
    observation: ExecutionObservation,
    *,
    analyze_visible: Optional[Dict[str, Any]] = None,
    remediation_proposal: Optional[Dict[str, Any]] = None,
    session=None,
    classification_summary=None,
) -> RecoveryExecutionPlanDoc:
    report = observation.failure_report
    if not report:
        return RecoveryExecutionPlanDoc(
            what_went_wrong="Migration failed but detailed errors are unavailable.",
            ask_user=True,
            rationale="Review migration records manually.",
            confidence="low",
        )

    clusters = (
        _clusters_from_classification(classification_summary)
        if classification_summary
        else _cluster_errors(report)
    )
    mapping_summary = (
        (remediation_proposal or {}).get("mapping_context_summary")
        or (analyze_visible or {}).get("narrative")
        or _default_mapping_summary(report)
    )
    rel_notice = _relationship_notice_from_session(session) if session else None

    recoverable_count = (
        int(classification_summary.recoverable_count)
        if classification_summary
        else 0
    )
    steps: List[RecoveryPlanStep] = []
    ask_user = False
    confidence = "medium"

    if classification_summary and recoverable_count <= 0:
        confidence = "high"
        ask_user = True
    elif recoverable_count > 0:
        steps = _standard_remap_steps(mapping_summary, recoverable_count=recoverable_count)
        if report.success_rate < 0.5:
            confidence = "low"
            ask_user = True
    elif report.failed_fields or report.top_errors:
        steps = _standard_remap_steps(mapping_summary)
        if report.success_rate < 0.5:
            confidence = "low"
            ask_user = True
    else:
        ask_user = True

    what = (
        _what_went_wrong_from_classification(classification_summary, report)
        if classification_summary
        else ((analyze_visible or {}).get("narrative") or _default_what_went_wrong(report))
    )

    rationale = (
        f"{recoverable_count} recoverable of {report.failed_count} failed. "
        if classification_summary and recoverable_count
        else f"{report.failed_count} records failed. "
    ) + (
        "Re-map affected fields, then retry migration for those records only."
        if steps
        else "Review errors in the migration panel — automatic recovery is not available."
    )

    return RecoveryExecutionPlanDoc(
        what_went_wrong=what,
        error_clusters=clusters,
        relationship_notice=rel_notice,
        steps=steps,
        mapping_context_summary=mapping_summary if steps else None,
        ask_user=ask_user,
        rationale=rationale,
        confidence=confidence,
    )


def _default_mapping_summary(report: FailureReport) -> str:
    parts = ["Fix migration errors from the previous attempt."]
    if report.failed_fields:
        parts.append(f"Review mapping for: {', '.join(report.failed_fields[:8])}.")
    if report.top_errors:
        parts.append(f"Address: {report.top_errors[0].message[:180]}")
    return " ".join(parts)


def _standard_remap_steps(
    mapping_summary: str,
    *,
    recoverable_count: Optional[int] = None,
) -> List[RecoveryPlanStep]:
    migrate_label = "Retry migration for failed records"
    if recoverable_count:
        migrate_label = f"Retry migration ({recoverable_count} recoverable record{'s' if recoverable_count != 1 else ''})"
    return [
        RecoveryPlanStep(
            step_type="map",
            label="Re-map fields to fix errors",
            tool_id="map_fields",
            parameters={
                "tool_id": "map_fields",
                "mapping_context_summary": mapping_summary,
            },
        ),
        RecoveryPlanStep(
            step_type="migrate",
            label=migrate_label,
            tool_id="start_migration",
            parameters={"tool_id": "start_migration"},
        ),
    ]


async def plan_recovery_with_llm(
    session,
    observation: ExecutionObservation,
    *,
    analyze_visible: Optional[Dict[str, Any]] = None,
    remediation_proposal: Optional[Dict[str, Any]] = None,
    similar_context: Optional[str] = None,
    classification_summary=None,
) -> RecoveryExecutionPlanDoc:
    """Build an approvable recovery plan; falls back to rules on LLM failure."""
    report = observation.failure_report
    fallback = _rule_based_recovery_plan(
        observation,
        analyze_visible=analyze_visible,
        remediation_proposal=remediation_proposal,
        session=session,
        classification_summary=classification_summary,
    )
    if not report:
        return fallback

    if classification_summary and classification_summary.recoverable_count <= 0:
        return fallback

    rel_notice = _relationship_notice_from_session(session)
    intent = {}
    snap = getattr(session, "classification_snapshot", None) or {}
    contract = snap.get("plan_contract") or {}
    if isinstance(contract.get("migration_intent"), dict):
        intent = contract["migration_intent"]

    prompt = f"""You are a migration recovery planner. The user's migration partially or fully failed.
Build a concise recovery execution plan (re-map → retry migrate when appropriate).

Failure report:
{json.dumps(report.model_dump(mode="json"), default=str)[:3500]}

Recovery classification (authoritative for recoverability — do not propose steps for unrecoverable rows):
{json.dumps(classification_summary.model_dump(mode="json") if classification_summary else {}, default=str)[:3500]}

Analysis narrative:
{(analyze_visible or {}).get("narrative") or ""}

Remediation hints:
{json.dumps(remediation_proposal or {}, default=str)[:1200]}

Migration relationship intent (if any):
{json.dumps(intent, default=str)[:800]}

Similar past runs:
{(similar_context or "")[:600]}

Return ONLY valid JSON:
{{
  "what_went_wrong": "2-3 sentence plain-language summary for the user",
  "error_clusters": [
    {{"theme": "short label", "count": 10, "sample_message": "...", "fields": ["FieldApiName"]}}
  ],
  "relationship_notice": "optional note about parent/child lookup issues or null",
  "mapping_context_summary": "instructions for the map_fields step",
  "ask_user": false,
  "rationale": "why this recovery path",
  "confidence": "high|medium|low",
  "steps": [
    {{"step_type": "map", "label": "Re-map fields to fix errors", "tool_id": "map_fields"}},
    {{"step_type": "migrate", "label": "Retry migration", "tool_id": "start_migration"}}
  ]
}}

Rules:
- ONLY propose map_fields → start_migration when recovery classification shows recoverable_count > 0.
- Do NOT propose recovery steps for system limits, invalid source data, or unknown errors.
- Prefer map_fields → start_migration when field/lookup/validation errors dominate among recoverable rows.
- Use start_migration alone only when mapping is already correct and a retry is sufficient.
- Cluster errors by theme (required fields, lookups, duplicates, invalid values).
- If success rate is below 50%, you may set ask_user true but STILL propose steps when a mapping fix is plausible.
- Include relationship_notice when migration_intent shows parent/child extract and errors mention lookups.
- steps must use tool_id map_fields and/or start_migration only.
"""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks
        from utils.openai_helpers import parse_openai_json_response

        response = await async_openai_call(
            None,
            task=llm_tasks.RECOVERY_PLAN,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.0,
            user_id=str(getattr(session, "user_id", "") or ""),
            conversation_id=str(getattr(session, "conversation_id", "") or "") or None,
        )
        raw = (response.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return fallback
        data = parse_openai_json_response(match.group(0))

        clusters: List[ErrorCluster] = []
        for item in data.get("error_clusters") or []:
            if isinstance(item, dict) and item.get("theme"):
                clusters.append(
                    ErrorCluster(
                        theme=str(item.get("theme") or "Errors"),
                        count=int(item.get("count") or 1),
                        sample_message=str(item.get("sample_message") or "")[:240],
                        fields=[str(f) for f in (item.get("fields") or []) if f],
                    )
                )
        if not clusters:
            clusters = fallback.error_clusters

        steps: List[RecoveryPlanStep] = []
        for item in data.get("steps") or []:
            if not isinstance(item, dict):
                continue
            tool_id = (item.get("tool_id") or "").strip()
            if tool_id not in ("map_fields", "start_migration"):
                continue
            label = str(item.get("label") or tool_id)
            step_type = str(item.get("step_type") or ("map" if tool_id == "map_fields" else "migrate"))
            params = {"tool_id": tool_id}
            if tool_id == "map_fields":
                summary = (data.get("mapping_context_summary") or fallback.mapping_context_summary or "").strip()
                if summary:
                    params["mapping_context_summary"] = summary
            steps.append(
                RecoveryPlanStep(
                    step_type=step_type,
                    label=label,
                    tool_id=tool_id,
                    parameters=params,
                )
            )

        if not steps:
            steps = fallback.steps

        if classification_summary and classification_summary.recoverable_count <= 0:
            steps = []

        mapping_summary = (data.get("mapping_context_summary") or fallback.mapping_context_summary or "").strip()
        return RecoveryExecutionPlanDoc(
            what_went_wrong=str(data.get("what_went_wrong") or fallback.what_went_wrong).strip(),
            error_clusters=clusters,
            relationship_notice=(
                str(data.get("relationship_notice")).strip()
                if data.get("relationship_notice")
                else rel_notice
            ),
            steps=steps,
            mapping_context_summary=mapping_summary or None,
            ask_user=bool(data.get("ask_user")),
            rationale=str(data.get("rationale") or fallback.rationale).strip(),
            confidence=str(data.get("confidence") or "medium").lower(),
        )
    except Exception:
        logger.exception("recovery_planner LLM failed")
        return fallback


def recovery_plan_to_execution_plan(plan: RecoveryExecutionPlanDoc) -> Dict[str, Any]:
    return {
        "steps": [s.model_dump(mode="json") for s in plan.steps],
        "metadata": {
            "template": "recovery",
            "confidence": plan.confidence,
            "ask_user": plan.ask_user,
        },
    }


def recovery_plan_to_decision(plan: RecoveryExecutionPlanDoc, *, job_id: Optional[str] = None):
    from agent.observation import RecoveryDecision

    if not plan.steps:
        return RecoveryDecision(
            ask_user=True,
            rationale=plan.rationale or plan.what_went_wrong or "Review migration errors manually.",
        )

    first = plan.steps[0]
    args = dict(first.parameters or {})
    if job_id and first.tool_id == "map_fields":
        args.setdefault("job_id", job_id)

    return RecoveryDecision(
        tool_id=first.tool_id,
        arguments=args,
        rationale=plan.rationale or plan.what_went_wrong,
        ask_user=plan.ask_user,
    )
