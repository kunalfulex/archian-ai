"""Resolve durable save-flow payload from session + chat + extraction lineage."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from db.extracted_schema import SaveMappingFlowRequest
from utils.mapping_utils import sanitize_mapping_for_save


def _normalize_source_type(raw: Optional[str]) -> str:
    s = (raw or "").strip().lower()
    if s in ("google_sheets", "gsheets"):
        return "gsheets"
    if s in ("sql", "salesforce", "file"):
        return s
    return s or "file"


def _normalize_destination(raw: Optional[str]) -> str:
    d = (raw or "salesforce").strip().lower()
    if d in ("gsheets", "google_sheets"):
        return "google_sheets"
    return d or "salesforce"


def _event_job_id(meta: Dict[str, Any]) -> Optional[str]:
    for key in ("job_id", "source_job_id", "active_job_id"):
        val = (meta.get(key) or "").strip()
        if val:
            return str(val)
    return None


def _sql_from_meta(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    for key in ("sql_query", "query", "soql", "SOQL"):
        val = meta.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _extraction_version(meta: Dict[str, Any]) -> int:
    try:
        return int(meta.get("extraction_version") or 1)
    except (TypeError, ValueError):
        return 1


def _parse_row_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = row.get("extraction_metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    return meta if isinstance(meta, dict) else {}


def _is_extraction_event(meta: Dict[str, Any]) -> bool:
    return meta.get("event_type") == "extraction_completed"


def _entry_matches_job(meta: Dict[str, Any], anchor: str, lineage: List[str]) -> bool:
    if not anchor:
        return False
    jid = _event_job_id(meta)
    if jid and jid == anchor:
        return True
    if jid and jid in lineage:
        return True
    parent = (meta.get("parent_job_id") or "").strip()
    if parent and parent in lineage:
        return True
    return False


def find_best_extraction_completed(
    history: List[Dict[str, Any]],
    records_job_id: str,
    lineage: List[str],
) -> Optional[Dict[str, Any]]:
    related = find_all_extraction_completed(history, records_job_id, lineage)
    if not related:
        return None
    return related[0]


def find_all_extraction_completed(
    history: List[Dict[str, Any]],
    records_job_id: str,
    lineage: List[str],
) -> List[Dict[str, Any]]:
    """All extraction_completed events for lineage, highest extraction_version first."""
    related: List[Tuple[int, Dict[str, Any]]] = []
    for msg in history or []:
        meta = msg.get("metadata")
        if not isinstance(meta, dict) or not _is_extraction_event(meta):
            continue
        if _entry_matches_job(meta, records_job_id, lineage):
            related.append((_extraction_version(meta), meta))
    if not related:
        return []
    related.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in related]


def find_best_migration_completed(
    history: List[Dict[str, Any]],
    *,
    source_job_id: Optional[str] = None,
    batch_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for msg in history or []:
        meta = msg.get("metadata")
        if not isinstance(meta, dict) or meta.get("event_type") != "migration_completed":
            continue
        if source_job_id and (
            meta.get("source_job_id") == source_job_id
            or meta.get("job_id") == source_job_id
        ):
            matches.append(meta)
            continue
        if batch_id and (
            meta.get("batch_id") == batch_id or meta.get("job_id") == batch_id
        ):
            matches.append(meta)
    if not matches:
        return None

    def score(m: Dict[str, Any]) -> int:
        s = 0
        if m.get("destination_connector_id"):
            s += 4
        if m.get("spreadsheet_id") or m.get("destination_spreadsheet_id"):
            s += 4
        if isinstance(m.get("mapping_config"), dict) and m["mapping_config"]:
            s += 3
        if m.get("source_job_id"):
            s += 2
        if m.get("sql_query") or _sql_from_meta(m):
            s += 1
        return s

    matches.sort(key=score, reverse=True)
    return matches[0]


def find_mapping_started_for_job(
    history: List[Dict[str, Any]],
    records_job_id: str,
    lineage: List[str],
) -> Optional[Dict[str, Any]]:
    for msg in reversed(history or []):
        meta = msg.get("metadata")
        if not isinstance(meta, dict) or meta.get("event_type") != "mapping_started":
            continue
        if _entry_matches_job(meta, records_job_id, lineage):
            return meta
    return None


async def resolve_save_flow_records_job_id(
    *,
    user_id: str,
    conversation_id: str,
    job_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    session_active_job_id: Optional[str] = None,
) -> str:
    """
    Resolve extraction job id for save-flow from durable facts.

    Order: explicit job_id → session active_job_id → batch_id/migration_completed
    → latest mapping_started → latest extraction_completed.
    """
    explicit = (job_id or "").strip()
    if explicit:
        return explicit

    session_job = (session_active_job_id or "").strip()
    if session_job:
        return session_job

    from services.chat_service import ChatService
    from services.planning.conversation_session import latest_extraction_completed_metadata

    history = await ChatService.get_messages_with_metadata_for_context(
        str(conversation_id), str(user_id), limit=50
    )

    batch = (batch_id or "").strip()
    if batch:
        mig = find_best_migration_completed(history, batch_id=batch)
        if mig:
            for key in ("source_job_id", "records_job_id"):
                val = (mig.get(key) or "").strip()
                if val:
                    return val

    for msg in reversed(history or []):
        meta = msg.get("metadata")
        if not isinstance(meta, dict) or meta.get("event_type") != "migration_completed":
            continue
        src = (meta.get("source_job_id") or "").strip()
        if src:
            return src

    for msg in reversed(history or []):
        meta = msg.get("metadata")
        if not isinstance(meta, dict) or meta.get("event_type") != "mapping_started":
            continue
        jid = _event_job_id(meta)
        if jid:
            return jid

    ext = latest_extraction_completed_metadata(history)
    if ext:
        jid = _event_job_id(ext)
        if jid:
            return jid

    raise ValueError("No active extraction job for save flow.")


async def _resolve_sf_token_id(
    user_id: str,
    *,
    token_id: Optional[str] = None,
    org_id: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Optional[str]:
    from utils.destination_connector_resolve import resolve_salesforce_token_record

    if token_id:
        rec = await resolve_salesforce_token_record(user_id, token_id=str(token_id))
        if rec and rec.get("id"):
            return str(rec["id"])
    if display_name and str(display_name).strip().lower() not in ("", "unknown"):
        rec = await resolve_salesforce_token_record(
            user_id, destination_name=str(display_name).strip()
        )
        if rec and rec.get("id"):
            return str(rec["id"])
    if org_id:
        from services.connectors.salesforce_ref import resolve_salesforce_token

        tok = await resolve_salesforce_token(str(user_id), str(org_id))
        if tok and tok.get("connector_id"):
            return str(tok["connector_id"])
    return None


async def _walk_lineage_for_sql(
    user_id: str,
    lineage: List[str],
    history: List[Dict[str, Any]],
) -> Optional[str]:
    from services.extraction_results_service import ExtractionResultsService

    for jid in reversed(lineage):
        row = await ExtractionResultsService.get_extraction_result(
            jid, user_id, prefer_lineage_latest=False
        )
        if row:
            q = _sql_from_meta(_parse_row_metadata(row))
            if q:
                return q
        for msg in history or []:
            meta = msg.get("metadata")
            if not isinstance(meta, dict):
                continue
            if _is_extraction_event(meta) and _event_job_id(meta) == jid:
                q = _sql_from_meta(meta)
                if q:
                    return q
    return None


@dataclass
class FlowSaveContext:
    records_job_id: str
    mapping: Dict[str, Any]
    document_type: str
    user_prompt: str
    extracted_schema_type: str
    source_connector_id: Optional[str] = None
    destination_id: Optional[str] = None
    destination: str = "salesforce"
    destination_name: Optional[str] = None
    sql_query: Optional[str] = None
    table_name: Optional[str] = None
    file_key: Optional[str] = None
    openai_file_id: Optional[str] = None
    source_name: Optional[str] = None
    migration_rules: Dict[str, Any] = field(default_factory=dict)
    extracted_schema: Dict[str, Any] = field(default_factory=dict)
    all_records: List[Dict[str, Any]] = field(default_factory=list)

    def to_save_request(self, flow_name: str) -> SaveMappingFlowRequest:
        return SaveMappingFlowRequest(
            flow_name=flow_name,
            document_type=self.document_type,
            file_key=self.file_key,
            openai_file_id=self.openai_file_id,
            extracted_schema=self.extracted_schema,
            all_records=self.all_records,
            mapping=self.mapping,
            user_prompt=self.user_prompt,
            extracted_schema_type=self.extracted_schema_type,
            connector_id=self.source_connector_id,
            sql_query=self.sql_query,
            table_name=self.table_name,
            destination=self.destination,
            destination_name=self.destination_name,
            destination_id=self.destination_id,
            migration_rules=self.migration_rules or None,
        )


async def _walk_lineage_row_metas(
    user_id: str,
    lineage: List[str],
) -> List[Dict[str, Any]]:
    from services.extraction_results_service import ExtractionResultsService

    metas: List[Dict[str, Any]] = []
    for jid in lineage:
        row = await ExtractionResultsService.get_extraction_result(
            jid, user_id, prefer_lineage_latest=False
        )
        if row:
            metas.append(_parse_row_metadata(row))
    return metas


async def resolve_flow_save_context(
    *,
    user_id: str,
    conversation_id: str,
    records_job_id: str,
    batch_id: Optional[str] = None,
    mapping_draft: Optional[Dict[str, Any]] = None,
) -> FlowSaveContext:
    """
    Build save-flow context from chat metadata, extraction lineage, and migration context.

    ``records_job_id`` is the extraction job whose rows + mapping should be persisted
    (usually session.active_job_id — may be a post-extract child job).
    """
    from services.chat_service import ChatService
    from services.extraction_results_service import ExtractionResultsService
    from services.migration.mapping_lineage import find_lineage_mapping_context

    job_id = str(records_job_id).strip()
    if not job_id:
        raise ValueError("No extraction job for save flow.")

    history = await ChatService.get_messages_with_metadata_for_context(
        str(conversation_id), str(user_id), limit=50
    )
    lineage = await ExtractionResultsService.get_lineage_job_ids(job_id, str(user_id))
    if not lineage:
        lineage = [job_id]

    row = await ExtractionResultsService.get_extraction_result(
        job_id, str(user_id), prefer_lineage_latest=False
    )
    if not row:
        raise ValueError(f"Extraction job {job_id} not found.")

    mapping_ctx = await find_lineage_mapping_context(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        target_job_id=job_id,
    )
    if not mapping_ctx or not mapping_ctx[0].mapping:
        raise ValueError("No mapping available to save.")
    ctx_mapping, _mapping_job = mapping_ctx

    ext_events = find_all_extraction_completed(history, job_id, lineage)
    ext_evt = ext_events[0] if ext_events else None
    map_evt = find_mapping_started_for_job(history, job_id, lineage)
    mig_evt = find_best_migration_completed(
        history, source_job_id=job_id, batch_id=batch_id
    )
    lineage_metas = await _walk_lineage_row_metas(str(user_id), lineage)

    source_type = _normalize_source_type(
        (mig_evt or {}).get("source_type")
        or (ext_evt or {}).get("source_type")
        or row.get("source_type")
        or (map_evt or {}).get("source_type")
    )
    extracted_schema_type = _normalize_source_type(
        (mig_evt or {}).get("extracted_schema_type")
        or (ext_evt or {}).get("extracted_schema_type")
        or source_type
    )

    destination = _normalize_destination(
        (mig_evt or {}).get("destination")
        or (map_evt or {}).get("destination")
        or ctx_mapping.destination
    )
    destination_name = (
        (mig_evt or {}).get("destination_name")
        or (map_evt or {}).get("destination_name")
        or ctx_mapping.destination_name
    )

    sql_query = (
        _sql_from_meta(mig_evt)
        or _sql_from_meta(map_evt)
        or _sql_from_meta(ext_evt)
        or ctx_mapping.sql_query
    )
    if not sql_query:
        sql_query = await _walk_lineage_for_sql(str(user_id), lineage, history)

    row_meta = _parse_row_metadata(row)
    table_name = (
        ctx_mapping.table_name
        or (mig_evt or {}).get("table_name")
        or (map_evt or {}).get("table_name")
        or (ext_evt or {}).get("table_name")
        or (ext_evt or {}).get("object_api_name")
        or row_meta.get("object_api_name")
        or row_meta.get("table_name")
        or row_meta.get("source_table")
    )
    if not table_name and source_type == "sql":
        table_name = row.get("source_identifier")
    if not table_name and source_type == "salesforce":
        table_name = row_meta.get("object_api_name") or row_meta.get("source_entity_key")

    source_name = (
        row.get("source_name")
        or (ext_evt or {}).get("source_name")
        or (map_evt or {}).get("source_name")
        or (mig_evt or {}).get("source_name")
    )

    source_connector_id = (
        (mig_evt or {}).get("source_connector_id")
        or (ext_evt or {}).get("source_connector_id")
        or (ext_evt or {}).get("connector_id")
        or (map_evt or {}).get("source_connector_id")
        or ctx_mapping.source_connector_id
    )

    if source_type == "salesforce":
        source_connector_id = await _resolve_sf_token_id(
            str(user_id),
            token_id=source_connector_id if source_connector_id else None,
            org_id=row.get("source_identifier"),
            display_name=source_name,
        )
    elif source_type == "sql":
        sid = source_connector_id or row.get("source_identifier")
        if sid:
            source_connector_id = str(sid)
    elif source_type == "gsheets":
        sid = (
            source_connector_id
            or row.get("source_identifier")
            or row_meta.get("spreadsheet_id")
        )
        if sid:
            source_connector_id = str(sid)

    destination_id: Optional[str] = None
    if destination == "salesforce":
        destination_id = (
            (mig_evt or {}).get("destination_connector_id")
            or (map_evt or {}).get("destination_connector_id")
        )
        if not destination_id and destination_name:
            destination_id = await _resolve_sf_token_id(
                str(user_id), display_name=destination_name
            )
    elif destination == "google_sheets":
        destination_id = (
            (mig_evt or {}).get("destination_spreadsheet_id")
            or (mig_evt or {}).get("spreadsheet_id")
            or (map_evt or {}).get("destination_spreadsheet_id")
            or (map_evt or {}).get("spreadsheet_id")
            or ctx_mapping.spreadsheet_id
        )
        if destination_id:
            destination_id = str(destination_id)
    elif destination == "sql":
        destination_id = (
            (mig_evt or {}).get("destination_connector_id")
            or (map_evt or {}).get("destination_connector_id")
            or ctx_mapping.connector_id
        )
        if destination_id:
            destination_id = str(destination_id)

    file_key = (
        row_meta.get("file_key")
        or (ext_evt or {}).get("file_key")
        or (mig_evt or {}).get("file_key")
    )

    extracted = row.get("extracted_data") or []
    if isinstance(extracted, str):
        try:
            extracted = json.loads(extracted)
        except json.JSONDecodeError:
            extracted = []
    all_records = extracted if isinstance(extracted, list) else []
    first_record = all_records[0] if all_records and isinstance(all_records[0], dict) else {}

    from services.flow.migration_rules_resolve import (
        build_flow_migration_rules,
        collect_extraction_rule_candidates,
        merge_richest_extraction_rules,
    )

    rule_candidates = collect_extraction_rule_candidates(
        row_meta=row_meta,
        all_records=all_records,
        extraction_events=ext_events,
        migration_event=mig_evt,
        ctx_migration_rules=ctx_mapping.migration_rules,
        lineage_row_metas=lineage_metas,
    )
    extraction_rules = merge_richest_extraction_rules(rule_candidates)
    migration_rules = build_flow_migration_rules(
        ctx_migration_rules=ctx_mapping.migration_rules,
        ctx_transformation_rules=ctx_mapping.transformation_rules,
        extraction_rules=extraction_rules or None,
        map_event=map_evt,
        mapping_draft=mapping_draft,
        migration_event=mig_evt,
        all_records=all_records,
        source_type=source_type,
    )

    user_prompt = (
        row.get("extraction_goals")
        or row.get("mapping_context_summary")
        or (ext_evt or {}).get("user_prompt")
        or ""
    )

    document_type = str(
        ctx_mapping.document_type
        or row.get("document_type")
        or (ext_evt or {}).get("document_type")
        or ""
    )

    return FlowSaveContext(
        records_job_id=job_id,
        mapping=sanitize_mapping_for_save(dict(ctx_mapping.mapping)),
        document_type=document_type,
        user_prompt=str(user_prompt),
        extracted_schema_type=extracted_schema_type,
        source_connector_id=source_connector_id,
        destination_id=destination_id,
        destination=destination,
        destination_name=destination_name,
        sql_query=sql_query,
        table_name=table_name,
        file_key=str(file_key) if file_key else None,
        source_name=source_name,
        migration_rules=migration_rules,
        extracted_schema=first_record,
        all_records=all_records,
    )
