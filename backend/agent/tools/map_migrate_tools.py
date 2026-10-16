"""Agent tools for map_data / migrate_data intents."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agent.session import AgentSession
from agent.tools.base import Tool, ToolResult
from db.connection import database
from services.extraction_results_service import ExtractionResultsService
from services.migration.session_migration import _infer_sql_table_from_mapping


def _destination_entity_from_session(
    session: AgentSession,
    args: Optional[Dict[str, Any]] = None,
) -> str:
    """Pinned destination entity from plan contract or tool args (no hardcoded defaults)."""
    args = args or {}
    pinned = (args.get("destination_entity_key") or "").strip()
    if pinned:
        if "::" in pinned:
            return pinned.split("::", 1)[-1].strip() or pinned
        return pinned
    snap = session.classification_snapshot or {}
    contract = snap.get("plan_contract") or {}
    dest = contract.get("destination") if isinstance(contract, dict) else {}
    if isinstance(dest, dict):
        key = (dest.get("entity_key") or "").strip()
        if key:
            if "::" in key:
                return key.split("::", 1)[-1].strip() or key
            return key
    for field in (
        "destination_object_api_name",
        "destination_table_name",
        "destination_sheet_name",
    ):
        val = (snap.get(field) or "").strip()
        if val and val.lower() not in ("unknown",):
            return val
    return ""


def _map_fields_progress_sse(label: str, detail: str) -> str:
    from agent.events import operation_phase

    return operation_phase("map_fields", "running", label=label, detail=detail)


async def _load_extraction_row(job_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Load extraction row, polling briefly when a background job just finished."""
    import asyncio

    row = await ExtractionResultsService.get_extraction_result(job_id, user_id)
    if row:
        return row
    for _ in range(30):
        await asyncio.sleep(0.5)
        row = await ExtractionResultsService.get_extraction_result(job_id, user_id)
        if row:
            return row
    return None


def _resolve_mapping_from_session(
    session: AgentSession,
    job_id: Optional[str],
) -> Dict[str, Any]:
    """Use mapping_started chat metadata when map ran outside agent last_result."""
    from services.planning.conversation_session import latest_mapping_started_metadata

    meta = latest_mapping_started_metadata(session.conversation_history)
    if not meta:
        return {}
    meta_job = meta.get("job_id")
    if job_id and meta_job and str(meta_job) != str(job_id):
        return {}
    mapping_config = meta.get("mapping_config") or meta.get("mapping")
    if isinstance(mapping_config, dict) and mapping_config.get("mapping"):
        mapping_config = mapping_config.get("mapping")
    if not isinstance(mapping_config, dict) or not mapping_config:
        return {}
    out: Dict[str, Any] = {
        "mapping_config": mapping_config,
        "mapping_id": meta.get("mapping_id"),
        "document_type": meta.get("destination_document_type") or meta.get("document_type"),
        "document_description": meta.get("destination_document_description")
        or meta.get("document_description"),
        "destination_document_type": meta.get("destination_document_type"),
        "destination_document_description": meta.get("destination_document_description"),
    }
    return out


def _first_record(row: Dict[str, Any]) -> Dict[str, Any]:
    from utils.mapping_sample import pick_most_complete_record

    data = row.get("extracted_data") or []
    if isinstance(data, list) and data:
        return pick_most_complete_record(data)
    if isinstance(data, dict):
        return data
    return {}


def _extraction_context_for_map_sse(row: Dict[str, Any], session: AgentSession) -> Dict[str, Any]:
    """Job metadata for frontend mapping/migrate UI (no row payload)."""
    ext_meta = row.get("extraction_metadata") or {}
    if isinstance(ext_meta, str):
        import json

        try:
            ext_meta = json.loads(ext_meta)
        except Exception:
            ext_meta = {}
    if not isinstance(ext_meta, dict):
        ext_meta = {}

    out: Dict[str, Any] = {}
    rc = row.get("record_count")
    if rc is not None:
        out["record_count"] = rc
        out["total_records"] = rc

    src_id = (row.get("source_identifier") or "").strip()
    src_type = (row.get("source_type") or "").lower()
    if src_type == "sql" and src_id:
        out["source_connector_id"] = src_id
        out["connector_id"] = src_id
    if src_type in ("google_sheets", "gsheets") and src_id:
        out["spreadsheet_id"] = src_id

    sql_q = ext_meta.get("query") or ext_meta.get("soql")
    if sql_q:
        out["sql_query"] = sql_q
    if ext_meta.get("table_name"):
        out["table_name"] = ext_meta["table_name"]
    if ext_meta.get("object_api_name"):
        out["object_api_name"] = ext_meta["object_api_name"]

    if session.destination and str(session.destination).lower() not in ("", "unknown"):
        out.setdefault("destination", session.destination)
    if session.destination_name:
        out.setdefault("destination_name", session.destination_name)

    if row.get("detail_expired"):
        out["detail_expired"] = True
    return out


class MapFieldsTool(Tool):
    tool_id = "map_fields"
    description = "Map extracted fields to destination objects/tables"
    allowed_modes = {"extract", "map", "migrate", "data_ops", "general"}

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        from services.planning.map_flow_debug import log_map_flow

        job_id = args.get("job_id") or session.active_job_id
        destination = (args.get("destination") or session.destination or "unknown").lower()
        destination_name = args.get("destination_name") or session.destination_name
        log_map_flow(
            "map_fields_enter",
            job_id=job_id,
            destination=destination,
            destination_name=destination_name,
            args_keys=sorted(list(args.keys()))[:20],
            session_active_job_id=session.active_job_id,
            dest_object_api_name=(session.classification_snapshot or {}).get(
                "destination_object_api_name"
            ),
            prompt_excerpt=(session.prompt or "")[:200],
        )
        if not job_id:
            log_map_flow("map_fields_abort", reason="no_job_id")
            return ToolResult(ok=False, message="No active extraction job to map.")

        from utils.document_category_match import destination_requires_pick

        if destination_requires_pick(destination, destination_name):
            log_map_flow(
                "map_fields_needs_destination_pick",
                job_id=job_id,
                destination=destination,
                destination_name=destination_name,
            )
            return ToolResult(
                ok=True,
                user_visible={
                    "action": "needs_destination_pick",
                    "job_id": job_id,
                    "destination": destination,
                    "destination_name": destination_name,
                },
            )

        row = await _load_extraction_row(job_id, session.user_id)
        if not row:
            return ToolResult(ok=False, message=f"Extraction job {job_id} not found.")

        flat_data = _first_record(row)
        if not flat_data:
            return ToolResult(ok=False, message="No extracted records available for mapping.")

        filtered_flat = {
            k: v
            for k, v in flat_data.items()
            if k not in ("Attachments", "_attachments", "attachments")
        }
        snap = session.classification_snapshot or {}
        from services.mapping.context import (
            build_mapping_execution_context,
            requires_destination_entity_for_mapping,
        )
        from services.mapping.validate import mapping_config_is_empty

        map_ctx = build_mapping_execution_context(
            classification_snapshot=snap,
            step_args={**args, "job_id": job_id},
            extraction_row=row,
            destination=destination,
            session_prompt=session.prompt,
        )
        if (
            requires_destination_entity_for_mapping(destination)
            and map_ctx.approved_plan
            and not map_ctx.has_pinned_entity
        ):
            log_map_flow(
                "map_fields_abort",
                reason="destination_entity_not_pinned",
                job_id=job_id,
            )
            return ToolResult(
                ok=False,
                message=(
                    "Destination object/table is not pinned on the approved plan. "
                    "Resume planning and confirm the destination entity before mapping."
                ),
                needs_clarification=True,
            )

        mapping_context_summary = map_ctx.mapping_brief or None
        target_resolution = None
        if map_ctx.has_pinned_entity:
            target_resolution = map_ctx.to_target_resolution()
        elif mapping_context_summary or snap:
            try:
                from services.mapping_plan.target_resolution import resolve_mapping_target_async

                target_resolution = await resolve_mapping_target_async(
                    destination=destination,
                    mapping_instruction=mapping_context_summary,
                    classification_snapshot=snap,
                    user_id=session.user_id,
                    destination_name=destination_name,
                )
            except Exception:
                target_resolution = None

        flow_mappings = snap.get("flow_field_mappings") or args.get("flow_field_mappings")
        if flow_mappings:
            from services.planning.flow_steps import flow_field_mappings_to_config
            from utils.migration_rules_utils import (
                dual_publish_mapping_transform_api,
                get_mapping_transformation,
            )

            flow_mt = snap.get("flow_mapping_transformation")
            if not flow_mt:
                flow_id = snap.get("flow_id") or args.get("flow_id")
                if flow_id:
                    try:
                        from db.extracted_schema import get_flow_details
                        from uuid import UUID

                        details = await get_flow_details(str(flow_id), UUID(str(session.user_id)))
                        flow_info = (details or {}).get("flow") or {}
                        rules = flow_info.get("migration_rules") or {}
                        if isinstance(rules, str):
                            import json

                            try:
                                rules = json.loads(rules)
                            except json.JSONDecodeError:
                                rules = {}
                        flow_mt = get_mapping_transformation(rules)
                    except Exception:
                        flow_mt = None

            mapping_cfg = flow_field_mappings_to_config(
                flow_mappings if isinstance(flow_mappings, list) else [],
                destination=destination,
                mapping_transformation=flow_mt,
            )
            if mapping_cfg:
                mapping_id = f"mapping-flow-{int(time.time() * 1000)}"
                visible: Dict[str, Any] = {
                    "action": "map_fields_ready",
                    "mapping_id": mapping_id,
                    "job_id": job_id,
                    "destination": destination,
                    "destination_name": destination_name,
                    "mapping_instruction": mapping_context_summary,
                    "mapping_config": mapping_cfg,
                    "mapping": {"mapping": mapping_cfg},
                    "from_saved_flow": True,
                    "document_type": row.get("document_type") or "",
                    "document_description": row.get("document_description") or "",
                    "source_type": row.get("source_type"),
                    "source_name": row.get("source_name"),
                }
                visible.update(_extraction_context_for_map_sse(row, session))
                dual_publish_mapping_transform_api(visible, flow_mt)
                return ToolResult(ok=True, user_visible=visible)

        step_label = (args.get("label") or "").strip()
        dest_label = destination_name or destination or "destination"
        map_phase_label = step_label or f"Map fields to {dest_label}"
        mapping_sse_chunks: List[str] = [
            _map_fields_progress_sse(
                map_phase_label,
                "Reviewing source and destination fields...",
            ),
        ]

        try:
            from services.mapping.service import execute_mapping

            snap = session.classification_snapshot or {}
            dest_spreadsheet_id = snap.get("destination_spreadsheet_id")
            gs_connection_id = snap.get("google_sheets_connection_id")
            dest_connector_id = (
                snap.get("destination_connector_id")
                or args.get("destination_connector_id")
            )
            if destination in ("google_sheets", "gsheets"):
                dest_connector_id = dest_spreadsheet_id or args.get("spreadsheet_id")
            result = await execute_mapping(
                user_id=session.user_id,
                destination=destination,
                destination_name=destination_name,
                flat_data=filtered_flat,
                extraction_row=row,
                mapping_instruction=mapping_context_summary,
                target_resolution=target_resolution,
                destination_connector_id=str(dest_connector_id)
                if dest_connector_id
                else None,
                is_sql_data=(row.get("source_type") or "").lower() == "sql",
                classification_snapshot={
                    **snap,
                    **({"destination_spreadsheet_id": dest_spreadsheet_id} if dest_spreadsheet_id else {}),
                    **({"google_sheets_connection_id": gs_connection_id} if gs_connection_id else {}),
                },
            )
        except Exception as exc:
            from utils.mapping_user_messages import is_mapping_destination_clarification

            msg = str(exc)
            return ToolResult(
                ok=False,
                message=msg,
                needs_clarification=is_mapping_destination_clarification(msg),
            )

        if mapping_config_is_empty(result.get("mapping")):
            log_map_flow(
                "map_fields_abort",
                reason="empty_mapping_after_validation",
                job_id=job_id,
                target=result.get("destination_object_api_name"),
            )
            return ToolResult(
                ok=False,
                message=(
                    "No field mappings could be validated for the pinned destination entity. "
                    "Check that source columns match the destination schema, or adjust the plan."
                ),
                needs_clarification=True,
            )

        mapping_id = f"mapping-{int(time.time() * 1000)}"
        src_type = result.get("source_document_type") or row.get("document_type") or ""
        src_desc = result.get("source_document_description") or row.get("document_description") or ""
        mapping_audit = result.get("mapping_audit") if isinstance(result, dict) else None
        from utils.mapping_audit import migration_allowed_by_audit

        allow_auto = migration_allowed_by_audit(mapping_audit)
        visible: Dict[str, Any] = {
            "action": "map_fields_ready",
            "mapping_id": mapping_id,
            "job_id": job_id,
            "destination": destination,
            "destination_name": destination_name,
            "mapping_instruction": mapping_context_summary,
            "mapping_audit": mapping_audit,
            "execute_enabled": allow_auto,
            "document_type": result.get("destination_document_type") or result.get("document_type") or src_type,
            "document_description": result.get("destination_document_description")
            or result.get("document_description")
            or src_desc,
            "source_document_type": src_type,
            "source_document_description": src_desc,
            "destination_document_type": result.get("destination_document_type") or result.get("document_type"),
            "destination_document_description": result.get("destination_document_description")
            or result.get("document_description"),
            "destination_object_api_name": result.get("destination_object_api_name"),
            "mapping_plan": result.get("mapping_plan"),
            "mapping_config": result.get("mapping") or {},
            "mapping": result,
            "source_type": row.get("source_type"),
            "source_name": row.get("source_name"),
        }
        visible.update(_extraction_context_for_map_sse(row, session))
        if result.get("connector_id"):
            visible["destination_connector_id"] = result.get("connector_id")
        if result.get("primary_table"):
            visible["primary_table"] = result.get("primary_table")
        if result.get("token_id"):
            visible["destination_connector_id"] = result.get("token_id")
        if result.get("spreadsheet_id"):
            visible["spreadsheet_id"] = result.get("spreadsheet_id")
        if result.get("available_tables"):
            visible["available_tables"] = result.get("available_tables")
        if result.get("target_resolution_source"):
            visible["target_resolution_source"] = result.get("target_resolution_source")
        if result.get("available_objects"):
            visible["available_objects"] = result.get("available_objects")
        if result.get("source_fields"):
            visible["source_fields"] = result.get("source_fields")
        source_type_lower = (row.get("source_type") or "").lower()
        source_identifier = (row.get("source_identifier") or "").strip()
        if not visible.get("destination_connector_id") and source_identifier:
            if source_type_lower == "sql":
                visible["destination_connector_id"] = source_identifier
        if not visible.get("spreadsheet_id") and source_identifier:
            if source_type_lower in ("google_sheets", "gsheets"):
                visible["spreadsheet_id"] = source_identifier
        sheet_name_for_visible = None
        if destination in ("google_sheets", "gsheets"):
            avail = result.get("available_sheets") or {}
            if isinstance(avail, dict) and avail:
                snap = session.classification_snapshot or {}
                tab = (snap.get("destination_sheet_name") or "").strip().lower()
                if tab:
                    for name in avail.keys():
                        if str(name).strip().lower() == tab:
                            sheet_name_for_visible = name
                            break
                if not sheet_name_for_visible:
                    sheet_name_for_visible = next(iter(avail.keys()), None)
        if sheet_name_for_visible:
            visible["sheet_name"] = sheet_name_for_visible
        if not visible.get("primary_table"):
            inferred = _infer_sql_table_from_mapping(visible.get("mapping_config") or {})
            if inferred:
                visible["primary_table"] = inferred
        try:
            from services.pipeline_learning.service import PipelineLearningService

            if PipelineLearningService.enabled():
                mapping_cfg = result.get("mapping") or {}
                if isinstance(mapping_cfg, dict) and "mapping" in mapping_cfg:
                    mapping_cfg = mapping_cfg.get("mapping") or mapping_cfg
                PipelineLearningService.schedule_capture(
                    lambda: PipelineLearningService.capture_mapping_ai(
                        user_id=session.user_id,
                        conversation_id=session.conversation_id,
                        job_id=job_id,
                        mapping_config=visible.get("mapping_config") or {},
                        mapping_plan=result.get("mapping_plan"),
                        transformation_rules=result.get("transformation_rules"),
                        destination_object=result.get("destination_object_api_name"),
                        destination_type=destination,
                        source_type=row.get("source_type"),
                    )
                )
        except Exception:
            pass
        try:
            from services.mapping.persist import (
                document_type_for_persist,
                persist_mapping_execution_result,
            )

            mapping_cfg = visible.get("mapping_config") or {}
            if isinstance(mapping_cfg, dict) and mapping_cfg:
                saved = await persist_mapping_execution_result(
                    session,
                    mapping_id=mapping_id,
                    destination=destination,
                    destination_name=destination_name,
                    mapping_cfg=mapping_cfg,
                    result=result,
                    row=row,
                    document_type=document_type_for_persist(
                        row=row,
                        destination_entity=_destination_entity_from_session(session, args),
                        destination_document_type=str(
                            visible.get("destination_document_type")
                            or visible.get("document_type")
                            or ""
                        ),
                    ),
                    sheet_name=sheet_name_for_visible,
                    connector_id=visible.get("destination_connector_id"),
                    table_name=visible.get("primary_table"),
                )
                if saved:
                    visible["mapping_version"] = saved.mapping_version
        except Exception as exc:
            print(f"⚠️ persist_mapping_execution_result: {exc}")
        mapping_sse_chunks.append(
            _map_fields_progress_sse(map_phase_label, "Field mapping ready"),
        )
        return ToolResult(ok=True, user_visible=visible, sse_chunks=mapping_sse_chunks)


class StartMigrationTool(Tool):
    tool_id = "start_migration"
    description = "Prompt user to confirm migration with current mapping panel"
    allowed_modes = {"extract", "migrate", "map", "data_ops", "general"}

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        from agent.debug import log_agent_runtime
        from services.migration.migration_service import MigrationService
        from services.migration.session_migration import (
            enrich_migration_context,
            resolve_migration_context_for_execution,
        )

        job_id = args.get("job_id") or session.active_job_id
        if not job_id:
            log_agent_runtime("start_migration.no_job", {"prompt": (session.prompt or "")[:80]})
            return ToolResult(ok=False, message="No active extraction job for migration.")

        ctx = None
        if session.conversation_id:
            ctx = await resolve_migration_context_for_execution(
                user_id=session.user_id,
                conversation_id=session.conversation_id,
                job_id=str(job_id),
                session=session,
            )
        else:
            from services.migration.session_migration import resolve_migration_context_for_tool

            ctx = await resolve_migration_context_for_tool(session, str(job_id))

        if ctx:
            from services.planning.conversation_session import coerce_destination_type

            dest_arg = coerce_destination_type(
                args.get("destination") or session.destination,
                default="",
            )
            if dest_arg:
                ctx.destination = dest_arg
            if args.get("destination_name"):
                ctx.destination_name = args.get("destination_name")
            if ctx.destination == "sql" and (not ctx.connector_id or not ctx.table_name):
                ctx = await enrich_migration_context(ctx, session, str(job_id))
        has_mapping = bool(ctx and ctx.mapping)
        log_agent_runtime(
            "start_migration.resolve",
            {
                "job_id": job_id,
                "has_mapping": has_mapping,
                "mapping_id": ctx.mapping_id if ctx else None,
                "mapping_version": getattr(ctx, "mapping_version", None) if ctx else None,
                "mapping_field_count": len(ctx.mapping) if ctx else 0,
                "has_transformation_rules": bool(
                    ctx and ctx.transformation_rules and ctx.transformation_rules.get("fields")
                ),
                "destination": ctx.destination if ctx else session.destination,
                "destination_name": ctx.destination_name if ctx else session.destination_name,
                "connector_id": ctx.connector_id if ctx else None,
                "table_name": ctx.table_name if ctx else None,
                "source": "db_context" if ctx else "none",
            },
        )
        if not has_mapping or not ctx:
            return ToolResult(
                ok=True,
                user_visible={
                    "action": "migration_needs_mapping",
                    "job_id": job_id,
                    "destination": session.destination,
                    "destination_name": session.destination_name,
                    "message": "Complete field mapping in the panel above, then click **Start Migration**.",
                },
            )

        from utils.mapping_audit import migration_allowed_by_audit

        audit = (session.last_result or {}).get("mapping_audit") if session.last_result else None
        if not migration_allowed_by_audit(audit):
            return ToolResult(
                ok=True,
                user_visible={
                    "action": "migration_blocked_by_audit",
                    "job_id": job_id,
                    "mapping_audit": audit,
                    "message": (
                        "Mapping did not pass validation. Review the mapping panel "
                        "and click **Start Migration** when ready."
                    ),
                },
            )

        from services.migration.errors import MigrationServiceError
        from utils.migration_sse_stream import migration_started_extra_only, migration_started_sse

        try:
            result = await MigrationService.start(ctx, session=session)
            try:
                from services.extraction_results_service import ExtractionResultsService

                source_job = str(result.source_job_id or job_id)
                await ExtractionResultsService.update_pipeline_stage(
                    source_job,
                    session.user_id,
                    "migrating",
                )
            except Exception:
                pass
            sse = migration_started_sse(
                job_id=result.job_id,
                batch_id=result.batch_id,
                destination=result.destination,
                destination_name=result.destination_name,
                total_records=result.total_records,
                source_job_id=result.source_job_id,
                **migration_started_extra_only(result.extra),
            )
            visible: Dict[str, Any] = {
                "action": "migration_started",
                "job_id": result.job_id,
                "batch_id": result.batch_id,
                "source_job_id": result.source_job_id,
                "destination": result.destination,
                "destination_name": result.destination_name,
                "total_records": result.total_records,
                "record_count": result.total_records,
                "mapping_id": ctx.mapping_id,
            }
            if result.extra:
                for key in (
                    "destination_connector_id",
                    "destination_spreadsheet_id",
                    "spreadsheet_id",
                    "source_connector_id",
                    "source_type",
                    "extracted_schema_type",
                    "sql_query",
                    "table_name",
                ):
                    if result.extra.get(key) is not None:
                        visible[key] = result.extra[key]
            if ctx.connector_id and not visible.get("destination_connector_id"):
                visible["destination_connector_id"] = str(ctx.connector_id)
            if session.conversation_id:
                from services.conversation.session_sync import sync_session_after_migration

                await sync_session_after_migration(
                    session.user_id,
                    session.conversation_id,
                    job_id=str(result.source_job_id or job_id),
                )
            return ToolResult(
                ok=True,
                user_visible=visible,
                sse_chunks=[sse],
                background_job_id=result.job_id,
            )
        except MigrationServiceError as e:
            log_agent_runtime(
                "start_migration.failed",
                {"message": e.message, "status_code": e.status_code},
            )
            return ToolResult(ok=False, message=e.message)
