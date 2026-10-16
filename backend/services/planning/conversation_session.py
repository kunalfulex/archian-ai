"""Extraction session and continuation helpers (extracted from classification_handler)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def _coerce_resource_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "id", "label", "value"):
            inner = value.get(key)
            if inner is not None and str(inner).strip():
                return str(inner).strip()
        return ""
    return str(value).strip()


def coerce_destination_type(value: Any, *, default: str = "salesforce") -> str:
    """
    Resolve a connector type string from legacy flat fields or IntentState ConnectorRef dicts.

    Used at migration/read boundaries so Redis intent_state and classification snapshots
    do not crash callers that expect ``"salesforce"`` / ``"sql"`` / ``"google_sheets"``.
    """
    if value is None:
        return default
    if isinstance(value, str):
        d = value.strip().lower()
        if not d or d == "unknown":
            return default
        if d == "gsheets":
            return "google_sheets"
        return d
    if isinstance(value, dict):
        for key in ("connector_type", "type", "active_destination", "destination_type"):
            inner = value.get(key)
            if inner is not None and str(inner).strip():
                return coerce_destination_type(str(inner).strip(), default=default)
        return default
    raw = str(value).strip().lower()
    if not raw or raw == "unknown":
        return default
    if raw == "gsheets":
        return "google_sheets"
    return raw


def is_unknown_resource(value: Any) -> bool:
    v = _coerce_resource_str(value).lower()
    return not v or v == "unknown"


def latest_extraction_completed_metadata(
    conversation_history: Optional[List[Dict]],
) -> Optional[Dict]:
    for msg in reversed(conversation_history or []):
        metadata = msg.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if metadata.get("event_type") == "extraction_completed":
            return metadata
    return None


def latest_mapping_started_metadata(
    conversation_history: Optional[List[Dict]],
    *,
    job_id: Optional[str] = None,
) -> Optional[Dict]:
    target = (job_id or "").strip()
    for msg in reversed(conversation_history or []):
        metadata = msg.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if metadata.get("event_type") != "mapping_started":
            continue
        if target and _event_job_id(metadata) != target:
            continue
        return metadata
    return None


def _message_metadata(msg: Dict[str, Any]) -> Dict[str, Any]:
    meta = msg.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _event_job_id(meta: Dict[str, Any]) -> Optional[str]:
    for key in ("job_id", "source_job_id", "active_job_id"):
        val = _coerce_resource_str(meta.get(key))
        if val:
            return str(val)
    return None


def _mapping_metadata_usable(meta: Dict[str, Any]) -> bool:
    cfg = meta.get("mapping_config")
    if isinstance(cfg, dict) and len(cfg) > 0:
        return True
    try:
        count = int(meta.get("field_mappings_count") or 0)
    except (TypeError, ValueError):
        count = 0
    return count > 0


def build_session_progress_summary(
    conversation_history: Optional[List[Dict]],
    *,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pipeline progress for the active extraction job derived from chat data events.

    Used by S01 session continuation so the LLM knows extract/map/migrate completion
    state — not just that an extraction job exists.
    """
    target_job = (job_id or "").strip()
    if not target_job:
        ext_meta = latest_extraction_completed_metadata(conversation_history)
        target_job = (_event_job_id(ext_meta) if ext_meta else "") or ""

    empty: Dict[str, Any] = {
        "job_id": target_job or None,
        "last_completed_step": "none",
        "next_logical_step": "extract",
        "mapping_ready": False,
        "steps": {
            "extract": {"status": "not_started"},
            "map": {"status": "not_started"},
            "migrate": {"status": "not_started"},
        },
    }
    if not target_job:
        return empty

    extract: Dict[str, Any] = {"status": "not_started"}
    map_step: Dict[str, Any] = {"status": "not_started"}
    migrate: Dict[str, Any] = {"status": "not_started"}

    for msg in conversation_history or []:
        meta = _message_metadata(msg)
        if _event_job_id(meta) != target_job:
            continue
        et = meta.get("event_type")
        if et == "extraction_completed":
            extract = {
                "status": "completed",
                "record_count": meta.get("record_count"),
                "table_name": meta.get("table_name") or meta.get("object_api_name"),
                "source_name": meta.get("source_name"),
            }
        elif et == "mapping_started":
            field_count = len(meta.get("mapping_config") or {}) if isinstance(
                meta.get("mapping_config"), dict
            ) else 0
            map_details = {
                "destination": meta.get("destination"),
                "destination_name": meta.get("destination_name"),
                "destination_object_api_name": meta.get("destination_object_api_name"),
                "mapping_id": meta.get("mapping_id"),
                "field_count": field_count or meta.get("field_mappings_count"),
            }
            if _mapping_metadata_usable(meta):
                map_step = {**map_details, "status": "completed"}
            else:
                map_step = {**map_details, "status": "in_progress"}
        elif et == "mapping_completed":
            map_step = {
                "status": "completed",
                "destination": meta.get("destination"),
                "destination_name": meta.get("destination_name"),
                "destination_object_api_name": meta.get("destination_object_api_name"),
                "field_count": meta.get("field_mappings_count"),
            }
        elif et == "migration_started":
            migrate = {
                "status": "in_progress",
                "record_count": meta.get("record_count"),
                "batch_id": meta.get("batch_id"),
            }
        elif et == "migration_completed":
            raw_status = (meta.get("status") or "completed").strip().lower()
            migrate = {
                "status": "completed"
                if raw_status in ("success", "completed")
                else "failed",
                "success_count": meta.get("success_count"),
                "error_count": meta.get("error_count"),
            }

    mapping_ready = map_step.get("status") == "completed"
    last_completed = "none"
    if migrate.get("status") == "completed":
        last_completed = "migrate"
    elif mapping_ready:
        last_completed = "map"
    elif extract.get("status") == "completed":
        last_completed = "extract"

    if last_completed == "none":
        next_logical = "extract"
        pipeline_stage = "none"
    elif last_completed == "extract":
        next_logical = "map"
        pipeline_stage = "extracted"
    elif last_completed == "map":
        next_logical = "migrate"
        pipeline_stage = "mapped"
    else:
        next_logical = "none"
        pipeline_stage = "migrated"

    return {
        "job_id": target_job,
        "last_completed_step": last_completed,
        "next_logical_step": next_logical,
        "mapping_ready": mapping_ready,
        "pipeline_stage": pipeline_stage,
        "steps": {
            "extract": extract,
            "map": map_step,
            "migrate": migrate,
        },
    }


def intent_state_from_extraction_metadata(metadata: Dict) -> Optional[Dict]:
    if not isinstance(metadata, dict):
        return None
    existing = metadata.get("intent_state")
    if isinstance(existing, dict) and existing.get("is_active"):
        return existing
    job_id = metadata.get("job_id")
    if not job_id:
        return None
    source_type = metadata.get("source_type") or "unknown"
    if source_type == "gsheets":
        source_type = "google_sheets"
    destination = metadata.get("destination") or "unknown"
    now = datetime.now(timezone.utc)
    return {
        "active_intent": "migrate" if destination not in (None, "", "unknown") else "extract",
        "active_source": source_type,
        "active_destination": destination,
        "source_name": metadata.get("source_name"),
        "destination_name": metadata.get("destination_name"),
        "job_id": job_id,
        "active_job_id": job_id,
        "state": "extraction_completed",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(),
        "is_active": True,
        "extraction_rules": metadata.get("extraction_rules"),
        "active_version": metadata.get("extraction_version"),
    }


def latest_extraction_session_for_classifier(
    conversation_history: Optional[List[Dict]],
) -> Optional[Dict]:
    meta = latest_extraction_completed_metadata(conversation_history)
    if not meta:
        return None
    session = intent_state_from_extraction_metadata(meta) or {}
    session = dict(session)
    src = meta.get("source_type") or meta.get("source")
    dest = meta.get("destination")
    if src and not is_unknown_resource(src):
        session["active_source"] = src
    if dest and not is_unknown_resource(dest):
        session["active_destination"] = dest
    if meta.get("source_name"):
        session["source_name"] = meta.get("source_name")
    if meta.get("destination_name"):
        session["destination_name"] = meta.get("destination_name")
    if meta.get("job_id"):
        session["job_id"] = meta.get("job_id")
        session["active_job_id"] = meta.get("job_id")
    session["state"] = "extraction_completed"
    return session


def _continuation_intent_for_session_inherit(intent: str, mode: Optional[str]) -> bool:
    i = (intent or "").lower()
    m = (mode or "").lower()
    if i in (
        "map_data",
        "migrate_data",
        "migrate",
        "multi",
        "transform",
        "filter",
        "dedupe",
        "deduplication",
        "validate",
        "validation",
        "cleanup",
    ):
        return True
    if m in ("map", "migrate", "data_ops"):
        return True
    return False


def inherit_session_resources_from_intent_state(
    classification: dict,
    intent_state: Optional[dict],
    *,
    conversation_history: Optional[List[Dict]] = None,
    prompt: Optional[str] = None,
) -> dict:
    if not intent_state and not conversation_history:
        return dict(classification or {})

    out = dict(classification or {})
    session_action = (out.get("session_action") or "").strip()
    if session_action != "continue_active_job" or not out.get("active_job_id"):
        return out

    intent = (out.get("intent") or "").lower()
    mode = out.get("mode")
    if not _continuation_intent_for_session_inherit(intent, mode):
        return out

    state = (intent_state or {}).get("state")
    if state and state not in ("extraction_completed", "mapping_started"):
        return out

    meta = latest_extraction_completed_metadata(conversation_history) or {}
    prefer_meta = intent in ("map_data", "migrate_data", "migrate") or (mode or "").lower() in (
        "map",
        "migrate",
    )

    def _pick(*keys: str) -> Optional[str]:
        for key in keys:
            sources = (meta, intent_state) if prefer_meta else (intent_state, meta)
            for src in sources:
                if not src or not isinstance(src, dict):
                    continue
                val = src.get(key)
                if val not in (None, ""):
                    return val
        return None

    if is_unknown_resource(out.get("source")):
        src = _pick("active_source", "source", "source_type")
        if src and not is_unknown_resource(src):
            out["source"] = src
    if not out.get("source_name"):
        sn = _pick("source_name")
        if sn:
            out["source_name"] = sn

    if is_unknown_resource(out.get("destination")):
        dest = _pick("active_destination", "destination")
        if dest and not is_unknown_resource(dest):
            out["destination"] = dest
    if not out.get("destination_name") or is_unknown_resource(out.get("destination_name")):
        dn = _pick("destination_name")
        if dn and not is_unknown_resource(dn):
            out["destination_name"] = dn

    if is_unknown_resource(out.get("destination_name")):
        map_meta = latest_mapping_started_metadata(conversation_history)
        if map_meta and map_meta.get("destination_name"):
            out["destination_name"] = map_meta["destination_name"]
            if map_meta.get("destination"):
                out["destination"] = map_meta["destination"]
        else:
            src_name = out.get("source_name")
            if (
                src_name
                and str(out.get("source") or "").lower() == "salesforce"
                and str(out.get("destination") or "").lower() == "salesforce"
            ):
                out["destination_name"] = src_name

    job_id = _pick("job_id", "active_job_id")
    if job_id:
        out["active_job_id"] = job_id

    from services.planning.schemas import normalize_classification_connectors

    return normalize_classification_connectors(out)
