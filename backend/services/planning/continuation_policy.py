"""Session continuation rules using DB-backed resource resolution."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from services.planning.resource_catalog import ResourceCatalog

if TYPE_CHECKING:
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.planning.schemas import PlanEntityRef

RESUME_MODE_SCOPE_TURN = "scope_turn"
RESUME_MODE_CLARIFICATION = "clarification_resume"


def _resume_mode(classification: Dict[str, Any]) -> str:
    mode = (classification.get("_resume_mode") or RESUME_MODE_SCOPE_TURN).strip()
    return mode if mode in (RESUME_MODE_SCOPE_TURN, RESUME_MODE_CLARIFICATION) else RESUME_MODE_SCOPE_TURN


def _is_clarification_resume(classification: Dict[str, Any]) -> bool:
    return _resume_mode(classification) == RESUME_MODE_CLARIFICATION


def _is_unknown(value: Optional[str]) -> bool:
    v = (value or "").strip().lower()
    return not v or v == "unknown"


def force_planning_continuation_for_open_clar(
    open_clarifications: Any,
) -> bool:
    """
    True when an open ranked-slot clarification must not be superseded by a fresh turn.

    Used by pipeline turn routing and fresh-supersede guards (e.g. pick_extract_fields).
    """
    from services.planning.clarification_queue import open_ranked_slot_clarification_types

    return bool(open_ranked_slot_clarification_types(open_clarifications))


def should_supersede_pending_for_fresh_turn(
    open_clarifications: Any,
) -> bool:
    """False when open ranked-slot clarifications would be lost on fresh supersede."""
    return not force_planning_continuation_for_open_clar(open_clarifications)


def _classification_overrides_job_extract_endpoint(
    classification: Dict[str, Any],
    extraction_meta: Dict[str, Any],
) -> bool:
    """True when classifier pins a different extract source/dest than the session job."""
    if _is_unknown(classification.get("source")):
        return False
    intent_l = (classification.get("intent") or "").lower()
    mode_l = (classification.get("mode") or "").lower()
    if intent_l != "extract" and mode_l != "extract":
        return False

    cls_src = (classification.get("source_name") or "").strip().lower()
    job_src = (extraction_meta.get("source_name") or "").strip().lower()
    cls_dest = (classification.get("destination_name") or "").strip().lower()
    job_dest = (extraction_meta.get("destination_name") or "").strip().lower()

    if cls_src and job_src and cls_src != job_src:
        return True
    if (
        cls_dest
        and job_dest
        and not _is_unknown(cls_dest)
        and cls_dest != job_dest
    ):
        return True
    return False


def apply_extraction_session_continuation(
    classification: Dict[str, Any],
    *,
    extraction_meta: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
    conversation_history: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Pin source from latest extraction_completed; resolve destination from prompt
    when user names a different connector (all types via catalog).
    """
    out = dict(classification or {})
    if not extraction_meta:
        return out

    session_action = (out.get("session_action") or "").strip()
    if session_action == "continue_active_job":
        pass  # keep pins; do not clear active_job_id
    elif session_action in ("fresh_extract", "fresh_full_pipeline"):
        out.pop("active_job_id", None)
        if session_action == "fresh_full_pipeline":
            return out
        # fall through for source/dest refresh on fresh extract

    from services.planning.plan_scope import classification_requests_fresh_extraction

    if classification_requests_fresh_extraction(out):
        out.pop("active_job_id", None)
        return out

    if _classification_overrides_job_extract_endpoint(out, extraction_meta):
        out.pop("active_job_id", None)
        return out

    src = extraction_meta.get("source_type") or extraction_meta.get("source")
    dest = extraction_meta.get("destination")
    src_name = extraction_meta.get("source_name")
    dest_name = extraction_meta.get("destination_name")

    if src and not _is_unknown(src):
        out["source"] = src
    if src_name:
        out["source_name"] = src_name

    intent_l = (out.get("intent") or "").lower()
    mode_l = (out.get("mode") or "").lower()
    map_migrate_intent = intent_l in ("map_data", "migrate_data", "migrate") or mode_l in (
        "map",
        "migrate",
    )
    llm_object = (out.get("destination_object_api_name") or "").strip()
    if llm_object and llm_object.lower() not in ("unknown",):
        out["destination_object_api_name"] = llm_object

    if map_migrate_intent and not out.get("destination_object_api_name") and extraction_meta:
        obj = (
            extraction_meta.get("object_api_name")
            or extraction_meta.get("destination_object_api_name")
        )
        if obj and str(obj).strip() and str(obj).lower() not in ("unknown",):
            out["destination_object_api_name"] = str(obj).strip()

    map_meta = None
    if map_migrate_intent and conversation_history:
        from services.planning.conversation_session import latest_mapping_started_metadata

        map_meta = latest_mapping_started_metadata(conversation_history)
        if map_meta:
            if map_meta.get("destination") and not _is_unknown(map_meta.get("destination")):
                out["destination"] = map_meta["destination"]
            if map_meta.get("destination_name") and not _is_unknown(
                map_meta.get("destination_name")
            ):
                out["destination_name"] = map_meta["destination_name"]

    pinned_map_dest = bool(
        map_meta
        and (
            (map_meta.get("destination_name") and not _is_unknown(map_meta.get("destination_name")))
            or (map_meta.get("destination") and not _is_unknown(map_meta.get("destination")))
        )
    )
    session_dest = out.get("destination_name")
    pinned_session_dest = bool(
        map_migrate_intent
        and session_dest
        and not _is_unknown(session_dest)
        and (not dest_name or session_dest.strip().lower() != dest_name.strip().lower())
    )
    if not pinned_map_dest and not pinned_session_dest:
        if dest and not _is_unknown(dest):
            out["destination"] = dest
        if dest_name and not _is_unknown(dest_name):
            out["destination_name"] = dest_name

    if _is_unknown(out.get("destination_name")) and src_name:
        if (
            str(out.get("source") or "").lower() == "salesforce"
            and str(out.get("destination") or "").lower() == "salesforce"
        ):
            out["destination_name"] = src_name

    job_id = extraction_meta.get("job_id")

    if job_id:
        session_action = (out.get("session_action") or "").strip()
        fresh_compound_extract = (
            intent_l == "extract"
            and mode_l == "extract"
            and not _is_unknown(out.get("destination_name"))
            and session_action != "continue_active_job"
        )
        if not fresh_compound_extract:
            out["active_job_id"] = job_id

    return out


def apply_migration_failure_continuation(
    classification: Dict[str, Any],
    *,
    prompt: Optional[str],
    conversation_history: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    When extract succeeded but migration partially failed, pin extraction job and
    prefer map → migrate on continuation turns (skip re-extract by default).
    """
    del prompt
    out = dict(classification or {})
    if not _session_signals_migration_retry(out):
        return out

    mig_meta = latest_partial_migration_metadata(conversation_history)
    if not mig_meta and not latest_failure_report_from_history(conversation_history):
        return out

    source_job = (mig_meta or {}).get("source_job_id")
    if source_job:
        out["active_job_id"] = source_job
    out["retry_after_partial"] = True
    out["mode"] = "migrate"
    out["intent"] = "migrate_data"

    report = latest_failure_report_from_history(conversation_history)
    if report:
        out["last_failure_report"] = report
        ctx = build_retry_mapping_context(report)
        if ctx:
            out["recovery_mapping_context"] = ctx

    if mig_meta:
        if mig_meta.get("destination"):
            out["destination"] = mig_meta["destination"]
        if mig_meta.get("destination_name"):
            out["destination_name"] = mig_meta["destination_name"]
        if mig_meta.get("batch_id"):
            out["recovery_batch_id"] = mig_meta["batch_id"]

    return out


def should_skip_source_binding(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
    *,
    prompt: Optional[str] = None,
) -> bool:
    """
    Map/migrate on a completed extraction job — source schema comes from the active job,
    not a fresh catalog bind. Fresh extract in the same turn still binds source.

    Uses session_action + pipeline tokens from PLAN_SCOPE only.
    """
    del prompt
    if (classification.get("plan_route") or "").strip() == "fresh_path":
        return False

    session_action = (classification.get("session_action") or "").strip()
    if session_action in ("fresh_extract", "fresh_full_pipeline", "no_prior_job"):
        return False

    if not classification.get("active_job_id"):
        return False

    from services.planning.plan_scope import continuation_pipeline_tokens, read_plan_scope

    tokens = continuation_pipeline_tokens(
        classification, dict(parsed_goal or read_plan_scope(classification) or {})
    )
    if "extract" in tokens:
        return False

    if session_action == "continue_active_job":
        return True

    if any(t in tokens for t in ("map", "migrate", "data_ops", "create")):
        return True

    return False


def pin_source_from_active_job(
    classification: Dict[str, Any],
) -> tuple["PlanEntityRef", "PromptMigrationIntent"]:
    """
    Build a resolved source contract from session pins (active job + prior extract metadata).
    """
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.planning.schemas import PlanEntityRef, normalize_connector_type

    cls = dict(classification or {})
    ct = normalize_connector_type(cls.get("source") or "")
    entity_key = (
        (cls.get("source_table_name") or "").strip()
        or (cls.get("source_object_api_name") or "").strip()
        or (cls.get("source_sheet_name") or "").strip()
    )
    mi_raw = cls.get("migration_intent") if isinstance(cls.get("migration_intent"), dict) else {}
    if not entity_key:
        entity_key = (mi_raw.get("driving_entity_key") or "").strip()
    if not entity_key:
        entity_key = "active_extraction"
    label = (
        (mi_raw.get("driving_entity_label") or "").strip()
        or entity_key
        or "extracted_data"
    )

    connector_name = (cls.get("source_name") or "").strip() or None
    if connector_name and entity_key and connector_name.lower() == entity_key.lower():
        connector_name = None

    field_hints = list(mi_raw.get("field_hints") or [])
    source_ref = PlanEntityRef(
        connector_type=ct,
        connector_id=(cls.get("source_connector_id") or "").strip() or None,
        display_name=connector_name,
        entity_key=entity_key or None,
        entity_label=label,
        catalog_label=label,
        resolution_source="plan_pinned",
        confidence=0.95 if entity_key else 0.85,
    )
    migration_intent = PromptMigrationIntent(
        driving_entity_key=entity_key or None,
        driving_entity_label=label,
        field_hints=field_hints,
        field_hints_strict=bool(mi_raw.get("field_hints_strict")),
        extract_field_scope=mi_raw.get("extract_field_scope") or "explicit",
        extract_fields_confirmed=True,
        needs_extract_clarification=False,
        cross_system=ct
        != normalize_connector_type(cls.get("destination") or "")
        and normalize_connector_type(cls.get("destination") or "") not in ("unknown", ""),
    )
    return source_ref, migration_intent


def apply_active_job_continuation_flags(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Mark extract source/fields confirmed when continuing map/migrate on an active job."""
    out = dict(classification or {})
    if not out.get("active_job_id"):
        return out
    out["planner_source_entity_confirmed"] = True
    out["extract_fields_confirmed"] = True
    return out


def _goal_requests_downstream_pipeline(goal: Dict[str, Any]) -> bool:
    """True when scope explicitly flags destination need (legacy + scope LLM)."""
    if goal.get("needs_destination") is True:
        return True
    if goal.get("create_destination") or goal.get("destination_phrase"):
        return True
    if goal.get("scope_kind") == "compound":
        pipeline_ops = [str(o).lower() for o in (goal.get("operations") or [])]
        if any(o in ("map", "migrate") for o in pipeline_ops):
            return True
    return False


def _classification_has_confirmed_migrate_destination(classification: Dict[str, Any]) -> bool:
    from services.planning.connector_guards import is_known_connector

    dest_name = (classification.get("destination_name") or "").strip().lower()
    if dest_name and dest_name not in ("unknown", "") and classification.get(
        "planner_destination_confirmed"
    ):
        return True
    dest = (classification.get("destination") or "").strip().lower()
    if is_known_connector(dest) and dest_name and dest_name not in ("unknown", ""):
        return True
    return False


def _should_normalize_fresh_extract_only(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    prompt: Optional[str] = None,
) -> bool:
    """True when S01 decided fresh_extract and PLAN_SCOPE does not require a destination."""
    session_action = (classification.get("session_action") or "").strip()

    if session_action == "fresh_full_pipeline":
        return False

    pipeline_ops = [str(o).lower() for o in (goal.get("operations") or [])]
    if any(o in pipeline_ops for o in ("map", "migrate")):
        return False
    if classification.get("destination_same_as_source"):
        return False
    if _goal_requests_downstream_pipeline(goal):
        return False
    if goal.get("create_destination") or classification.get("planner_create_spreadsheet"):
        return False

    dest_name = (classification.get("destination_name") or "").strip().lower()
    if dest_name and dest_name not in ("unknown", "") and classification.get(
        "planner_destination_confirmed"
    ):
        return False

    if session_action == "fresh_extract":
        return True

    if (classification.get("plan_route") or "") == "fresh_path" and not classification.get(
        "active_job_id"
    ):
        intent = (classification.get("intent") or "").lower()
        mode = (classification.get("mode") or "").lower()
        if intent == "extract" or mode == "extract":
            return True

    return False


def apply_fresh_extract_goal_normalization(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    prompt: Optional[str] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Strip stale ``create`` when destination is an existing pinned Google Sheet.

    PLAN_SCOPE LLM remains authoritative; this only reconciles mechanical flags.
    """
    del prompt
    out = dict(classification or {})
    parsed = dict(goal or {})
    from services.planning.goal_reconciliation import (
        catalog_proved_existing_gsheet_destination,
        drop_stale_create_for_pre_pinned_workbook,
        reconcile_existing_destination_flags,
    )

    if catalog_proved_existing_gsheet_destination(out, None):
        out, parsed, dropped = drop_stale_create_for_pre_pinned_workbook(out, parsed)
        if dropped:
            ops = [str(o).lower() for o in (parsed.get("operations") or out.get("operations") or [])]
            if "migrate" in ops:
                out["mode"] = "migrate"
                parsed["mode"] = "migrate"
        out, parsed, _ = reconcile_existing_destination_flags(out, parsed, None)
    return out, parsed


def sync_plan_scope_for_continuation(
    classification: Dict[str, Any],
    parsed_goal: Dict[str, Any],
) -> Dict[str, Any]:
    """Keep parsed_goal operations aligned after LLM goal enrichment."""
    goal = dict(parsed_goal or {})
    ops = list(classification.get("operations") or goal.get("operations") or [])
    if ops:
        goal["operations"] = ops
    if classification.get("mode"):
        goal["mode"] = classification.get("mode")
    if classification.get("intent"):
        goal["intent"] = classification.get("intent")
    return goal


ContinuationStep = str  # "data_ops" | "map" | "migrate"

CONTINUATION_DEST_INHERIT = "inherit"
CONTINUATION_DEST_RESOLVE = "resolve"
CONTINUATION_DEST_NONE = "none"

# PLAN_SCOPE sometimes echoes the migrate operation, not a connector/workbook target.
_MIGRATE_ACTION_NOISE_PHRASES = frozenset(
    {
        "migration",
        "migrate",
        "start migration",
        "start the migration",
        "the migration",
    }
)


def resolve_continuation_steps(
    goal: Dict[str, Any],
    job: Any,
    *,
    continuation_phase: str = "none",
) -> List[str]:
    """
    Build ordered continuation plan steps from LLM goal + job facts (no keyword matching).
    Step tokens: extract, data_ops, map, migrate.
    """
    from services.planning.pipeline_step_resolver import PlanContext, resolve_pipeline_steps

    ctx = PlanContext(
        active_job_id=getattr(job, "job_id", None),
        mapping_ready=bool(getattr(job, "mapping_ready", False)),
        continuation_phase=continuation_phase or "none",
        is_continuation=True,
    )
    return resolve_pipeline_steps(goal, ctx)


def continuation_needs_destination_resolution(steps: List[str]) -> bool:
    return any(s in steps for s in ("map", "migrate"))


def _phrase_matches_job_destination_label(phrase: str, job: Any) -> bool:
    dest = getattr(job, "destination", None)
    if not dest:
        return False
    job_label = (getattr(dest, "display_name", None) or "").strip().lower()
    if not job_label:
        return False
    return (phrase or "").strip().lower() == job_label


def _phrase_matches_job_source_label(phrase: str, job: Any) -> bool:
    src = getattr(job, "source", None)
    if not src:
        return False
    job_label = (getattr(src, "display_name", None) or "").strip().lower()
    if not job_label:
        return False
    return (phrase or "").strip().lower() == job_label


def goal_requests_new_source_endpoint(
    goal: Dict[str, Any],
    job: Any,
) -> bool:
    """True when PLAN_SCOPE names a source different from the active job source."""
    phrase = (goal.get("source_phrase") or "").strip()
    if not phrase:
        return False
    return not _phrase_matches_job_source_label(phrase, job)


def goal_requests_new_destination_endpoint(
    goal: Dict[str, Any],
    job: Any,
    *,
    cont_steps: Optional[List[str]] = None,
    catalog: Optional[ResourceCatalog] = None,
) -> bool:
    """
    True when PLAN_SCOPE names a map/migrate target different from the job destination.

    Trust PLAN_SCOPE LLM for destination_phrase (including connector-type labels like
    "google sheet"). Generic/deictic phrase enrichment is session_facts only — not here.
    Empty or migrate-action noise phrases do not count as a new endpoint.
    """
    del cont_steps, catalog
    if goal.get("destination_same_as_source"):
        return True
    phrase = (goal.get("destination_phrase") or "").strip()
    if not phrase:
        return False
    if _phrase_matches_job_destination_label(phrase, job):
        return False
    norm = " ".join(phrase.lower().split())
    if norm in _MIGRATE_ACTION_NOISE_PHRASES:
        return False
    return True


def continuation_should_inherit_job_destination(
    cont_steps: List[str],
    job: Any,
    goal: Dict[str, Any],
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> bool:
    """Migrate on mapping_ready job with no new connector target — use JobContext destination."""
    steps = [str(s).lower() for s in (cont_steps or [])]
    if "migrate" not in steps:
        return False
    if not bool(getattr(job, "mapping_ready", False)):
        return False
    if goal.get("destination_same_as_source"):
        return False
    if goal_requests_new_destination_endpoint(
        goal, job, cont_steps=steps, catalog=catalog
    ):
        return False
    return bool(getattr(job, "destination", None))


def resolve_continuation_destination_mode(
    cont_steps: List[str],
    goal: Dict[str, Any],
    job: Any,
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> str:
    """
    Mechanical continuation destination policy after PLAN_SCOPE.

    INHERIT — reuse JobContext destination (mapped migrate, no new target phrase).
    RESOLVE — run destination connector LLM + entity bind.
    NONE — no map/migrate destination work this turn.
    """
    steps = [str(s).lower() for s in (cont_steps or [])]
    if not continuation_needs_destination_resolution(steps):
        return CONTINUATION_DEST_NONE
    if continuation_should_inherit_job_destination(steps, job, goal, catalog=catalog):
        return CONTINUATION_DEST_INHERIT
    return CONTINUATION_DEST_RESOLVE


def continuation_needs_destination_connector_bind(
    cont_steps: List[str],
    goal: Dict[str, Any],
    job: Any,
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> bool:
    """True when continuation should run destination connector catalog LLM."""
    return (
        resolve_continuation_destination_mode(cont_steps, goal, job, catalog=catalog)
        == CONTINUATION_DEST_RESOLVE
    )


def continuation_needs_map_connector_resolution(
    cont_steps: List[str],
    goal: Dict[str, Any],
    job: Any,
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> bool:
    """Alias for continuation_needs_destination_connector_bind (legacy name)."""
    return continuation_needs_destination_connector_bind(
        cont_steps, goal, job, catalog=catalog
    )


def ensure_map_when_destination_changes(
    goal: Dict[str, Any],
    job: Any,
    cont_steps: List[str],
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    """Inject map before migrate when user names a new destination on a mapping_ready job."""
    from services.planning.plan_scope import finalize_plan_scope_goal

    out = dict(goal or {})
    steps = [str(s).lower() for s in (cont_steps or [])]
    if "migrate" not in steps or not bool(getattr(job, "mapping_ready", False)):
        return out
    if resolve_continuation_destination_mode(steps, out, job, catalog=catalog) != CONTINUATION_DEST_RESOLVE:
        return out
    ops = [str(o).lower() for o in (out.get("operations") or [])]
    if "map" in ops:
        return out
    idx = ops.index("migrate") if "migrate" in ops else len(ops)
    ops.insert(idx, "map")
    out["operations"] = ops
    return finalize_plan_scope_goal(out)


def apply_job_destination_pins(
    classification: Dict[str, Any],
    job: Any,
) -> Dict[str, Any]:
    """Copy canonical destination from JobContext (migration context), not chat event replay."""
    out = dict(classification or {})
    dest = getattr(job, "destination", None)
    if not dest:
        return out

    if not _is_unknown(getattr(dest, "connector_type", None)):
        out["destination"] = dest.connector_type
    if getattr(dest, "display_name", None) and not _is_unknown(dest.display_name):
        out["destination_name"] = dest.display_name
    if getattr(dest, "connector_id", None):
        out["destination_connector_id"] = dest.connector_id

    dest_entity = (
        (getattr(dest, "entity_key", None) or "").strip()
        or (getattr(job, "destination_object_api_name", None) or "").strip()
        or None
    )
    if dest_entity:
        from utils.migration_destination_entity import coerce_destination_entity_api_name

        dest_type = (getattr(dest, "connector_type", None) or out.get("destination") or "").strip()
        dest_entity = (
            coerce_destination_entity_api_name(
                dest_entity,
                connector_type=dest_type,
                mapping=getattr(job, "mapping_config", None),
            )
            or dest_entity
        )
    if dest_entity and dest_entity.lower() not in ("unknown",):
        out["destination_object_api_name"] = dest_entity
        out["planner_destination_entity_confirmed"] = True
        st = (out.get("destination") or "").strip().lower()
        if st == "sql":
            out["destination_table_name"] = dest_entity
        elif st == "salesforce":
            out["object_api_name"] = dest_entity

    if out.get("destination_name") or out.get("destination_connector_id"):
        out["planner_destination_confirmed"] = True
        out["_destination_endpoint_clarified"] = True

    contract = dict(out.get("plan_contract") or {})
    if contract:
        dest_side = dict(contract.get("destination") or {})
        if out.get("destination"):
            dest_side["connector_type"] = out["destination"]
        if out.get("destination_name"):
            dest_side["display_name"] = out["destination_name"]
        if out.get("destination_connector_id"):
            dest_side["connector_id"] = out["destination_connector_id"]
        if dest_entity:
            dest_side["entity_key"] = dest_entity
            dest_side["entity_label"] = dest_entity
            dest_side["catalog_label"] = dest_entity
            dest_side["resolution_source"] = "plan_pinned"
        contract["destination"] = dest_side
        out["plan_contract"] = contract
    return out


def clear_stale_plan_contract_destination(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Drop job-pinned destination side from plan_contract when user names a new target."""
    out = dict(classification or {})
    contract = out.get("plan_contract")
    if not isinstance(contract, dict):
        return out
    trimmed = dict(contract)
    trimmed.pop("destination", None)
    trimmed.pop("mapping_target", None)
    out["plan_contract"] = trimmed
    return out


def _destination_entity_key_from_pins(
    classification: Dict[str, Any],
    connector_type: str,
) -> Optional[str]:
    from services.planning.schemas import normalize_connector_type

    ct = normalize_connector_type(connector_type or "")
    if ct == "salesforce":
        return (classification.get("destination_object_api_name") or "").strip() or None
    if ct == "sql":
        return (classification.get("destination_table_name") or "").strip() or None
    if ct in ("google_sheets", "gsheets"):
        entity = (classification.get("destination_object_api_name") or "").strip()
        if entity:
            return entity
        sid = (classification.get("destination_spreadsheet_id") or "").strip()
        tab = (classification.get("destination_sheet_name") or "").strip()
        if sid and tab:
            return f"{sid}::{tab}"
        name = (classification.get("destination_name") or "").strip()
        return name or None
    return (classification.get("destination_object_api_name") or "").strip() or None


def sync_plan_contract_destination_from_classification(
    classification: Dict[str, Any],
    *,
    resolution_source: str = "llm_bind",
    confidence: float = 0.9,
) -> Dict[str, Any]:
    """Mirror validated destination connector + entity pins into plan_contract.destination."""
    from services.planning.schemas import PlanEntityRef, normalize_connector_type

    out = dict(classification or {})
    ct = normalize_connector_type(out.get("destination") or "")
    if not ct or ct == "unknown":
        return out

    entity_key = _destination_entity_key_from_pins(out, ct)
    display = (
        (out.get("destination_name") or "").strip()
        or (out.get("destination_account_name") or "").strip()
        or None
    )
    conn_id = (
        (out.get("destination_connector_id") or "").strip()
        or (out.get("google_sheets_connection_id") or "").strip()
        or None
    )
    sheet_name = (out.get("destination_sheet_name") or "").strip() or None
    spreadsheet_id = (out.get("destination_spreadsheet_id") or "").strip() or None

    resolved = bool(conn_id or display or spreadsheet_id)
    entity_resolved = bool(entity_key)
    dest_ref = PlanEntityRef(
        connector_type=ct,
        connector_id=conn_id,
        display_name=display,
        entity_key=entity_key if entity_resolved else None,
        entity_label=entity_key if entity_resolved else display,
        catalog_label=entity_key if entity_resolved else display,
        sheet_name=sheet_name,
        spreadsheet_id=spreadsheet_id,
        resolution_source=resolution_source if entity_resolved else "pending",  # type: ignore[arg-type]
        confidence=confidence if entity_resolved else (0.85 if resolved else 0.0),
    )
    if resolved and not entity_resolved:
        dest_ref.resolution_source = resolution_source  # type: ignore[assignment]
        dest_ref.confidence = confidence

    contract = dict(out.get("plan_contract") or {})
    contract["destination"] = dest_ref.model_dump(mode="json")
    out["plan_contract"] = contract
    return out


def plan_contract_destination_stale_vs_classification(
    classification: Dict[str, Any],
    contract: Optional[Any],
) -> bool:
    """True when job-pinned contract destination disagrees with classification connector pins."""
    if contract is None:
        return False
    from services.planning.schemas import PlanContract, normalize_connector_type

    try:
        pc = contract if isinstance(contract, PlanContract) else PlanContract.model_validate(contract)
    except Exception:
        return False
    dest = pc.destination
    if not dest or (dest.resolution_source or "") != "plan_pinned":
        return False
    cls_ct = normalize_connector_type(classification.get("destination") or "")
    con_ct = normalize_connector_type(dest.connector_type or "")
    if not cls_ct or cls_ct == "unknown" or not con_ct or con_ct == "unknown":
        return False
    return cls_ct != con_ct


def apply_continuation_endpoint_pins_from_job(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    job: Any,
    *,
    resume_mode: Optional[str] = None,
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    """
    Apply structured destination intent from PLAN_SCOPE + active job before label-aware grounding.

    Uses job facts for same-as-source (not regex). Clears stale job destination pins when the user
    names a new target phrase on a scope turn — never on clarification resume or when connector slot
    is already satisfied.
    """
    out = dict(classification or {})
    scoped = dict(goal or {})
    mode = (resume_mode or _resume_mode(out)).strip()

    if scoped.get("source_phrase"):
        out["source_phrase"] = str(scoped["source_phrase"]).strip()
    dest_phrase = (scoped.get("destination_phrase") or "").strip()
    if dest_phrase:
        out["destination_phrase"] = dest_phrase
        scoped["destination_phrase"] = dest_phrase

    same_as_source = bool(scoped.get("destination_same_as_source"))
    if same_as_source:
        out["destination_same_as_source"] = True
        scoped["destination_same_as_source"] = True
    elif (
        mode != RESUME_MODE_CLARIFICATION
        and dest_phrase
        and goal_requests_new_destination_endpoint(scoped, job, catalog=catalog)
    ):
        for key in (
            "destination",
            "destination_name",
            "destination_account_name",
            "destination_connector_id",
            "google_sheets_connection_id",
            "destination_sheet_name",
            "destination_spreadsheet_id",
            "planner_destination_confirmed",
            "_destination_endpoint_clarified",
            "_destination_connector_validated",
            "destination_object_api_name",
            "destination_table_name",
            "planner_destination_entity_confirmed",
            "object_api_name",
        ):
            out.pop(key, None)
        out = clear_stale_plan_contract_destination(out)

    out["_plan_scope"] = scoped
    return out


def continuation_needs_destination_llm_resolution(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    job: Any,
    *,
    catalog: Optional[ResourceCatalog] = None,
    needs_map: bool = True,
    needs_connector_bind: Optional[bool] = None,
    prompt: Optional[str] = None,
) -> bool:
    """
    True when continuation should call the catalog destination LLM.

    Slot-satisfied connector pins win over stale destination_phrase on clarification resume.
    """
    from services.planning.connector_guards import classification_destination_endpoint_resolved
    from services.planning.destination_catalog_validate import (
        destination_connector_slot_satisfied,
        read_needs_destination,
    )
    from services.planning.resource_catalog import coerce_planning_catalog

    bind = needs_connector_bind if needs_connector_bind is not None else needs_map
    if not bind:
        return False

    scoped = dict(goal or {})
    if not read_needs_destination(classification, scoped):
        return False

    cat = catalog or coerce_planning_catalog(classification=classification)
    if _is_clarification_resume(classification) and cat is not None:
        if destination_connector_slot_satisfied(classification, cat, parsed_goal=scoped):
            return False

    dest_phrase = (
        scoped.get("destination_phrase")
        or (classification or {}).get("destination_phrase")
        or ""
    ).strip()

    if classification_destination_endpoint_resolved(classification):
        if (
            not _is_clarification_resume(classification)
            and dest_phrase
            and goal_requests_new_destination_endpoint(scoped, job, catalog=catalog)
        ):
            return True
        return False

    return bool(
        dest_phrase
        or (prompt or "").strip()
        or scoped.get("destination_same_as_source")
        or (classification or {}).get("destination_same_as_source")
    )


def reconcile_continuation_scope_after_destination(
    cls: Dict[str, Any],
    goal: Dict[str, Any],
    job: Any,
    decision: Any,
) -> tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """
    Re-run map injection and step tokens after session enrich or connector LLM updates.

    Same rules as apply_continuation_goal_to_classification scope tail — no new heuristics.
    """
    from services.intent.session_continuation import SessionContinuationDecision
    from services.planning.plan_scope import apply_continuation_scope_cap, sync_scope_into_classification

    phase = (
        decision.continuation_phase
        if isinstance(decision, SessionContinuationDecision)
        else (decision or {}).get("continuation_phase")
    ) or "none"
    mapping_ready = bool(getattr(job, "mapping_ready", False))
    scoped = dict(goal or {})
    if not [o for o in (scoped.get("operations") or []) if str(o).strip()]:
        prior_steps = list(cls.get("_continuation_steps") or [])
        pipeline_ops = [s for s in prior_steps if s != "data_ops"]
        if pipeline_ops:
            scoped["operations"] = pipeline_ops
    capped_goal = apply_continuation_scope_cap(
        scoped,
        continuation_phase=str(phase),
        mapping_ready=mapping_ready,
    )
    prelim_steps = resolve_continuation_steps(
        capped_goal, job, continuation_phase=str(phase)
    )
    capped_goal = ensure_map_when_destination_changes(
        capped_goal, job, prelim_steps
    )
    steps = resolve_continuation_steps(capped_goal, job, continuation_phase=str(phase))
    parsed_goal = dict(capped_goal)
    if steps:
        parsed_goal["operations"] = [s for s in steps if s != "data_ops"]
    out = sync_scope_into_classification(dict(cls), parsed_goal, steps)
    out["_continuation_steps"] = steps
    return out, parsed_goal, steps


def apply_continuation_goal_to_classification(
    cls: Dict[str, Any],
    goal: Dict[str, Any],
    job: Any,
    decision: Any,
) -> Dict[str, Any]:
    """
    Merge PLAN_SCOPE output + job stage into classification for continuation planning.
    """
    from services.intent.session_continuation import SessionContinuationDecision
    from services.planning.goal_parser import resolve_data_operations

    out, capped_goal, steps = reconcile_continuation_scope_after_destination(
        cls, goal, job, decision
    )

    phase = (
        decision.continuation_phase
        if isinstance(decision, SessionContinuationDecision)
        else (decision or {}).get("continuation_phase")
    ) or "none"

    data_ops = resolve_data_operations(out, prompt=None, goal=capped_goal)
    if not data_ops:
        data_ops = list(capped_goal.get("data_operations") or [])

    in_place = phase == "data_ops" or (
        bool(capped_goal.get("in_place_transform"))
        and not continuation_needs_destination_resolution(steps)
    )
    if steps == ["data_ops"] or in_place:
        for key in (
            "destination",
            "destination_name",
            "destination_connector_id",
            "destination_object_api_name",
            "destination_sheet_name",
            "destination_spreadsheet_id",
            "planner_destination_confirmed",
            "planner_destination_entity_confirmed",
            "_destination_endpoint_clarified",
            "plan_destination",
        ):
            out.pop(key, None)
        contract = out.get("plan_contract")
        if isinstance(contract, dict):
            trimmed = dict(contract)
            trimmed.pop("destination", None)
            trimmed.pop("mapping_target", None)
            out["plan_contract"] = trimmed

    parsed_goal = dict(capped_goal)
    if "data_ops" in steps and data_ops:
        parsed_goal["operations"] = []
        parsed_goal["data_operations"] = data_ops
    elif steps:
        parsed_goal["operations"] = [s for s in steps if s != "data_ops"]
    if data_ops:
        parsed_goal["data_operations"] = data_ops
    from services.planning.plan_scope import sync_scope_into_classification

    return sync_scope_into_classification(out, parsed_goal, steps)


# --- Failure context (merged P4.9) ---

"""Failure-aware planning context from chat history and session metadata."""

from typing import Any, Dict, List, Optional

from agent.recovery_policy import MIN_SUCCESS_RATE_OK


def _session_signals_migration_retry(classification: Dict[str, Any]) -> bool:
    """True when S01 continuation owns a migrate/remap turn after partial failure."""
    sa = (classification.get("session_action") or "").strip()
    phase = (classification.get("continuation_phase") or "").strip().lower()
    if sa != "continue_active_job":
        return False
    if phase in ("map", "migrate", "data_ops"):
        return True
    return bool(classification.get("active_job_id") or classification.get("retry_after_partial"))


def _success_rate_from_metadata(meta: Dict[str, Any]) -> Optional[float]:
    success = int(meta.get("success_count") or meta.get("successful_records") or 0)
    errors = int(meta.get("error_count") or meta.get("failed_records") or 0)
    total = int(meta.get("total_records") or 0) or success + errors
    if total <= 0:
        return None
    return success / total


def latest_partial_migration_metadata(
    conversation_history: Optional[List[Dict]],
) -> Optional[Dict[str, Any]]:
    """Most recent migration_completed with success rate below recovery OK threshold."""
    for msg in reversed(conversation_history or []):
        metadata = msg.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if metadata.get("event_type") != "migration_completed":
            continue
        rate = _success_rate_from_metadata(metadata)
        if rate is None:
            continue
        errors = int(metadata.get("error_count") or metadata.get("failed_records") or 0)
        if rate < MIN_SUCCESS_RATE_OK and errors > 0:
            return metadata
        if metadata.get("status") == "failed":
            return metadata
    return None


def latest_failure_report_from_history(
    conversation_history: Optional[List[Dict]],
) -> Optional[Dict[str, Any]]:
    """Find structured failure_report from recovery panel or session metadata."""
    for msg in reversed(conversation_history or []):
        metadata = msg.get("metadata")
        if not isinstance(metadata, dict):
            continue
        proposal = metadata.get("recovery_proposal") or {}
        report = proposal.get("failure_report")
        if isinstance(report, dict) and report.get("success_rate") is not None:
            return report
        report = metadata.get("failure_report") or metadata.get("last_failure_report")
        if isinstance(report, dict) and report.get("success_rate") is not None:
            return report
    mig = latest_partial_migration_metadata(conversation_history)
    if not mig:
        return None
    rate = _success_rate_from_metadata(mig)
    if rate is None:
        return None
    return {
        "batch_id": mig.get("batch_id"),
        "success_rate": rate,
        "failed_count": int(mig.get("error_count") or mig.get("failed_records") or 0),
        "total_records": int(mig.get("success_count") or 0)
        + int(mig.get("error_count") or 0),
        "successful_records": int(mig.get("success_count") or 0),
        "destination_type": mig.get("destination"),
        "failed_fields": mig.get("failed_fields") or [],
    }


def build_retry_mapping_context(report: Optional[Dict[str, Any]]) -> Optional[str]:
    if not report:
        return None
    parts = ["Fix migration errors from the previous attempt."]
    fields = report.get("failed_fields") or []
    if fields:
        parts.append(f"Review mapping for: {', '.join(fields[:12])}.")
    top_errors = report.get("top_errors") or []
    if top_errors and isinstance(top_errors[0], dict):
        msg = (top_errors[0].get("message") or "").strip()
        if msg:
            parts.append(f"Common error: {msg[:200]}")
    return " ".join(parts)


async def get_failure_context_for_planning(
    *,
    user_id: str,
    prompt: str,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict]] = None,
    classification: Optional[Dict[str, Any]] = None,
) -> str:
    """Text block for goal_parser / gap_detector when prior migration had failures."""
    history = list(conversation_history or [])
    if not history and conversation_id:
        try:
            from services.chat_service import ChatService

            messages = await ChatService.get_messages(conversation_id, user_id, limit=80)
            history = [
                {"metadata": m.get("metadata"), "text": m.get("content"), "type": m.get("role")}
                for m in (messages or [])
            ]
        except Exception:
            history = []

    report = latest_failure_report_from_history(history)
    mig = latest_partial_migration_metadata(history)
    if not report and not mig:
        return ""

    cls = dict(classification or {})
    if not _session_signals_migration_retry(cls) and not report:
        return ""

    lines = ["Prior migration context (use for retry planning — do NOT re-extract unless user asks):"]
    if report:
        rate = float(report.get("success_rate") or 0)
        lines.append(
            f"- Success rate: {int(rate * 100)}% "
            f"({report.get('successful_records', '?')}/{report.get('total_records', '?')} records)"
        )
        fields = report.get("failed_fields") or []
        if fields:
            lines.append(f"- Failed fields: {', '.join(fields[:10])}")
        top = report.get("top_errors") or []
        if top and isinstance(top[0], dict) and top[0].get("message"):
            lines.append(f"- Top error: {str(top[0]['message'])[:180]}")
    if mig and mig.get("source_job_id"):
        lines.append(f"- Existing extraction job_id: {mig['source_job_id']} (reuse for remap/retry)")
    lines.append("- Preferred operations: map, migrate (skip extract unless user requests fresh extract)")
    return "\n".join(lines) + "\n\n"

