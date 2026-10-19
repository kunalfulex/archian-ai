"""Stage chat-attached files for planner clarify / approve resume (no bytes in SSE)."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional

from services.file_extraction.upload import upload_file_bytes_to_s3
from services.file_store import get_bytes

if TYPE_CHECKING:
    from services.intent.session_continuation import SessionContinuationDecision


def is_pinned_file_upload(classification: Optional[Dict[str, Any]]) -> bool:
    """True when an attached chat file is the extract source (not a connector picker)."""
    if not classification or not isinstance(classification, dict):
        return False
    if not classification.get("_file_upload_pinned"):
        return False
    from services.planning.connector_guards import canonicalize_connector

    return canonicalize_connector(classification.get("source")) == "file"


def is_file_source(
    classification: Optional[Dict[str, Any]],
    job: Optional[Any] = None,
) -> bool:
    """True when the extract source is an uploaded file (pinned or explicit source=file)."""
    if job is not None:
        try:
            from services.intent.job_context import JobContext

            if isinstance(job, JobContext):
                st = (job.source.connector_type or "").strip().lower()
                if st == "file":
                    return True
        except Exception:
            pass
    if not classification or not isinstance(classification, dict):
        return False
    if is_pinned_file_upload(classification):
        return True
    from services.planning.connector_guards import canonicalize_connector

    if canonicalize_connector(classification.get("source")) != "file":
        return False
    return bool((classification.get("source_name") or "").strip())


def file_source_awaiting_extract(classification: Optional[Dict[str, Any]]) -> bool:
    """File is the source but structured extract has not completed yet (no active_job_id)."""
    return is_file_source(classification) and not (classification or {}).get("active_job_id")


def attached_file_forces_fresh_session(explicit_source: Optional[str]) -> bool:
    """True when the architect request includes a new in-chat file upload."""
    return str(explicit_source or "").strip().lower() == "file"


def fresh_session_decision_for_attached_file() -> "SessionContinuationDecision":
    """
    Attached file always starts a new extraction job (filename match ignored).

    Uses fresh_extract initially; classify_and_enrich promotes to fresh_full_pipeline
    when the classifier signals migrate/map to a named destination.
    """
    from services.intent.session_continuation import SessionContinuationDecision

    return SessionContinuationDecision(
        session_action="fresh_extract",
        continuation_phase="none",
        reuse_job_id=False,
        confidence=1.0,
        decision_source="file_upload_guardrail",
        reasoning_short="New file attached — start fresh extraction job",
    )


def promote_file_upload_session_from_classification(
    decision: "SessionContinuationDecision",
    classification: Dict[str, Any],
) -> "SessionContinuationDecision":
    """Promote fresh_extract → fresh_full_pipeline when classifier signals load/migrate."""
    from services.intent.session_continuation import SessionContinuationDecision

    if decision.session_action != "fresh_extract":
        return decision
    intent = (classification.get("intent") or "").lower()
    mode = (classification.get("mode") or "").lower()
    dest = (classification.get("destination") or "").strip().lower()
    dest_name = (classification.get("destination_name") or "").strip()
    wants_load = intent in ("migrate", "migrate_data", "map_data") or mode in (
        "migrate",
        "map",
    )
    has_dest = bool(dest_name) or (dest and dest not in ("unknown", ""))
    if not wants_load or not has_dest:
        return decision
    return decision.model_copy(
        update={
            "session_action": "fresh_full_pipeline",
            "reasoning_short": (
                decision.reasoning_short or ""
            ) + " [classifier migrate/load → fresh_full_pipeline]",
        }
    )


def pin_file_upload_on_classification(
    classification: Dict[str, Any],
    *,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Bind an in-chat file upload as the extract source before planner / gap detection.

    Must run before apply_post_classification_pipeline so pick_source_connector is not emitted.
    """
    out = dict(classification or {})
    name = (filename or out.get("source_name") or "").strip() or "upload"
    out["source"] = "file"
    out["source_name"] = name
    out["_file_upload_pinned"] = True
    out["_source_endpoint_clarified"] = True
    out["_endpoints_resolved"] = True
    out["planner_source_entity_confirmed"] = True

    def _ctype(clar: Any) -> str:
        if isinstance(clar, dict):
            return str(clar.get("clarification_type") or "")
        return str(getattr(clar, "clarification_type", "") or "")

    clarifications = out.get("clarifications") or []
    if clarifications:
        filtered = [
            c
            for c in clarifications
            if _ctype(c) not in ("pick_source_connector", "pick_source_entity")
        ]
        if len(filtered) != len(clarifications):
            out["clarifications"] = filtered
            if not filtered:
                out["needs_clarification"] = False

    ep = out.get("execution_plan")
    if isinstance(ep, dict) and ep.get("clarifications"):
        ep = dict(ep)
        ep["clarifications"] = [
            c
            for c in ep["clarifications"]
            if not (
                isinstance(c, dict)
                and c.get("clarification_type")
                in ("pick_source_connector", "pick_source_entity")
            )
        ]
        if not ep["clarifications"]:
            ep.pop("clarifications", None)
        out["execution_plan"] = ep

    return out


def reconcile_plan_scope_with_file_source(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Attached file is the extract source — stale PLAN_SCOPE must not ask for connectors.

    When the user uploads a file, needs_source is false and connector source_phrase
    from session history must not override the attachment.
    """
    out = dict(classification or {})
    goal = dict(parsed_goal or {})
    if not is_file_source(out):
        return out, goal
    goal["needs_source"] = False
    goal.pop("source_phrase", None)
    out["needs_source"] = False
    out.pop("source_phrase", None)
    return out, goal


async def stage_pending_file_upload(
    *,
    user_id: str,
    conversation_id: Optional[str],
    filename: str,
    file_content: bytes,
) -> Dict[str, Any]:
    """Upload to S3 and return a JSON-safe ref stored on classification / planner_pending."""
    upload_id = str(uuid.uuid4())
    conv_part = (conversation_id or "no-conversation").strip()
    safe_name = (filename or "upload").replace("/", "_")
    file_key = f"pending-upload/{user_id}/{conv_part}/{upload_id}_{safe_name}"
    await upload_file_bytes_to_s3(file_key=file_key, file_content=file_content)
    return {
        "file_key": file_key,
        "filename": filename or "upload",
        "size_bytes": len(file_content or b""),
    }


async def load_pending_file_upload(ref: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Load staged bytes from file store using _pending_file_ref from planner_pending."""
    if not ref or not isinstance(ref, dict):
        return None
    file_key = (ref.get("file_key") or "").strip()
    if not file_key:
        return None
    content = await get_bytes(file_key=file_key)
    return {
        "filename": ref.get("filename") or "upload",
        "content": content,
        "file_key": file_key,
    }


async def resolve_pending_file_upload(
    *,
    pending_file_upload: Optional[Dict[str, Any]],
    classification: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Prefer in-request upload; else load from staged ref on classification."""
    if pending_file_upload and pending_file_upload.get("content"):
        return pending_file_upload
    ref = (classification or {}).get("_pending_file_ref")
    if ref:
        return await load_pending_file_upload(ref)
    payload = (classification or {}).get("_pending_file_payload")
    if payload and isinstance(payload, dict) and payload.get("content"):
        return payload
    return None
