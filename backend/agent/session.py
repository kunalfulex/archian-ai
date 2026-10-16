from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from handlers.classification_handler import get_active_intent_state
from services.extraction_results_service import ExtractionResultsService

AgentMode = Literal["extract", "data_ops", "migrate", "map", "general"]


def infer_agent_mode_from_classification(classification: Dict[str, Any]) -> AgentMode:
    """Derive agent session mode when classification.mode is missing."""
    raw = (classification.get("mode") or "").strip().lower()
    if raw in ("extract", "data_ops", "migrate", "map", "general"):
        return raw  # type: ignore[return-value]
    if raw == "join":
        return "extract"
    intent = (classification.get("intent") or "").lower()
    if intent in (
        "transform",
        "filter",
        "dedupe",
        "validate",
        "validation",
        "cleanup",
        "multi",
        "transform_data",
    ):
        return "data_ops"
    if intent == "map_data":
        return "map"
    if intent in ("migrate_data", "migrate"):
        return "migrate"
    if intent in ("extract", "metadata_deploy", "join"):
        return "extract"
    return "general"


@dataclass
class AgentSession:
    user_id: str
    conversation_id: Optional[str]
    mode: AgentMode
    active_job_id: Optional[str] = None
    active_version: Optional[int] = None
    source: Optional[str] = None
    destination: Optional[str] = None
    source_name: Optional[str] = None
    destination_name: Optional[str] = None
    extraction_rules: Optional[Dict[str, Any]] = None
    source_schema: Optional[List[str]] = None
    prompt: str = ""
    operations: List[str] = field(default_factory=list)
    last_result: Optional[Dict[str, Any]] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    classification_snapshot: Dict[str, Any] = field(default_factory=dict)
    execution_plan: Optional[Dict[str, Any]] = None
    mapping_id: Optional[str] = None
    mapping_config: Optional[Dict[str, Any]] = None
    mapping_version: int = 0
    pending_file_upload: Optional[Dict[str, Any]] = None
    execution_trace: List[Dict[str, Any]] = field(default_factory=list)
    extraction_finished_sse_emitted: bool = False

    def append_execution_observation(self, observation: Any) -> None:
        dump = (
            observation.model_dump(mode="json")
            if hasattr(observation, "model_dump")
            else dict(observation or {})
        )
        self.execution_trace.append(dump)
        if dump.get("failure_report"):
            snap = dict(self.classification_snapshot or {})
            snap["last_failure_report"] = dump["failure_report"]
            self.classification_snapshot = snap

    @classmethod
    async def from_context(
        cls,
        user_id: str,
        conversation_id: Optional[str],
        conversation_history: List[Dict],
        classification: Dict[str, Any],
        prompt: str,
    ) -> "AgentSession":
        intent_state = await get_active_intent_state(
            conversation_id, user_id, conversation_history, current_prompt=prompt
        )
        mode = classification.get("mode") or infer_agent_mode_from_classification(classification)
        if mode not in ("extract", "data_ops", "migrate", "map", "general"):
            mode = infer_agent_mode_from_classification(classification)

        job_id = None
        rules = classification.get("extraction_rules")
        version = None
        if intent_state:
            job_id = intent_state.get("job_id") or intent_state.get("active_job_id")
            version = intent_state.get("active_version")
            if not rules:
                rules = intent_state.get("extraction_rules")

        mode_for_job = infer_agent_mode_from_classification(classification)
        if mode_for_job in ("map", "migrate"):
            from handlers.classification_handler import latest_extraction_session_for_classifier

            latest_sess = latest_extraction_session_for_classifier(conversation_history)
            if latest_sess and latest_sess.get("job_id"):
                job_id = latest_sess.get("job_id")
                if not rules and latest_sess.get("extraction_rules"):
                    rules = latest_sess.get("extraction_rules")

        if not job_id:
            job_id = (classification.get("active_job_id") or "").strip() or None

        if not job_id:
            for msg in reversed(conversation_history or []):
                meta = msg.get("metadata") or {}
                if meta.get("event_type") == "extraction_completed" and meta.get("job_id"):
                    job_id = meta.get("job_id")
                    if not rules:
                        rules = meta.get("extraction_rules")
                    break

        schema = None
        if job_id:
            row = await ExtractionResultsService.get_extraction_result(
                job_id, user_id, version=version
            )
            if row:
                version = row.get("extraction_version")
                data = row.get("extracted_data") or []
                if data and isinstance(data[0], dict):
                    schema = sorted(
                        {k for r in data[:30] if isinstance(r, dict) for k in r.keys()}
                    )

        return cls(
            user_id=str(user_id),
            conversation_id=conversation_id,
            mode=mode,
            active_job_id=job_id,
            active_version=version,
            source=classification.get("source"),
            destination=classification.get("destination"),
            source_name=classification.get("source_name"),
            destination_name=classification.get("destination_name"),
            extraction_rules=rules if isinstance(rules, dict) else None,
            source_schema=schema,
            prompt=prompt,
            operations=list(classification.get("operations") or []),
            conversation_history=list(conversation_history or []),
            classification_snapshot=dict(classification),
            execution_plan=classification.get("execution_plan")
            if isinstance(classification.get("execution_plan"), dict)
            else None,
        )

    @classmethod
    async def from_architect_context(
        cls,
        user_id: str,
        conversation_id: Optional[str],
        conversation_history: List[Dict],
        classification: Dict[str, Any],
        prompt: str,
        pending_file_upload: Optional[Dict[str, Any]] = None,
    ) -> "AgentSession":
        """
        Cold-start extract/migrate/general sessions: do not require an existing extraction job.
        Optional job/schema loaded when intent_state or history references a completed extract.
        """
        base = await cls.from_context(
            user_id,
            conversation_id,
            conversation_history,
            classification,
            prompt,
        )
        mode = infer_agent_mode_from_classification(classification)
        from services.planning.plan_scope import classification_retains_active_job_at_execution

        retain_job = classification_retains_active_job_at_execution(classification)
        pinned_job = (classification.get("active_job_id") or "").strip() or None
        active_job_id = (pinned_job or base.active_job_id) if retain_job else None
        active_version = base.active_version if retain_job else None
        extraction_rules = base.extraction_rules if retain_job else None
        source_schema = base.source_schema if retain_job else None
        session = cls(
            user_id=base.user_id,
            conversation_id=base.conversation_id,
            mode=mode,
            active_job_id=active_job_id,
            active_version=active_version,
            source=classification.get("source"),
            destination=classification.get("destination"),
            source_name=classification.get("source_name"),
            destination_name=classification.get("destination_name"),
            extraction_rules=extraction_rules,
            source_schema=source_schema,
            prompt=prompt,
            operations=list(classification.get("operations") or []),
            conversation_history=list(conversation_history or []),
            classification_snapshot=dict(classification),
            execution_plan=classification.get("execution_plan")
            if isinstance(classification.get("execution_plan"), dict)
            else None,
        )
        needs_migration_hydrate = mode in ("map", "migrate")
        if not needs_migration_hydrate:
            exec_plan = classification.get("execution_plan")
            if isinstance(exec_plan, dict):
                for step in exec_plan.get("steps") or []:
                    params = step.get("parameters") if isinstance(step, dict) else {}
                    if isinstance(params, dict) and params.get("tool_id") == "start_migration":
                        needs_migration_hydrate = True
                        break
            if not needs_migration_hydrate:
                step_tools = classification.get("step_tools") or []
                if isinstance(step_tools, list) and "start_migration" in step_tools:
                    needs_migration_hydrate = True
            if not needs_migration_hydrate:
                cont_steps = classification.get("_continuation_steps") or []
                if isinstance(cont_steps, list) and "migrate" in cont_steps:
                    needs_migration_hydrate = True
        if needs_migration_hydrate:
            from services.migration.session_migration import hydrate_session_migration_context

            await hydrate_session_migration_context(session)
        if pending_file_upload and isinstance(pending_file_upload, dict):
            session.pending_file_upload = dict(pending_file_upload)
        return session

    def merge_result(self, result: Dict[str, Any]) -> None:
        self.last_result = result
        if result.get("job_id"):
            from agent.terminal_extraction import is_migration_terminal_result

            if not is_migration_terminal_result(result):
                self.active_job_id = result["job_id"]
                if self.classification_snapshot is not None:
                    self.classification_snapshot["active_job_id"] = self.active_job_id
        if result.get("extraction_version"):
            self.active_version = result["extraction_version"]
        if result.get("extraction_rules"):
            self.extraction_rules = result["extraction_rules"]
        if result.get("source_schema"):
            self.source_schema = result["source_schema"]
        dest_name = result.get("destination_name") or result.get("spreadsheet_name")
        if dest_name:
            self.destination_name = str(dest_name).strip()
            if self.classification_snapshot is not None:
                self.classification_snapshot["destination_name"] = self.destination_name
                self.classification_snapshot["planner_destination_confirmed"] = True
        sheet_id = result.get("spreadsheet_id")
        sheet_name = result.get("sheet_name") or result.get("destination_sheet_name")
        if sheet_id and self.classification_snapshot is not None:
            self.classification_snapshot["destination_spreadsheet_id"] = str(sheet_id)
            if sheet_name or result.get("event_type") == "spreadsheet_created":
                from services.planning.google_sheets_destination import (
                    pin_google_sheets_destination_after_create,
                )

                self.classification_snapshot = pin_google_sheets_destination_after_create(
                    self.classification_snapshot,
                    spreadsheet_id=str(sheet_id),
                    sheet_name=str(sheet_name or "Sheet1"),
                    spreadsheet_name=dest_name,
                )
        if result.get("destination"):
            self.destination = result["destination"]
            if self.classification_snapshot is not None:
                self.classification_snapshot["destination"] = self.destination
        elif result.get("spreadsheet_name") and not self.destination:
            self.destination = "google_sheets"
            if self.classification_snapshot is not None:
                self.classification_snapshot["destination"] = "google_sheets"
        if result.get("mapping_config") and isinstance(result["mapping_config"], dict):
            self.mapping_config = result["mapping_config"]
        elif isinstance(result.get("mapping"), dict):
            nested = result["mapping"]
            cfg = nested.get("mapping") if isinstance(nested.get("mapping"), dict) else nested
            if isinstance(cfg, dict) and cfg:
                self.mapping_config = cfg
        if result.get("mapping_id"):
            self.mapping_id = result["mapping_id"]
            if self.classification_snapshot is not None:
                self.classification_snapshot["mapping_id"] = self.mapping_id
        if result.get("mapping_version"):
            self.mapping_version = int(result["mapping_version"])
        connector_id = result.get("destination_connector_id") or result.get("connector_id")
        if connector_id and self.classification_snapshot is not None:
            self.classification_snapshot["destination_connector_id"] = str(connector_id)
        primary_table = result.get("primary_table")
        if primary_table and self.classification_snapshot is not None:
            self.classification_snapshot["destination_table_name"] = str(primary_table)
            self.classification_snapshot["primary_table"] = str(primary_table)
        if (
            sheet_name
            and self.classification_snapshot is not None
            and not self.classification_snapshot.get("destination_sheet_name")
        ):
            self.classification_snapshot["destination_sheet_name"] = str(sheet_name)
