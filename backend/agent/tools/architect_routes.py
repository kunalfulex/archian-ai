"""Thin Tool wrappers for architect SSE handlers."""

from __future__ import annotations

from typing import Any, AsyncIterable, Dict, Set

from agent.session import AgentSession
from agent.stream_utils import consume_legacy_sse_stream, session_delta_from_sse_meta
from agent.tools.base import Tool, ToolResult
from handlers.file_extract_handler import handle_file_extract_stream
from handlers.file_reextraction_handler import handle_file_reextraction_stream
from handlers.general_ai_handler import handle_general_ai_response_stream
from handlers.google_sheets_handler import (
    handle_google_sheets_create_from_prompt_stream,
    handle_google_sheets_extraction_stream,
    handle_google_sheets_info_stream,
    handle_google_sheets_update_metadata_stream,
)
from handlers.salesforce_handler import (
    handle_salesforce_data_extraction_stream,
    handle_salesforce_metadata_deploy_stream,
    handle_salesforce_request_stream,
)
from handlers.sql_handler import handle_sql_data_extraction_stream, handle_sql_table_info_stream


_EXTRACT_MIGRATE_MODES: Set[str] = {"extract", "migrate"}
_GENERAL_MODES: Set[str] = {"extract", "migrate", "general"}


def _migration_intent_for_extract(
    session: AgentSession,
    args: Dict[str, Any],
) -> Dict[str, Any] | None:
    raw = args.get("migration_intent")
    if isinstance(raw, dict) and raw:
        return dict(raw)
    snap = session.classification_snapshot or {}
    mi = snap.get("migration_intent")
    if isinstance(mi, dict) and mi:
        return dict(mi)
    exec_plan = session.execution_plan if isinstance(session.execution_plan, dict) else {}
    plan_mi = (exec_plan.get("metadata") or {}).get("migration_intent")
    if isinstance(plan_mi, dict) and plan_mi:
        return dict(plan_mi)
    return None


async def _consume_handler_stream(stream_maker) -> ToolResult:
    stream = await stream_maker()
    chunks, meta = await consume_legacy_sse_stream(stream)
    if meta.get("_stream_status") == "error":
        return ToolResult(
            ok=False,
            message=meta.get("_stream_error_message") or "Request failed",
            sse_chunks=chunks,
        )
    visible = session_delta_from_sse_meta(meta)
    return ToolResult(
        ok=True,
        sse_chunks=chunks or None,
        user_visible=visible or None,
    )


class ExtractGoogleSheetsTool(Tool):
    tool_id = "extract_google_sheets"
    description = "Extract data from Google Sheets toward a destination"
    allowed_modes = _EXTRACT_MIGRATE_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        return await handle_google_sheets_extraction_stream(
            session.prompt,
            session.user_id,
            session.conversation_history,
            destination=args.get("destination", "salesforce"),
            destination_name=args.get("destination_name"),
            source_name=args.get("source_name"),
            conversation_id=session.conversation_id,
            source_sheet_name=args.get("source_sheet_name"),
            destination_sheet_name=args.get("destination_sheet_name"),
            source_entity_key=args.get("source_entity_key"),
            extract_field_scope=args.get("extract_field_scope"),
            extract_contract=args.get("extract_contract"),
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


class ExtractSqlTool(Tool):
    tool_id = "extract_sql"
    description = "Extract data from SQL toward a destination"
    allowed_modes = _EXTRACT_MIGRATE_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        return await handle_sql_data_extraction_stream(
            session.prompt,
            session.user_id,
            session.conversation_history,
            destination=args.get("destination", "salesforce"),
            destination_name=args.get("destination_name"),
            source_name=args.get("source_name"),
            conversation_id=session.conversation_id,
            source_entity_key=args.get("source_entity_key"),
            related_entity_keys=args.get("related_entity_keys"),
            extract_field_scope=args.get("extract_field_scope"),
            migration_intent=_migration_intent_for_extract(session, args),
            extract_contract=args.get("extract_contract"),
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


class ExtractSalesforceTool(Tool):
    tool_id = "extract_salesforce"
    description = "Extract data from Salesforce toward a destination"
    allowed_modes = _EXTRACT_MIGRATE_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        return await handle_salesforce_data_extraction_stream(
            session.prompt,
            session.user_id,
            session.conversation_history,
            destination=args.get("destination", "salesforce"),
            destination_name=args.get("destination_name"),
            source_name=args.get("source_name"),
            conversation_id=session.conversation_id,
            source_entity_key=args.get("source_entity_key"),
            related_entity_keys=args.get("related_entity_keys"),
            extract_field_scope=args.get("extract_field_scope"),
            migration_intent=_migration_intent_for_extract(session, args),
            extract_contract=args.get("extract_contract"),
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


class MetadataDeploySalesforceTool(Tool):
    tool_id = "metadata_deploy_salesforce"
    description = "Deploy Salesforce metadata between orgs"
    allowed_modes = _EXTRACT_MIGRATE_MODES

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(
            lambda: handle_salesforce_metadata_deploy_stream(
                session.prompt,
                session.user_id,
                session.conversation_history,
                source_name=args.get("source_name"),
                destination_name=args.get("destination_name"),
                conversation_id=session.conversation_id,
                deploy_contract=args.get("deploy_contract"),
            )
        )


class ExtractFileTool(Tool):
    tool_id = "extract_file"
    description = "Upload and extract structured data from an attached file"
    allowed_modes = _EXTRACT_MIGRATE_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        pending = session.pending_file_upload
        if not pending or not pending.get("content"):
            # Resume / Run plan may only carry _pending_file_ref on classification.
            try:
                from services.file_extraction.pending_upload import resolve_pending_file_upload

                pending = await resolve_pending_file_upload(
                    pending_file_upload=pending,
                    classification=session.classification_snapshot,
                )
                if pending and pending.get("content"):
                    session.pending_file_upload = pending
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning(
                    "extract_file: failed to resolve staged pending file: %s",
                    exc,
                    exc_info=True,
                )
                pending = None
        if not pending or not pending.get("content"):
            async def _err():
                import json
                import logging

                snap = session.classification_snapshot or {}
                ref = snap.get("_pending_file_ref") if isinstance(snap, dict) else None
                has_ref = bool(isinstance(ref, dict) and (ref.get("file_key") or "").strip())
                logging.getLogger(__name__).warning(
                    "extract_file missing upload bytes (has_pending_file_ref=%s source=%r source_name=%r)",
                    has_ref,
                    (snap or {}).get("source"),
                    (snap or {}).get("source_name"),
                )
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "status": "error",
                            "message": (
                                "The attached file is no longer available for extraction. "
                                "Please re-attach the file and run the plan again."
                            ),
                        }
                    )
                    + "\n\n"
                )

            return _err()
        return handle_file_extract_stream(
            prompt=session.prompt,
            user_id=session.user_id,
            conversation_id=session.conversation_id,
            conversation_history=session.conversation_history,
            pending_file=pending,
            destination=args.get("destination") or "salesforce",
            destination_name=args.get("destination_name"),
            classification_snapshot=session.classification_snapshot,
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


class FileReextractTool(Tool):
    tool_id = "file_reextract"
    description = "Re-run file extraction using stored extraction context"
    allowed_modes = _EXTRACT_MIGRATE_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        intent_state = args.get("intent_state") or {}
        cid = session.conversation_id or ""
        return await handle_file_reextraction_stream(
            prompt=session.prompt,
            user_id=session.user_id,
            conversation_history=session.conversation_history,
            intent_state=intent_state,
            conversation_id=cid,
            destination=args.get("destination") or "unknown",
            destination_name=args.get("destination_name"),
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


class GoogleSheetsCreateFromPromptTool(Tool):
    tool_id = "google_sheets_create_from_prompt"
    description = "Create a Google Sheet from the user's prompt"
    allowed_modes = _GENERAL_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        return await handle_google_sheets_create_from_prompt_stream(
            session.prompt,
            session.user_id,
            session.conversation_history,
            conversation_id=session.conversation_id,
            intent_state=args.get("intent_state"),
            extraction_job_id=args.get("extraction_job_id"),
            spreadsheet_title=args.get("spreadsheet_title"),
            headers=args.get("headers"),
            use_extraction_headers=bool(args.get("use_extraction_headers")),
            create_contract=args.get("create_contract"),
            google_sheets_connection_id=args.get("google_sheets_connection_id"),
            stream_prose=False,
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


class GoogleSheetsUpdateMetadataTool(Tool):
    tool_id = "google_sheets_update_metadata"
    description = "Update Google Sheets spreadsheet metadata"
    allowed_modes = _GENERAL_MODES

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(
            lambda: handle_google_sheets_update_metadata_stream(
                session.prompt, session.user_id, session.conversation_history
            )
        )


class GoogleSheetsInfoTool(Tool):
    tool_id = "google_sheets_info"
    description = "Answer questions about connected Google Sheets"
    allowed_modes = _GENERAL_MODES

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(
            lambda: handle_google_sheets_info_stream(
                session.prompt,
                session.user_id,
                session.conversation_history,
                workbook_name=args.get("workbook_name"),
                sheet_name=args.get("sheet_name"),
            )
        )


class SqlTableInfoTool(Tool):
    tool_id = "sql_table_info"
    description = "Answer questions about connected SQL tables/schemas"
    allowed_modes = _GENERAL_MODES

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(
            lambda: handle_sql_table_info_stream(
                session.prompt,
                session.conversation_history,
                user_id=session.user_id,
                source_name=args.get("source_name"),
            )
        )


class SalesforceInfoRequestTool(Tool):
    tool_id = "salesforce_info_request"
    description = "Salesforce object/schema info chat"
    allowed_modes = _GENERAL_MODES

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        route_source = args.get("route_source") or session.source or "salesforce"
        route_name = args.get("route_source_name")
        return await _consume_handler_stream(
            lambda: handle_salesforce_request_stream(
                session.prompt,
                session.conversation_history,
                session.user_id,
                route_source,
                route_name,
            )
        )


class GeneralAiResponseTool(Tool):
    tool_id = "general_ai_response"
    description = "Fallback general assistant response"
    allowed_modes = _GENERAL_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        snap = session.classification_snapshot or {}
        return await handle_general_ai_response_stream(
            session.prompt,
            session.conversation_history,
            source=snap.get("source"),
            source_name=snap.get("source_name"),
            destination=snap.get("destination"),
            destination_name=snap.get("destination_name"),
            user_id=str(session.user_id),
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


__all__ = [
    "ExtractGoogleSheetsTool",
    "ExtractSqlTool",
    "ExtractSalesforceTool",
    "MetadataDeploySalesforceTool",
    "FileReextractTool",
    "GoogleSheetsCreateFromPromptTool",
    "GoogleSheetsUpdateMetadataTool",
    "GoogleSheetsInfoTool",
    "SqlTableInfoTool",
    "SalesforceInfoRequestTool",
    "GeneralAiResponseTool",
]
