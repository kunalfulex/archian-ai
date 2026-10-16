"""Load pipeline facts from extraction job stores (not chat event replay)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PipelineStage = Literal["none", "extracted", "mapped", "migrating", "migrated"]
NextAction = Literal[
    "continue_map",
    "continue_migrate",
    "continue_map_migrate",
    "continue_data_ops",
]


class ResourcePin(BaseModel):
    connector_type: str = "unknown"
    connector_id: Optional[str] = None
    display_name: Optional[str] = None
    entity_key: Optional[str] = None


class JobContext(BaseModel):
    job_id: str
    pipeline_stage: PipelineStage = "none"
    last_completed_step: Literal["none", "extract", "map", "migrate"] = "none"
    next_logical_step: Literal["extract", "map", "migrate", "none"] = "none"
    mapping_ready: bool = False
    source: ResourcePin = Field(default_factory=ResourcePin)
    destination: Optional[ResourcePin] = None
    field_hints: List[str] = Field(default_factory=list)
    extract_field_scope: str = "explicit"
    include_related_data: bool = False
    parent_entity_key: Optional[str] = None
    parent_entity_label: Optional[str] = None
    related_field_hints: List[str] = Field(default_factory=list)
    related_entity_keys: List[str] = Field(default_factory=list)
    lookup_field_path: Optional[str] = None
    lookup_relationship_name: Optional[str] = None
    mapping_config: Dict[str, Any] = Field(default_factory=dict)
    data_ops_rules: Optional[Dict[str, Any]] = None
    record_count: Optional[int] = None
    conversation_id: Optional[str] = None
    destination_object_api_name: Optional[str] = None

    def s01_progress_dict(self) -> Dict[str, Any]:
        """Compact progress for S01 LLM (replaces chat event scan when available)."""
        extract_status = (
            "completed"
            if self.pipeline_stage in ("extracted", "mapped", "migrating", "migrated")
            else "not_started"
        )
        map_status = (
            "completed"
            if self.mapping_ready or self.pipeline_stage in ("mapped", "migrating", "migrated")
            else "not_started"
        )
        migrate_status = (
            "completed"
            if self.pipeline_stage == "migrated"
            else "in_progress"
            if self.pipeline_stage == "migrating"
            else "not_started"
        )
        return {
            "job_id": self.job_id,
            "last_completed_step": self.last_completed_step,
            "next_logical_step": self.next_logical_step,
            "mapping_ready": self.mapping_ready,
            "pipeline_stage": self.pipeline_stage,
            "record_count": self.record_count,
            "source": {
                "type": self.source.connector_type,
                "name": self.source.display_name,
                "entity_key": self.source.entity_key,
            },
            "destination": {
                "type": (self.destination.connector_type if self.destination else None),
                "name": (self.destination.display_name if self.destination else None),
                "entity_key": (
                    self.destination.entity_key
                    or self.destination_object_api_name
                    if self.destination
                    else self.destination_object_api_name
                ),
            }
            if self.destination or self.destination_object_api_name
            else None,
            "steps": {
                "extract": {"status": extract_status},
                "map": {"status": map_status},
                "migrate": {"status": migrate_status},
            },
        }

    def s01_job_summary(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_type": self.source.connector_type,
            "source_name": self.source.display_name,
            "destination": self.destination.connector_type if self.destination else None,
            "destination_name": self.destination.display_name if self.destination else None,
            "table_name": self.source.entity_key,
            "object_api_name": self.source.entity_key
            if self.source.connector_type == "salesforce"
            else None,
            "record_count": self.record_count,
            "pipeline_stage": self.pipeline_stage,
        }


def _normalize_connector_type(value: Optional[str]) -> str:
    from services.planning.schemas import normalize_connector_type

    return normalize_connector_type(value or "unknown")


def _entity_key_from_sources(
    *,
    result: Dict[str, Any],
    meta: Dict[str, Any],
    redis_job: Dict[str, Any],
) -> str:
    for source in (meta, redis_job, result):
        for key in (
            "source_entity_key",
            "table_name",
            "object_api_name",
            "sheet_name",
            "entity_key",
        ):
            val = source.get(key)
            if val and str(val).strip():
                return str(val).strip()
        raw_tables = source.get("table_names")
        if isinstance(raw_tables, list):
            for raw in raw_tables:
                val = str(raw or "").strip()
                if val:
                    return val
        contract = source.get("extract_contract")
        if isinstance(contract, dict):
            primary = (contract.get("primary_table") or "").strip()
            if primary:
                return primary
    doc = (result.get("document_type") or "").strip()
    if doc and doc.lower() not in ("unknown", "search results", "sql data"):
        return doc
    return ""


def _field_hints_from_sources(
    meta: Dict[str, Any],
    redis_job: Dict[str, Any],
) -> List[str]:
    hints: List[str] = []
    for source in (meta, redis_job):
        raw = source.get("field_hints") or source.get("extract_fields")
        if isinstance(raw, list):
            hints.extend(str(x).strip() for x in raw if str(x).strip())
        rules = source.get("extraction_rules")
        if isinstance(rules, dict):
            cols = rules.get("columns") or rules.get("fields")
            if isinstance(cols, list):
                hints.extend(str(x).strip() for x in cols if str(x).strip())
    return list(dict.fromkeys(hints))


def _related_metadata_from_sources(
    meta: Dict[str, Any],
    redis_job: Dict[str, Any],
) -> Dict[str, Any]:
    """Read related-entity extract pins from persisted extraction_metadata."""
    out: Dict[str, Any] = {}
    for source in (meta, redis_job):
        if source.get("include_related_data"):
            out["include_related_data"] = True
        for key in (
            "parent_entity_key",
            "parent_entity_label",
            "lookup_field_path",
            "lookup_relationship_name",
        ):
            val = source.get(key)
            if val and str(val).strip():
                out[key] = str(val).strip()
        related_keys: List[str] = []
        raw_keys = source.get("related_entity_keys")
        if isinstance(raw_keys, list):
            related_keys.extend(str(x).strip() for x in raw_keys if str(x).strip())
        parent = (source.get("parent_entity_key") or source.get("related_entity_key") or "").strip()
        if parent and parent not in related_keys:
            related_keys.append(parent)
        if related_keys:
            out["related_entity_keys"] = list(dict.fromkeys(related_keys))
        raw_hints = source.get("related_field_hints")
        if isinstance(raw_hints, list):
            hints = [str(x).strip() for x in raw_hints if str(x).strip()]
            if hints:
                out["related_field_hints"] = list(dict.fromkeys(hints))
    return out


_STAGE_ORDER: Dict[str, int] = {
    "none": 0,
    "extracted": 1,
    "mapped": 2,
    "migrating": 3,
    "migrated": 4,
}


def _promote_pipeline_stage(current: PipelineStage, candidate: PipelineStage) -> PipelineStage:
    if _STAGE_ORDER.get(candidate, 0) > _STAGE_ORDER.get(current, 0):
        return candidate
    return current


def _stage_from_progress(last_step: str, mapping_ready: bool) -> PipelineStage:
    if last_step == "migrate":
        return "migrated"
    if mapping_ready or last_step == "map":
        return "mapped"
    if last_step == "extract":
        return "extracted"
    return "none"


def _last_step_from_stage(stage: PipelineStage, mapping_ready: bool) -> str:
    if stage == "migrated":
        return "migrate"
    if stage == "mapped" or mapping_ready:
        return "map"
    if stage == "extracted":
        return "extract"
    return "none"


def _next_logical_step(last_step: str, mapping_ready: bool) -> str:
    if last_step == "migrate":
        return "none"
    if last_step == "map" or mapping_ready:
        return "migrate"
    if last_step == "extract":
        return "map"
    return "extract"


def _resolve_job_destination_entity(
    *,
    dest_type: str,
    mapping_config: Dict[str, Any],
    mig_ctx: Any,
    meta: Dict[str, Any],
    redis_job: Dict[str, Any],
    map_meta: Optional[Dict[str, Any]] = None,
    dest_pin: Optional[ResourcePin] = None,
) -> Optional[str]:
    """Prefer stored API names; never treat catalog document_type as entity_key."""
    from utils.migration_destination_entity import coerce_destination_entity_api_name

    candidates: List[Optional[str]] = []
    if mig_ctx:
        candidates.append(getattr(mig_ctx, "destination_object_api_name", None))
    if dest_pin and dest_pin.entity_key:
        candidates.append(dest_pin.entity_key)
    for source in (map_meta or {}, meta, redis_job):
        if not isinstance(source, dict):
            continue
        candidates.append(source.get("destination_object_api_name"))
        contract = source.get("plan_contract")
        if isinstance(contract, dict):
            dest_side = contract.get("destination")
            if isinstance(dest_side, dict):
                candidates.append(dest_side.get("entity_key"))

    for raw in candidates:
        resolved = coerce_destination_entity_api_name(
            raw,
            connector_type=dest_type,
            mapping=mapping_config,
        )
        if resolved:
            return resolved
    return coerce_destination_entity_api_name(
        None,
        connector_type=dest_type,
        mapping=mapping_config,
    )


async def load_job_context(
    *,
    user_id: str,
    job_id: str,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Optional[JobContext]:
    """
    Load facts for continuation planning from job stores.

    Chat history is used only as a one-time fallback for pipeline_stage when
    job rows lack mapping state (until pipeline_stage column exists).
    """
    jid = (job_id or "").strip()
    if not jid:
        return None

    from services.extraction_results_service import ExtractionResultsService

    result = await ExtractionResultsService.get_extraction_result(jid, str(user_id))
    if not result:
        return None

    meta = dict(result.get("extraction_metadata") or {})
    redis_job: Dict[str, Any] = {}
    try:
        from utils.job_stores import extraction_jobs

        redis_job = dict(await extraction_jobs.get(jid, {}) or {})
    except Exception as exc:
        logger.debug("Redis extraction_jobs miss for %s: %s", jid, exc)

    source_type = _normalize_connector_type(
        result.get("source_type") or redis_job.get("source_type") or meta.get("source_type")
    )
    entity_key = _entity_key_from_sources(result=result, meta=meta, redis_job=redis_job)
    source_pin = ResourcePin(
        connector_type=source_type,
        connector_id=(
            meta.get("source_connector_id")
            or redis_job.get("source_connector_id")
            or redis_job.get("connector_id")
            or (
                result.get("source_identifier")
                if source_type in ("salesforce", "sql")
                else None
            )
        ),
        display_name=(result.get("source_name") or redis_job.get("source_name") or meta.get("source_name")),
        entity_key=entity_key or None,
    )

    dest_type = _normalize_connector_type(
        meta.get("destination")
        or redis_job.get("destination")
        or redis_job.get("destination_type")
        or "unknown"
    )
    dest_name = meta.get("destination_name") or redis_job.get("destination_name")
    dest_pin: Optional[ResourcePin] = None
    if dest_type not in ("unknown", "") or dest_name:
        dest_pin = ResourcePin(
            connector_type=dest_type if dest_type not in ("unknown", "") else "salesforce",
            connector_id=(
                meta.get("destination_connector_id")
                or redis_job.get("destination_connector_id")
            ),
            display_name=dest_name,
            entity_key=(
                meta.get("destination_object_api_name")
                or redis_job.get("destination_object_api_name")
            ),
        )

    mapping_config: Dict[str, Any] = {}
    mapping_job_id: Optional[str] = None
    dest_object: Optional[str] = None
    conv_id = conversation_id or result.get("conversation_id")
    mig_ctx = None
    if conv_id:
        try:
            from services.migration.context_repository import get_context
            from services.migration.mapping_lineage import find_lineage_mapping_context

            mig_ctx = await get_context(
                user_id=str(user_id),
                conversation_id=str(conv_id),
                extraction_job_id=str(jid),
            )
            if mig_ctx and mig_ctx.mapping:
                mapping_config = dict(mig_ctx.mapping or {})
                mapping_job_id = str(jid)
            else:
                found = await find_lineage_mapping_context(
                    user_id=str(user_id),
                    conversation_id=str(conv_id),
                    target_job_id=str(jid),
                )
                if found:
                    mig_ctx, mapping_job_id = found[0], found[1]
                    mapping_config = dict(mig_ctx.mapping or {})
            if mig_ctx:
                if mig_ctx.destination:
                    dest_type = _normalize_connector_type(mig_ctx.destination)
                if mig_ctx.destination_name:
                    dest_name = mig_ctx.destination_name
                if mig_ctx.connector_id and dest_pin:
                    dest_pin.connector_id = mig_ctx.connector_id
                if dest_pin is None and mig_ctx.destination:
                    dest_pin = ResourcePin(
                        connector_type=dest_type,
                        connector_id=mig_ctx.connector_id,
                        display_name=dest_name,
                        entity_key=mig_ctx.destination_object_api_name or None,
                    )
        except Exception as exc:
            logger.debug("migration context load failed job=%s: %s", jid, exc)

    if result.get("has_mapping_transform") and not mapping_config:
        mapping_config = meta.get("mapping_config") if isinstance(meta.get("mapping_config"), dict) else {}

    from services.migration.mapping_readiness import mapping_ready_for_job

    mapping_ready = await mapping_ready_for_job(
        str(user_id),
        str(conv_id) if conv_id else None,
        str(jid),
        mapping_config_on_job=mapping_config if mapping_config else None,
    )
    if not mapping_ready and bool(result.get("has_mapping_transform")):
        mapping_ready = True
    stored_stage = (result.get("pipeline_stage") or "").strip().lower()
    pipeline_stage: PipelineStage = "extracted"
    if stored_stage in ("extracted", "mapped", "migrating", "migrated"):
        pipeline_stage = stored_stage  # type: ignore[assignment]
    elif mapping_ready:
        pipeline_stage = "mapped"

    last_step = _last_step_from_stage(pipeline_stage, mapping_ready)

    map_meta: Dict[str, Any] = {}
    if conversation_history:
        from services.planning.conversation_session import build_session_progress_summary

        progress = build_session_progress_summary(conversation_history, job_id=jid)
        chat_last = (progress.get("last_completed_step") or "none").lower()
        chat_mapping = bool(progress.get("mapping_ready"))
        chat_stage_raw = (progress.get("pipeline_stage") or "").strip().lower()
        if chat_stage_raw in ("extracted", "mapped", "migrating", "migrated"):
            pipeline_stage = _promote_pipeline_stage(
                pipeline_stage,
                chat_stage_raw,  # type: ignore[arg-type]
            )
        elif chat_last in ("extract", "map", "migrate"):
            pipeline_stage = _promote_pipeline_stage(
                pipeline_stage,
                _stage_from_progress(chat_last, chat_mapping or mapping_ready),
            )
        step_order = {"none": 0, "extract": 1, "map": 2, "migrate": 3}
        if chat_last in step_order and step_order.get(chat_last, 0) >= step_order.get(
            last_step, 0
        ):
            last_step = chat_last  # type: ignore[assignment]
        if chat_mapping and mapping_ready:
            pipeline_stage = _promote_pipeline_stage(
                pipeline_stage,
                _stage_from_progress(last_step, mapping_ready),
            )
        map_meta = (progress.get("steps") or {}).get("map") or {}
        if map_meta.get("destination") or map_meta.get("destination_name"):
            if not dest_pin and map_meta.get("destination"):
                dest_pin = ResourcePin(
                    connector_type=_normalize_connector_type(map_meta.get("destination")),
                    display_name=map_meta.get("destination_name"),
                    entity_key=map_meta.get("destination_object_api_name"),
                )
            if map_meta.get("destination_object_api_name"):
                dest_object = map_meta.get("destination_object_api_name")

    dest_object = _resolve_job_destination_entity(
        dest_type=dest_type,
        mapping_config=mapping_config,
        mig_ctx=mig_ctx,
        meta=meta,
        redis_job=redis_job,
        map_meta=map_meta,
        dest_pin=dest_pin,
    )
    if dest_object and dest_pin:
        dest_pin.entity_key = dest_object

    field_hints = _field_hints_from_sources(meta, redis_job)
    scope = str(meta.get("extract_field_scope") or redis_job.get("extract_field_scope") or "explicit")
    related_meta = _related_metadata_from_sources(meta, redis_job)

    return JobContext(
        job_id=jid,
        pipeline_stage=pipeline_stage,
        last_completed_step=last_step,  # type: ignore[arg-type]
        next_logical_step=_next_logical_step(last_step, mapping_ready),  # type: ignore[arg-type]
        mapping_ready=mapping_ready,
        source=source_pin,
        destination=dest_pin,
        field_hints=field_hints,
        extract_field_scope=scope,
        include_related_data=bool(related_meta.get("include_related_data")),
        parent_entity_key=related_meta.get("parent_entity_key"),
        parent_entity_label=related_meta.get("parent_entity_label"),
        related_field_hints=list(related_meta.get("related_field_hints") or []),
        related_entity_keys=list(related_meta.get("related_entity_keys") or []),
        lookup_field_path=related_meta.get("lookup_field_path"),
        lookup_relationship_name=related_meta.get("lookup_relationship_name"),
        mapping_config=mapping_config,
        data_ops_rules=meta.get("extraction_rules") if isinstance(meta.get("extraction_rules"), dict) else None,
        record_count=result.get("record_count"),
        conversation_id=str(conv_id) if conv_id else None,
        destination_object_api_name=dest_object,
    )
