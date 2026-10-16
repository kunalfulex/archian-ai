"""Read-only recovery diagnosis tools for Agent Runtime v2."""
from __future__ import annotations

from typing import Any, Dict, List

from agent.observe import build_observation_from_batch
from agent.observation import FailureReport
from agent.session import AgentSession
from agent.tools.base import Tool, ToolResult


def _build_failure_narrative(report: FailureReport) -> str:
    ok = report.successful_records
    total = report.total_records
    fail = report.failed_count
    rate_pct = int(report.success_rate * 100)
    lines = [
        f"Migration finished with **{ok}/{total}** records successful ({rate_pct}% success rate).",
        f"**{fail}** record{'s' if fail != 1 else ''} failed.",
    ]
    if report.failed_fields:
        fields = ", ".join(report.failed_fields[:8])
        suffix = f" (and {len(report.failed_fields) - 8} more)" if len(report.failed_fields) > 8 else ""
        lines.append(f"Fields implicated: **{fields}**{suffix}.")
    if report.top_errors:
        err = report.top_errors[0]
        lines.append(f"Most common error: {err.message[:240]}")
    return " ".join(lines)


class AnalyzeMigrationFailuresTool(Tool):
    tool_id = "analyze_migration_failures"
    description = "Summarize migration batch failures into a structured FailureReport and narrative"
    allowed_modes = {"extract", "migrate", "map", "data_ops", "general"}

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        batch_id = (args.get("batch_id") or "").strip()
        if not batch_id:
            snap = session.classification_snapshot or {}
            last = snap.get("last_failure_report") or {}
            batch_id = (last.get("batch_id") or "").strip()
        if not batch_id:
            return ToolResult(ok=False, message="batch_id is required to analyze migration failures.")

        observation = await build_observation_from_batch(
            tool_id="start_migration",
            batch_id=batch_id,
            job_id=args.get("job_id") or session.active_job_id,
            mapping_id=args.get("mapping_id") or session.mapping_id,
        )
        report = observation.failure_report
        if not report:
            return ToolResult(
                ok=True,
                user_visible={
                    "action": "migration_analysis",
                    "batch_id": batch_id,
                    "status": observation.status,
                    "narrative": "Migration batch is still processing or has no failure detail yet.",
                },
                observation=observation,
            )

        narrative = _build_failure_narrative(report)
        return ToolResult(
            ok=True,
            user_visible={
                "action": "migration_analysis",
                "batch_id": batch_id,
                "failure_report": report.model_dump(mode="json"),
                "narrative": narrative,
                "failed_fields": report.failed_fields,
                "top_errors": [e.model_dump(mode="json") for e in report.top_errors],
            },
            observation=observation,
        )


class ProposeMappingRemediationTool(Tool):
    tool_id = "propose_mapping_remediation"
    description = "Read-only mapping remediation proposal from failure report and session context"
    allowed_modes = {"extract", "migrate", "map", "data_ops", "general"}

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        from services.planning.continuation_policy import build_retry_mapping_context

        report_raw = args.get("failure_report")
        if not report_raw:
            snap = session.classification_snapshot or {}
            report_raw = snap.get("last_failure_report") or {}
        if not report_raw and args.get("batch_id"):
            obs = await build_observation_from_batch(
                tool_id="start_migration",
                batch_id=str(args["batch_id"]),
                job_id=session.active_job_id,
            )
            report_raw = (
                obs.failure_report.model_dump(mode="json") if obs.failure_report else {}
            )

        failed_fields = list(report_raw.get("failed_fields") or [])
        top_errors = list(report_raw.get("top_errors") or [])
        summary = build_retry_mapping_context(report_raw) or (
            "Review field mapping for failed records before retrying migration."
        )

        suggestions: List[Dict[str, Any]] = []
        for field in failed_fields[:12]:
            suggestions.append(
                {
                    "field": field,
                    "action": "review_mapping",
                    "note": f"Verify source column maps correctly to {field}.",
                }
            )

        proposal = {
            "mapping_context_summary": summary,
            "failed_fields": failed_fields,
            "suggestions": suggestions,
            "top_errors": top_errors[:5],
        }
        return ToolResult(
            ok=True,
            user_visible={
                "action": "mapping_remediation_proposal",
                "proposal": proposal,
                "narrative": summary,
            },
        )
