"""Narrow plan scope view from classification (PLAN_SCOPE LLM output on _plan_scope)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MissingPlanScopeError(ValueError):
    """Raised when build_plan_steps runs without PLAN_SCOPE output on _plan_scope."""


_PLAN_SCOPE_FRESH_JSON_EXAMPLE = """{
  "pipeline_ops": ["extract"],
  "data_operations": [],
  "data_ops_requested": false,
  "create_destination": false,
  "spreadsheet_title": null,
  "spreadsheet_headers": [],
  "spreadsheet_header_source": null,
  "needs_source": true,
  "needs_destination": false,
  "confidence": 0.95
}"""

_PLAN_SCOPE_CONTINUATION_JSON_EXAMPLE = """{
  "pipeline_ops": [],
  "data_operations": ["validate", "transform", "filter"],
  "data_ops_requested": true,
  "needs_destination": false,
  "destination_phrase": null,
  "destination_same_as_source": false,
  "confidence": 0.95
}"""

# User phrasing → pipeline families (intent, not exact keywords).
_PLAN_SCOPE_USER_VOCABULARY = """
User vocabulary → pipeline families (match intent; typos and informal phrasing OK):
- MAP (pipeline_ops includes "map" ONLY — do NOT add migrate): map, mapping, remap, re-map, align
  fields, match columns, field mapping, change mapping, map fields to, map to (object/table/sheet/tab),
  map the extracted data to, map this/that/above data to.
  When the user uses MAP vocabulary, pipeline_ops is ["map"] even if they also say "extracted data to X"
  or name a destination. migrate is NOT implied by map language alone.
- MIGRATE / LOAD TO DESTINATION (pipeline_ops includes "migrate" only — map is added mechanically
  when mapping is not ready): migrate, migration, load, move, copy, send, push, transfer, sync,
  import into, upload to, deploy (data/load — NOT metadata/schema deploy), export to, write to,
  put in, save to — when followed by a named target (org, DB, sheet/workbook).
  Disambiguation:
  • "export/pull/fetch/get records FROM {source}" or "extract from …" → extract (fresh), NOT migrate.
  • On continuation: move/copy/send/load/sync the extracted data TO {target} WITHOUT map vocabulary
    → pipeline_ops: ["migrate"] (not ["map","migrate"] in JSON — map is injected when needed).
  • Map vocabulary + destination → ["map"] only. Migrate/load vocabulary + destination → ["migrate"].
- DATA OPS (data_operations: filter, dedupe, validate, transform — NOT pipeline_ops): clean,
  cleanup, scrub, filter, remove invalid/d duplicates, dedupe, validate, transform, normalize,
  standardize, combine columns, merge columns, fix emails, data quality, enrich columns.
  Do NOT re-run validate/transform/filter when the user ONLY names a load destination with no
  cleanup/transform language in THIS message.
"""

_PLAN_SCOPE_SINGLE_COMPOUND_RULES = """
Scope model (read the USER message — not RAG examples alone):
- scope_kind: single = one op family this turn; compound = two or more families.
  Families: data_operations (filter/dedupe/validate/transform), pipeline_ops (extract/map/migrate/create/...).
- Return EVERY family the user asks for (use vocabulary below — not only the words map/migrate/filter).
  Do NOT drop map/migrate because cleanup language appears in the same message.
- Do NOT add families the user did not ask for (no infer-from field names or data quality alone).
- data_ops_requested: true iff data_operations is non-empty; else false.
- needs_destination: true ONLY when pipeline_ops includes map, migrate, or create (new workbook/sheet).
  If pipeline_ops is only ["extract"] (or extract + data_ops), needs_destination MUST be false.
- Phrases after "from {org/connection}" describe the SOURCE (entity/fields), NOT destination_phrase.
  Examples: "extract company details from SF 2", "pull contact details from Production" → source only;
  do NOT set destination_phrase to "company details" or "contact details".
- create_destination: true when user wants a NEW spreadsheet/workbook (not an existing named sheet).
- spreadsheet_title: human title for new workbook when create is requested; null if unspecified.
- spreadsheet_headers: column header names when user specifies them in THIS message; else [].
- spreadsheet_header_source: where create-sheet column headers come from:
  • from_extraction — reuse columns from extract step or active job (export / continuation to NEW sheet); spreadsheet_headers MUST be [].
  • from_user — blank new sheet; user must supply columns via clarification; spreadsheet_headers [] unless they listed columns inline.
  • user_specified_inline — user named columns in THIS message; set spreadsheet_headers to that list.
- join/combine multiple sources (tabs, workbooks, SF orgs, SQL DBs): pipeline_ops MUST include
  extract and join (add map+migrate when exporting to a target). Set extract_sources[] on PLAN_SCOPE
  with one entry per named source; do NOT use a separate cross-workbook flag.
- needs_source: true when pipeline_ops includes extract, join, deploy_schema, upsert, or search and
  the user names a source connector/workbook (not file upload). false for create-only or continuation
  data_ops/map/migrate on an existing job (source already on job).
- sync / migrate / replicate / copy data between two systems (e.g. SF org to SF org, DB to SF):
  pipeline_ops MUST include extract, map, and migrate. needs_destination: true.
  Do NOT add create unless user explicitly asks for a NEW spreadsheet/workbook/sheet.
- "build pipeline" / "sync pipeline" between named connectors = compound extract+map+migrate, not create-only.
""" + _PLAN_SCOPE_USER_VOCABULARY


def _apply_plan_scope_llm_data(
    base: Dict[str, Any],
    data: Dict[str, Any],
    *,
    data_ops_requested_override: Optional[bool] = None,
) -> Dict[str, Any]:
    """Merge PLAN_SCOPE LLM JSON — pipeline_ops + data_operations only."""
    out = dict(base)
    pipeline: List[str] = []
    data_ops: List[str] = []

    if data_ops_requested_override is not None:
        requested = bool(data_ops_requested_override)
    else:
        requested = bool(data.get("data_ops_requested"))

    raw_pipeline = data.get("pipeline_ops") or data.get("operations")
    if isinstance(raw_pipeline, list) and raw_pipeline:
        pl, embedded = split_pipeline_and_data_operations(
            [str(o).lower() for o in raw_pipeline]
        )
        pipeline = pl
        if requested and embedded:
            data_ops.extend(embedded)

    if requested and isinstance(data.get("data_operations"), list):
        for raw in data["data_operations"]:
            norm = _normalize_data_op(str(raw))
            if norm:
                data_ops.append(norm)

    if pipeline:
        out["operations"] = pipeline
    if requested and data_ops:
        from agent.planner import _canonicalize_operations_list

        out["data_operations"] = _canonicalize_operations_list(data_ops)
    else:
        out.pop("data_operations", None)

    if "needs_destination" in data:
        out["needs_destination"] = bool(data["needs_destination"])

    if "needs_source" in data:
        out["needs_source"] = bool(data["needs_source"])

    if isinstance(data.get("extract_sources"), list):
        sources = []
        for raw in data["extract_sources"]:
            if not isinstance(raw, dict):
                continue
            entry = {
                k: v
                for k, v in raw.items()
                if k in ("connector_type", "display_name", "sheet_name", "workbook_name")
                and v is not None
            }
            if entry.get("display_name") or entry.get("workbook_name"):
                if entry.get("workbook_name") and not entry.get("display_name"):
                    entry["display_name"] = entry.pop("workbook_name")
                sources.append(entry)
        if sources:
            out["extract_sources"] = sources
            if len(sources) >= 2:
                out["multi_source_extract"] = True

    if "create_destination" in data:
        out["create_destination"] = bool(data["create_destination"])
    elif "create" in pipeline:
        out["create_destination"] = True

    title = data.get("spreadsheet_title")
    if title is not None and str(title).strip():
        out["spreadsheet_title"] = str(title).strip()

    if isinstance(data.get("spreadsheet_headers"), list):
        headers = [str(h).strip() for h in data["spreadsheet_headers"] if str(h).strip()]
        if headers:
            out["spreadsheet_headers"] = headers

    from services.planning.source_column_headers import (
        HEADER_SOURCE_FROM_EXTRACTION,
        HEADER_SOURCE_FROM_USER,
        HEADER_SOURCE_USER_SPECIFIED_INLINE,
        normalize_spreadsheet_header_source,
    )

    raw_header_source = data.get("spreadsheet_header_source")
    if raw_header_source is not None:
        normalized_source = normalize_spreadsheet_header_source(raw_header_source)
        if normalized_source:
            out["spreadsheet_header_source"] = normalized_source

    if "extract" in pipeline and ("create" in pipeline or out.get("create_destination")):
        out.pop("spreadsheet_headers", None)
        out["spreadsheet_header_source"] = HEADER_SOURCE_FROM_EXTRACTION
    elif out.get("spreadsheet_headers") and not out.get("spreadsheet_header_source"):
        out["spreadsheet_header_source"] = HEADER_SOURCE_USER_SPECIFIED_INLINE
    elif ("create" in pipeline or out.get("create_destination")) and not out.get(
        "spreadsheet_header_source"
    ):
        out["spreadsheet_header_source"] = HEADER_SOURCE_FROM_USER

    if out.get("spreadsheet_header_source") == HEADER_SOURCE_FROM_EXTRACTION:
        out.pop("spreadsheet_headers", None)

    if "load_flow" in data:
        out["load_flow"] = bool(data["load_flow"])
    if "save_flow" in data:
        out["save_flow"] = bool(data["save_flow"])

    if "confidence" in data:
        try:
            out["confidence"] = float(data["confidence"])
        except (TypeError, ValueError):
            pass

    if data.get("destination_same_as_source"):
        out["destination_same_as_source"] = True

    sp = (data.get("source_phrase") or "").strip()
    if sp:
        out["source_phrase"] = sp

    dp = (data.get("destination_phrase") or "").strip()
    if dp:
        out["destination_phrase"] = dp

    jk = (data.get("join_key") or "").strip()
    if jk:
        out["join_key"] = jk

    from services.planning.plan_scope import finalize_plan_scope_goal

    return finalize_plan_scope_goal(out)


def resolve_plan_scope_from_classification(
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
    *,
    plan: Optional[Any] = None,
    allow_fallback: bool = False,
) -> Dict[str, Any]:
    """
    Resolve scope goal dict for step assembly from classification._plan_scope.

    Requires PLAN_SCOPE output on classification._plan_scope or plan.metadata.goal.
    parse_goal() fallback only when allow_fallback=True (inner load_flow rebuild).
    """
    from services.planning.plan_scope import read_plan_scope

    stored = read_plan_scope(classification) or None
    if not stored and plan is not None:
        meta = getattr(plan, "metadata", None) or {}
        stored = meta.get("goal") if isinstance(meta, dict) else None
    if stored:
        goal = dict(stored)
        pipeline, embedded = split_pipeline_and_data_operations(
            list(goal.get("operations") or [])
        )
        goal["operations"] = pipeline
        data_ops = list(goal.get("data_operations") or [])
        if not data_ops and embedded:
            data_ops = embedded
        if not data_ops:
            data_ops = resolve_data_operations(
                classification, prompt, goal
            )
        goal["data_operations"] = data_ops
        from services.planning.scope_validator import validate_plan_scope_goal

        return validate_plan_scope_goal(goal, classification)

    if allow_fallback:
        logger.warning(
            "build_plan_steps without PLAN_SCOPE (_plan_scope); using parse_goal fallback"
        )
        goal = parse_goal(classification, prompt)
        goal["data_operations"] = resolve_data_operations(
            classification, prompt, goal
        )
        from services.planning.scope_validator import validate_plan_scope_goal

        return validate_plan_scope_goal(goal, classification)

    raise MissingPlanScopeError(
        "build_plan_steps requires PLAN_SCOPE: set classification['_plan_scope'] "
        "or plan.metadata['goal'] before building steps"
    )


def parse_goal(
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
    *,
    scope_base_only: bool = False,
) -> Dict[str, Any]:
    """
    Build goal-shaped dict for PLAN_SCOPE input or legacy inner rebuild.

    When scope_base_only=True (fresh planning path), operations are empty — PLAN_SCOPE LLM
    is the sole authority for pipeline_ops.
    """
    intent = (classification.get("intent") or "").lower()
    mode = (classification.get("mode") or "").lower()
    operations: List[str] = [] if scope_base_only else list(classification.get("operations") or [])

    if not scope_base_only and not operations:
        if intent in ("map_data",) or mode == "map":
            operations = ["map"]
        elif intent in ("migrate_data", "migrate") or mode == "migrate":
            last = (classification.get("last_completed_step") or "").lower()
            progress = classification.get("_session_progress") or {}
            tokens = list(classification.get("_continuation_steps") or classification.get("_pipeline_tokens") or [])
            if (
                classification.get("active_job_id")
                and "migrate" in tokens
                and (last == "map" or progress.get("mapping_ready"))
            ):
                operations = ["migrate"]
            elif classification.get("active_job_id") and "map" in tokens:
                operations = ["map"]
            else:
                operations = ["extract", "migrate"]
        elif intent == "metadata_deploy":
            operations = ["deploy_schema"]
        elif intent in ("extract",):
            operations = ["extract"]
        elif intent in ("create",) or mode == "create":
            operations = ["create"]
        elif mode == "data_ops":
            operations = []
        else:
            operations = []

    entities: List[str] = []
    for key in ("document_type", "object", "primary_object", "entity"):
        val = classification.get(key)
        if val and str(val).strip():
            entities.append(str(val).strip())

    from services.planning.endpoint_resolver import extract_endpoint_signals_from_classification

    endpoint_signals = extract_endpoint_signals_from_classification(classification)

    return {
        "intent": intent or "extract",
        "mode": mode or "extract",
        "operations": operations,
        "entities": entities,
        "create_destination": bool(classification.get("planner_create_spreadsheet")),
        "load_flow": bool(classification.get("load_flow") or classification.get("flow_id")),
        "save_flow": bool(classification.get("save_flow")),
        "destination_same_as_source": endpoint_signals["destination_same_as_source"],
        "in_place_transform": endpoint_signals["in_place_transform"],
        "source_phrase": endpoint_signals.get("source_phrase"),
        "destination_phrase": endpoint_signals.get("destination_phrase"),
    }


async def enrich_scope_from_llm(
    classification: Dict[str, Any],
    prompt: str,
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    PLAN_SCOPE LLM: pipeline ops, data ops, and create/join metadata (no connector catalog names).
    """
    base = parse_goal(classification, prompt, scope_base_only=True)
    if not (prompt or "").strip():
        return base

    from services.planning.observability import log_plan_scope_trace

    log_plan_scope_trace(
        "llm_start",
        conversation_id=conversation_id,
        user_id=str(user_id),
    )

    from services.planning.plan_context import effective_planning_prompt

    scope_prompt_text = effective_planning_prompt(
        prompt,
        conversation_history=conversation_history,
    )

    import json
    import re

    learning_block = ""
    failure_block = ""
    try:
        from services.planning.continuation_policy import get_failure_context_for_planning

        failure_block = await get_failure_context_for_planning(
            user_id=str(user_id),
            prompt=scope_prompt_text,
            conversation_id=conversation_id,
            conversation_history=conversation_history,
            classification=classification,
        )
    except Exception:
        pass
    try:
        from services.pipeline_learning.service import PipelineLearningService

        if PipelineLearningService.enabled():
            learning_block = await PipelineLearningService.get_planning_retrieval_context(
                user_id=str(user_id),
                query_text=scope_prompt_text,
                source_type=classification.get("source"),
                destination_type=classification.get("destination"),
                conversation_id=conversation_id,
            )
            if learning_block:
                learning_block = learning_block + "\n\n"
    except Exception:
        pass

    from services.intent.routing_context import (
        build_session_routing_block,
    )

    session_decision = classification.get("_session_continuation") or classification
    session_action = (
        (session_decision.get("session_action") if isinstance(session_decision, dict) else getattr(session_decision, "session_action", None))
        or classification.get("session_action")
        or "no_prior_job"
    )
    session_action = str(session_action).strip()

    fresh_extract_mode = session_action in ("fresh_extract", "no_prior_job")
    fresh_full_mode = session_action == "fresh_full_pipeline"

    scope_mode_block = ""
    if fresh_extract_mode and not fresh_full_mode:
        scope_mode_block = """
**S01 mode: fresh_extract (authoritative)**
- User is pulling NEW data from a source only unless they explicitly name a TO target or load/export language in this thread.
- Default pipeline_ops: ["extract"] with needs_destination: false.
- Do NOT add map, migrate, or create unless the user clearly asks to load/send/copy/export TO a named destination in the conversation below.
"""
    elif fresh_full_mode:
        scope_mode_block = """
**S01 mode: fresh_full_pipeline (authoritative)**
- User wants extract AND load/migrate in one compound request, or explicit sync between named systems.
- pipeline_ops may include extract, map, migrate, create as user intent requires.
"""

    routing_block = build_session_routing_block(
        conversation_history,
        current_prompt=scope_prompt_text or "",
        session_decision=session_decision,
        progress_summary=classification.get("_session_progress"),
    )

    classifier_hint = ""
    scope_prompt = f"""{failure_block}{learning_block}Decide pipeline SCOPE for a FRESH request.
Return pipeline_ops, data_operations, data_ops_requested, create_destination, spreadsheet_title,
spreadsheet_headers, spreadsheet_header_source, needs_source, needs_destination, source_phrase, destination_phrase, confidence.
Do NOT include connector catalog names (org names, SQL connection names, existing spreadsheet IDs).
{scope_mode_block}
{_PLAN_SCOPE_SINGLE_COMPOUND_RULES}

{routing_block}

User message (include thread goal):
\"\"\"{scope_prompt_text}\"\"\"
{classifier_hint}

Fresh rules:
- pipeline_ops: extract, map, migrate, create, deploy_schema, join, upsert, update, search, formula
  (NOT filter/dedupe/validate/transform — those go in data_operations).
- Use user vocabulary above: move/export/copy/send/load TO a target = map+migrate (with extract if new pull).
- data_operations: filter, dedupe, validate, transform when user asks cleanup in THIS message.
- When S01 says no_prior_job or fresh_*: scope from current message + conversation — do NOT add extract/map because a prior job exists in history.
- When user refers to a spreadsheet created earlier in this chat, set destination_phrase to that workbook name from session facts (not "google_sheets").
- Create new sheet/spreadsheet only → pipeline_ops: ["create"], create_destination: true, no extract.
- create only (new workbook, no extract): {{"pipeline_ops": ["create"], "create_destination": true, "spreadsheet_title": "...", "spreadsheet_header_source": "from_user", "spreadsheet_headers": [], "needs_destination": true}}
- create with inline columns: {{"pipeline_ops": ["create"], "create_destination": true, "spreadsheet_title": "CRM", "spreadsheet_header_source": "user_specified_inline", "spreadsheet_headers": ["Name", "Email"], "needs_destination": true}}
- extract + create compound: spreadsheet_header_source MUST be "from_extraction" and spreadsheet_headers MUST be [] (sheet columns come from extracted fields, not guessed CRM columns).

Single examples:
- extract only: {{"pipeline_ops": ["extract"], "data_operations": [], "data_ops_requested": false}}
- create sheet only: {{"pipeline_ops": ["create"], "create_destination": true, "spreadsheet_title": "CRM", "spreadsheet_header_source": "user_specified_inline", "spreadsheet_headers": ["Name", "Email"], "needs_destination": true}}
- extract + data_ops: {{"pipeline_ops": ["extract"], "data_operations": ["filter"], "data_ops_requested": true}}

Compound examples (return ALL requested families):
- extract + data_ops + map: {{"pipeline_ops": ["extract", "map"], "data_operations": ["filter"], "data_ops_requested": true, "needs_destination": true}}
- extract + create + map + migrate: {{"pipeline_ops": ["extract", "create", "map", "migrate"], "create_destination": true, "needs_destination": true, "spreadsheet_header_source": "from_extraction", "spreadsheet_headers": []}}
- SF org to SF org contact sync: {{"pipeline_ops": ["extract", "map", "migrate"], "needs_destination": true, "data_ops_requested": false}}
- join two tabs same workbook: {{"pipeline_ops": ["extract", "join"], "join_key": "Email", "needs_destination": false}}
- join sources and export: {{"pipeline_ops": ["extract", "join", "map", "migrate"], "join_key": "Email", "needs_destination": true}}
- upsert only: {{"pipeline_ops": ["upsert"], "needs_destination": true}}
- deploy metadata only: {{"pipeline_ops": ["deploy_schema"], "needs_destination": true}}
- search only: {{"pipeline_ops": ["search"]}}
- formula only: {{"pipeline_ops": ["formula"]}}
"""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_SCOPE,
            messages=[{"role": "user", "content": scope_prompt}],
            max_tokens=400,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            log_plan_scope_trace(
                "llm_no_json",
                conversation_id=conversation_id,
                user_id=str(user_id),
                reason="response had no JSON object",
            )
            return base
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            base = _apply_plan_scope_llm_data(base, data)
            log_plan_scope_trace(
                "llm_parsed",
                conversation_id=conversation_id,
                user_id=str(user_id),
                pipeline_ops=data.get("pipeline_ops") or data.get("operations"),
                needs_source=data.get("needs_source"),
                needs_destination=data.get("needs_destination"),
                source_phrase=(data.get("source_phrase") or "").strip() or None,
                destination_phrase=(data.get("destination_phrase") or "").strip() or None,
            )
    except Exception as exc:
        log_plan_scope_trace(
            "llm_failed",
            conversation_id=conversation_id,
            user_id=str(user_id),
            reason=str(exc)[:200],
        )

    log_plan_scope_trace(
        "after_merge",
        conversation_id=conversation_id,
        user_id=str(user_id),
        operations=base.get("operations"),
        needs_source=base.get("needs_source"),
        needs_destination=base.get("needs_destination"),
        source_phrase=base.get("source_phrase"),
        destination_phrase=base.get("destination_phrase"),
    )

    return base


async def enrich_continuation_scope_from_llm(
    classification: Dict[str, Any],
    prompt: str,
    *,
    user_id: str,
    job: Any,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict]] = None,
    session_decision: Optional[Any] = None,
) -> Dict[str, Any]:
    """PLAN_SCOPE for continuation: scope on existing job (never extract)."""
    base = parse_goal(classification, prompt)
    if (classification.get("active_job_id") or "").strip():
        base["operations"] = []
        base.pop("needs_destination", None)
    if not (prompt or "").strip():
        return base

    import json
    import re

    job_summary = {
        "job_id": job.job_id,
        "pipeline_stage": job.pipeline_stage,
        "last_completed_step": job.last_completed_step,
        "mapping_ready": job.mapping_ready,
        "source_type": job.source.connector_type,
        "source_name": job.source.display_name,
        "source_entity": job.source.entity_key,
    }
    if job.destination:
        job_summary["destination_type"] = job.destination.connector_type
        job_summary["destination_name"] = job.destination.display_name
        job_summary["destination_entity"] = job.destination.entity_key

    phase = "none"
    if session_decision is not None:
        phase = getattr(session_decision, "continuation_phase", None) or "none"

    from services.intent.routing_context import build_session_routing_block

    progress = None
    if session_decision is not None:
        progress = getattr(session_decision, "session_progress", None)
    routing_block = build_session_routing_block(
        conversation_history,
        current_prompt=prompt or "",
        session_decision=session_decision,
        job_summary=job_summary,
        progress_summary=progress,
    )

    scope_prompt = f"""Decide pipeline SCOPE for work on an EXISTING extracted dataset.
NEVER include "extract" in pipeline_ops — data is already in the active job.
Return pipeline_ops, data_operations, data_ops_requested, needs_source, needs_destination, confidence,
destination_phrase, destination_same_as_source, create_destination, spreadsheet_title,
spreadsheet_header_source, spreadsheet_headers (optional source_phrase).
Do NOT return connector catalog IDs — only human phrases for endpoints.
{_PLAN_SCOPE_SINGLE_COMPOUND_RULES}

{routing_block}

S01 continuation phase (hint only — your pipeline_ops answer is authoritative):
{phase}

User message: "{prompt}"

Return JSON (shape reference — pick ops from user intent, not this default):
{_PLAN_SCOPE_CONTINUATION_JSON_EXAMPLE}

Continuation rules:
- NEVER include "extract" in pipeline_ops — data is already in the active job.
- pipeline_ops: map, migrate, create, deploy_schema, join per vocabulary above (MAP vs MIGRATE disambiguation).
- Export/move/copy extracted rows to a NEW spreadsheet/workbook (not an existing named sheet):
  pipeline_ops MUST include create (and map+migrate when loading data); create_destination: true;
  spreadsheet_header_source: "from_extraction"; spreadsheet_headers: [].
- data_operations: when user intent matches DATA OPS vocabulary in THIS message.
- Do NOT add map/migrate only because extract finished — require explicit map or migrate/load intent.
- S01 continuation_phase is a hint only — user vocabulary in THIS message is authoritative for ops.
  phase=map → expect map-only unless user uses migrate/load vocabulary.
  phase=migrate → expect migrate (return ["migrate"] only; map is added when mapping_ready is false).
- destination_phrase: named map/migrate target — use the FULL label after to/into (e.g. "SF 1 XYZ list", not a prefix shared with source_name alone).
- destination_same_as_source: true ONLY when user explicitly wants the same system as the job source (same org/database/sheet). NOT when they name a different workbook or connection, even if the phrase contains words from source_name.
- When destination_same_as_source is true, you MUST still set workbook_name or display_name to the catalog entry matching job source_name (same target as extract source).
- needs_destination: true when pipeline_ops includes map, migrate, or create (new workbook).
- needs_source: false on continuation (source is on the active job) unless user names a NEW source.

Single examples:
- data_ops only: {{"pipeline_ops": [], "data_operations": ["filter"], "data_ops_requested": true}}
- map only: {{"pipeline_ops": ["map"], "data_operations": [], "data_ops_requested": false, "needs_destination": true, "destination_phrase": "Production org"}}
- map extracted data to org (map vocabulary): {{"pipeline_ops": ["map"], "data_operations": [], "data_ops_requested": false, "needs_destination": true, "destination_phrase": "SF 2 org"}}
- map same source: {{"pipeline_ops": ["map"], "needs_destination": true, "destination_same_as_source": true, "destination_phrase": "<exact source_name from Active job>"}}
- move/load to sheet (migrate vocabulary, no cleanup): {{"pipeline_ops": ["migrate"], "data_operations": [], "data_ops_requested": false, "needs_destination": true, "destination_phrase": "SF 1 XYZ list"}}
- load extracted data to sheet (migrate vocabulary): {{"pipeline_ops": ["migrate"], "data_operations": [], "data_ops_requested": false, "needs_destination": true, "destination_phrase": "Contact Export"}}
- export extracted data to NEW google sheet: {{"pipeline_ops": ["create", "map", "migrate"], "create_destination": true, "needs_destination": true, "spreadsheet_header_source": "from_extraction", "spreadsheet_headers": []}}

Compound examples (return ALL requested families):
- data_ops + map: {{"pipeline_ops": ["map"], "data_operations": ["filter"], "data_ops_requested": true, "needs_destination": true, "destination_phrase": "Warehouse DB"}}
- data_ops + migrate: {{"pipeline_ops": ["migrate"], "data_operations": ["filter"], "data_ops_requested": true, "needs_destination": true, "destination_phrase": "Test DB"}}
- cleanup + move to sheet: {{"pipeline_ops": ["migrate"], "data_operations": ["filter"], "data_ops_requested": true, "needs_destination": true, "destination_phrase": "SF 1 XYZ list"}}
"""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_SCOPE,
            messages=[{"role": "user", "content": scope_prompt}],
            max_tokens=280,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return base
        data = json.loads(m.group(0))
        if isinstance(data, dict):
            if isinstance(data.get("pipeline_ops"), list):
                data = dict(data)
                data["pipeline_ops"] = [
                    str(o).lower()
                    for o in data["pipeline_ops"]
                    if str(o).lower() != "extract"
                ]
            base = _apply_plan_scope_llm_data(base, data)
    except Exception:
        pass

    from services.planning.scope_validator import validate_plan_scope_goal

    base = validate_plan_scope_goal(base, classification)
    from services.planning.plan_scope import apply_continuation_scope_cap

    base = apply_continuation_scope_cap(
        base,
        continuation_phase=str(phase),
        mapping_ready=bool(getattr(job, "mapping_ready", False)),
    )

    from services.planning.continuation_observability import log_continuation_plan_scope

    log_continuation_plan_scope(
        job_id=str(getattr(job, "job_id", "") or ""),
        operations=base.get("operations"),
        destination_phrase=base.get("destination_phrase"),
        destination_same_as_source=base.get("destination_same_as_source"),
        needs_destination=base.get("needs_destination"),
        data_operations=base.get("data_operations"),
    )
    return base


# --- Goal data operations ---
ALLOWED_DATA_OPS = frozenset({"transform", "filter", "dedupe", "validate"})
_PIPELINE_OPS = frozenset(
    {
        "extract",
        "map",
        "migrate",
        "deploy",
        "deploy_schema",
        "join",
        "create",
        "upsert",
        "update",
        "search",
        "formula",
        "load_flow",
        "save_flow",
    }
)
_DATA_OP_ALIASES = {
    "deduplication": "dedupe",
    "validation": "validate",
    "transformation": "transform",
    "cleanup": "validate",
    "cleanup_values": "validate",
}


def _normalize_data_op(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower()
    key = _DATA_OP_ALIASES.get(key, key)
    return key if key in ALLOWED_DATA_OPS else None


def prompt_requests_post_extract_data_ops(
    prompt: Optional[str],
    classification: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when classification/PLAN_SCOPE indicates post-extract data work (not keywords)."""
    del prompt
    if not classification:
        return False
    from services.planning.plan_scope import continuation_pipeline_tokens, read_plan_scope

    if "data_ops" in continuation_pipeline_tokens(classification, read_plan_scope(classification)):
        return True
    scope = read_plan_scope(classification)
    if scope.get("data_operations") or scope.get("data_ops_requested"):
        if classification.get("active_job_id") or (
            classification.get("plan_route") or ""
        ) == "continuation_path":
            return True
    return bool(classification.get("active_job_id") and classification.get("data_operations"))


def resolve_data_operations(
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
    goal: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Dedupe/filter/validate/transform ops for a compound plan.

    Plan assembly uses PLAN_SCOPE / classifier fields only (no prompt keyword inference).
    """
    del prompt
    from agent.planner import _canonicalize_operations_list

    candidates: List[str] = []

    for source in (
        (goal or {}).get("data_operations"),
        classification.get("data_operations"),
    ):
        if isinstance(source, list):
            for item in source:
                norm = _normalize_data_op(str(item))
                if norm:
                    candidates.append(norm)

    for op in (goal or {}).get("operations") or []:
        norm = _normalize_data_op(str(op))
        if norm:
            candidates.append(norm)

    for op in classification.get("operations") or []:
        norm = _normalize_data_op(str(op))
        if norm:
            candidates.append(norm)

    scope = classification.get("_plan_scope") or {}
    if isinstance(scope, dict):
        for op in scope.get("data_operations") or []:
            norm = _normalize_data_op(str(op))
            if norm:
                candidates.append(norm)

    return _canonicalize_operations_list(candidates)


def split_pipeline_and_data_operations(
    operations: List[str],
) -> tuple[List[str], List[str]]:
    """Partition mixed operations[] into pipeline vs post-extract data ops."""
    pipeline: List[str] = []
    data_ops: List[str] = []
    for raw in operations or []:
        key = (raw or "").strip().lower()
        norm = _normalize_data_op(key)
        if norm:
            data_ops.append(norm)
        elif key in _PIPELINE_OPS:
            if key == "deploy":
                pipeline.append("deploy_schema")
            else:
                pipeline.append(key)
    from agent.planner import _canonicalize_operations_list

    return pipeline, _canonicalize_operations_list(data_ops)


def data_ops_step_label(data_ops: List[str]) -> str:
    from utils.operation_labels import label_for_operation

    if not data_ops:
        return "Process extracted data"
    if len(data_ops) == 1:
        return label_for_operation(data_ops[0])
    parts = [label_for_operation(op) for op in data_ops]
    return "Process extracted data (" + ", ".join(parts) + ")"

