"""Recovery decision logic for partial/failed migrations (Agent Runtime v2/v3)."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agent.observation import ExecutionObservation, FailureReport, RecoveryDecision
from agent.preconditions import check_preconditions
from agent.recovery_policy import MIN_SUCCESS_RATE_DIAGNOSE, severity
from agent.session import AgentSession
from agent.tool_manifest import RECOVERY_ALLOWLIST, format_manifest_for_llm, manifest_for_mode
from agent.registry import default_registry

logger = logging.getLogger(__name__)

RECOVERY_READ_TOOLS = frozenset({
    "analyze_migration_failures",
    "retrieve_similar_pipelines",
    "propose_mapping_remediation",
})
RECOVERY_WRITE_TOOLS = frozenset({"map_fields", "start_migration"})
RECOVERY_ACTION_TOOLS = frozenset({"map_fields", "start_migration"})


def _mapping_remediation_summary(report: FailureReport, analyze: Optional[Dict[str, Any]] = None) -> str:
    parts: List[str] = [
        "Fix migration errors from the previous attempt.",
    ]
    if report.failed_fields:
        parts.append(f"Review mapping for fields: {', '.join(report.failed_fields[:12])}.")
    if report.top_errors:
        parts.append(f"Address error: {report.top_errors[0].message[:200]}")
    narrative = (analyze or {}).get("narrative")
    if narrative and narrative not in parts:
        parts.append(narrative[:400])
    return " ".join(parts)


class RecoveryOrchestrator:
    """Recovery planner: LLM pick from allowlist with rule-based fallback."""

    def decide(
        self,
        observation: ExecutionObservation,
        *,
        analyze_visible: Optional[Dict[str, Any]] = None,
        similar_context: Optional[str] = None,
        remediation_proposal: Optional[Dict[str, Any]] = None,
    ) -> RecoveryDecision:
        report = observation.failure_report
        if not report:
            if observation.status == "failed":
                return RecoveryDecision(
                    ask_user=True,
                    rationale="Migration failed but detailed error data is unavailable. Review records manually.",
                )
            return RecoveryDecision(
                ask_user=True,
                rationale="Migration outcome is unclear. Check the migration status panel.",
            )

        sev = severity(observation)
        if sev == "failed" or report.success_rate < MIN_SUCCESS_RATE_DIAGNOSE:
            summary = (
                (remediation_proposal or {}).get("mapping_context_summary")
                or _mapping_remediation_summary(report, analyze_visible)
            )
            if report.failed_fields or report.top_errors:
                rationale = (
                    f"Only {int(report.success_rate * 100)}% succeeded ({report.failed_count} failures). "
                    "Review the recovery plan — re-map and retry may still help."
                )
                if similar_context:
                    rationale = f"{rationale}\n\nSimilar past runs:\n{similar_context[:600]}"
                return RecoveryDecision(
                    tool_id="map_fields",
                    arguments={"mapping_context_summary": summary},
                    rationale=rationale,
                    ask_user=True,
                )
            rationale = (
                f"Only {int(report.success_rate * 100)}% of records succeeded. "
                "Too many failures for automatic remediation — review errors and adjust source data or mapping."
            )
            if similar_context:
                rationale = f"{rationale}\n\nSimilar past runs:\n{similar_context[:600]}"
            return RecoveryDecision(ask_user=True, rationale=rationale)

        if report.failed_fields:
            summary = (
                (remediation_proposal or {}).get("mapping_context_summary")
                or _mapping_remediation_summary(report, analyze_visible)
            )
            rationale = (
                f"{int(report.failed_count)} records failed, often tied to "
                f"{', '.join(report.failed_fields[:5])}. "
                "Re-map those fields, then retry migration."
            )
            return RecoveryDecision(
                tool_id="map_fields",
                arguments={"mapping_context_summary": summary},
                rationale=rationale,
            )

        if report.top_errors:
            summary = (
                (remediation_proposal or {}).get("mapping_context_summary")
                or _mapping_remediation_summary(report, analyze_visible)
            )
            return RecoveryDecision(
                tool_id="map_fields",
                arguments={"mapping_context_summary": summary},
                rationale=(
                    f"Repeated errors detected ({report.top_errors[0].message[:120]}). "
                    "Try adjusting field mapping and retry migration."
                ),
            )

        return RecoveryDecision(
            ask_user=True,
            rationale=(
                f"Partial success ({int(report.success_rate * 100)}%). "
                "Review failed records in the migration panel before retrying."
            ),
        )

    async def decide_with_llm(
        self,
        session: AgentSession,
        observation: ExecutionObservation,
        *,
        analyze_visible: Optional[Dict[str, Any]] = None,
        similar_context: Optional[str] = None,
        remediation_proposal: Optional[Dict[str, Any]] = None,
    ) -> RecoveryDecision:
        """LLM recovery action pick; falls back to :meth:`decide` on failure."""
        llm_decision = await self._llm_pick(
            session,
            observation,
            analyze_visible=analyze_visible,
            similar_context=similar_context,
            remediation_proposal=remediation_proposal,
        )
        if llm_decision is not None:
            await self._log_recovery_pick(session, observation, llm_decision, source="llm")
            return llm_decision

        rule_decision = self.decide(
            observation,
            analyze_visible=analyze_visible,
            similar_context=similar_context,
            remediation_proposal=remediation_proposal,
        )
        await self._log_recovery_pick(session, observation, rule_decision, source="rules")
        return rule_decision

    async def _llm_pick(
        self,
        session: AgentSession,
        observation: ExecutionObservation,
        *,
        analyze_visible: Optional[Dict[str, Any]] = None,
        similar_context: Optional[str] = None,
        remediation_proposal: Optional[Dict[str, Any]] = None,
    ) -> Optional[RecoveryDecision]:
        from services.llm import tasks as llm_tasks
        from services.llm.service import task_has_api_key

        if not await task_has_api_key(llm_tasks.RECOVERY_TOOL_PICK):
            return None

        report = observation.failure_report
        if not report:
            return None

        registry = default_registry()
        entries = [
            e
            for e in manifest_for_mode(registry, "migrate", allowlist=RECOVERY_ALLOWLIST)
            if e.tool_id in RECOVERY_ACTION_TOOLS
        ]
        manifest_text = format_manifest_for_llm("recovery", entries)

        report_json = report.model_dump(mode="json")
        user_msg = (
            f"Migration recovery decision.\n\n"
            f"Failure report:\n{json.dumps(report_json, default=str)[:3000]}\n\n"
            f"Analysis:\n{json.dumps(analyze_visible or {}, default=str)[:1500]}\n\n"
            f"Remediation proposal:\n{json.dumps(remediation_proposal or {}, default=str)[:1000]}\n\n"
        )
        if similar_context:
            user_msg += f"Similar past pipelines:\n{similar_context[:800]}\n\n"
        user_msg += (
            f"{manifest_text}\n\n"
            "Choose the best recovery action. Return JSON only:\n"
            '{"ask_user": false, "tool_id": "map_fields", "arguments": {}, "rationale": "..."}\n'
            'or {"ask_user": true, "rationale": "..."} when automatic remediation is unsafe.\n'
            "Use map_fields when field mapping fixes are likely; start_migration only when mapping "
            "is already correct and a retry is sufficient.\n"
            f"Do not pick tools outside: {', '.join(sorted(RECOVERY_ACTION_TOOLS))} or ask_user."
        )

        try:
            from services.llm import tasks as llm_tasks
            from services.llm.service import llm_complete_openai_compat
            from utils.openai_helpers import parse_openai_json_response

            response = await llm_complete_openai_compat(
                llm_tasks.RECOVERY_TOOL_PICK,
                messages=[{"role": "user", "content": user_msg}],
                temperature=0,
                max_tokens=500,
            )
            raw = (response.choices[0].message.content or "").strip()
            data = parse_openai_json_response(raw)
            return self._parse_llm_recovery_decision(
                data,
                session,
                observation,
                analyze_visible=analyze_visible,
                remediation_proposal=remediation_proposal,
            )
        except Exception as exc:
            logger.warning("[RECOVERY_ORCHESTRATOR] LLM pick failed: %s", exc)
            return None

    def _parse_llm_recovery_decision(
        self,
        data: Any,
        session: AgentSession,
        observation: ExecutionObservation,
        *,
        analyze_visible: Optional[Dict[str, Any]] = None,
        remediation_proposal: Optional[Dict[str, Any]] = None,
    ) -> Optional[RecoveryDecision]:
        if not isinstance(data, dict):
            return None
        if data.get("ask_user"):
            rationale = str(data.get("rationale") or "Review migration errors manually.")
            return RecoveryDecision(ask_user=True, rationale=rationale)

        tool_id = (data.get("tool_id") or "").strip()
        if tool_id not in RECOVERY_ACTION_TOOLS:
            return None

        args = dict(data.get("arguments") or {})
        if tool_id == "map_fields":
            if not args.get("mapping_context_summary"):
                report = observation.failure_report
                if report:
                    args["mapping_context_summary"] = (
                        (remediation_proposal or {}).get("mapping_context_summary")
                        or _mapping_remediation_summary(report, analyze_visible)
                    )
            if not args.get("job_id") and session.active_job_id:
                args["job_id"] = session.active_job_id

        pre = check_preconditions(tool_id, session, args)
        if not pre.ok:
            logger.info(
                "[RECOVERY_ORCHESTRATOR] LLM pick failed preconditions: %s",
                pre.message,
            )
            return None

        return RecoveryDecision(
            tool_id=tool_id,
            arguments=args,
            rationale=str(data.get("rationale") or ""),
        )

    async def _log_recovery_pick(
        self,
        session: AgentSession,
        observation: ExecutionObservation,
        decision: RecoveryDecision,
        *,
        source: str,
    ) -> None:
        try:
            from services.pipeline_learning.store import PipelineLearningStore

            payload = {
                "source": source,
                "tool_id": decision.tool_id,
                "ask_user": decision.ask_user,
                "rationale": (decision.rationale or "")[:500],
                "batch_id": observation.batch_id,
            }
            await PipelineLearningStore.log_retrieval(
                user_id=session.user_id,
                caller="recovery_llm" if source == "llm" else "recovery_rules",
                query_text=json.dumps(payload, default=str)[:4000],
                chunks=[],
                conversation_id=session.conversation_id,
                stage="recovery",
                chunk_type=decision.tool_id or ("ask_user" if decision.ask_user else "none"),
                source_type=session.source,
                destination_type=session.destination,
            )
        except Exception as exc:
            logger.debug("recovery pick log skipped: %s", exc)

    def requires_write_approval(self, decision: RecoveryDecision) -> bool:
        if not decision.tool_id:
            return False
        return decision.tool_id in RECOVERY_WRITE_TOOLS

    def build_execution_plan(self, decision: RecoveryDecision) -> Dict[str, Any]:
        """Mini plan for approved recovery (map → migrate)."""
        if not decision.tool_id:
            return {"steps": [], "metadata": {"template": "recovery", "ask_user": True}}

        steps: List[Dict[str, Any]] = []
        args = dict(decision.arguments or {})
        if decision.tool_id == "map_fields":
            steps.append(
                {
                    "step_type": "map",
                    "label": "Re-map fields to fix errors",
                    "parameters": {
                        "tool_id": "map_fields",
                        "mapping_context_summary": args.get("mapping_context_summary"),
                    },
                }
            )
            steps.append(
                {
                    "step_type": "migrate",
                    "label": "Retry migration",
                    "parameters": {"tool_id": "start_migration"},
                }
            )
        elif decision.tool_id == "start_migration":
            steps.append(
                {
                    "step_type": "migrate",
                    "label": "Retry migration",
                    "parameters": {"tool_id": "start_migration"},
                }
            )
        return {"steps": steps, "metadata": {"template": "recovery"}}
