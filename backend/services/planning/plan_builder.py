"""Build ExecutionPlan steps from resolved classification (connector-agnostic templates)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from services.planning.goal_parser import parse_goal
from services.planning.schemas import (
    ExecutionPlan,
    PlanStep,
    ResourceRef,
    StepType,
    normalize_connector_type,
)


def _destination_is_known(
    dest_type: Optional[str],
    dest_name: Optional[str] = None,
) -> bool:
    """True when the user (or classifier) named a real export/migrate target."""
    from services.planning.connector_guards import is_known_connector, is_unknown_resource_name

    if not is_known_connector(dest_type):
        return False
    return not is_unknown_resource_name(dest_name)


def _ref(
    connector_type: str,
    display_name: Optional[str],
    *,
    spreadsheet_id: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> Optional[ResourceRef]:
    if not display_name or str(display_name).strip().lower() in ("unknown", ""):
        return None
    return ResourceRef(
        connector_type=normalize_connector_type(connector_type),  # type: ignore[arg-type]
        display_name=str(display_name).strip(),
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
    )


def _destination_display_label(
    classification: Dict[str, Any],
    dest_ref: Optional[ResourceRef],
    dest_name: Optional[str],
) -> Optional[str]:
    name = (dest_name or (dest_ref.display_name if dest_ref else None) or "").strip()
    if not name:
        return None
    tab = (classification.get("destination_sheet_name") or "").strip()
    if not tab and dest_ref and dest_ref.sheet_name:
        tab = dest_ref.sheet_name.strip()
    if tab and tab.lower() not in ("sheet1",) and tab not in name:
        return f"{name} ({tab})"
    return name


def _format_source_label(src_item: Dict[str, Any], fallback_type: str) -> str:
    dn = (src_item.get("display_name") or "").strip()
    if dn:
        tab = (src_item.get("sheet_name") or "").strip()
        if tab and tab.lower() not in ("sheet1",) and tab not in dn:
            return f"{dn} ({tab})"
        return dn
    return fallback_type or "source"


def _destination_requires_mapping(dest_type: str) -> bool:
    return (dest_type or "").lower() in (
        "google_sheets",
        "gsheets",
        "salesforce",
        "sql",
    )


def _route_endpoints_differ(
    classification: Dict[str, Any],
    src_type: str,
    dest_type: str,
) -> bool:
    """
    True when source and destination are not the same logical endpoint.

    Salesforce org A → org B shares connector type ``salesforce`` but must still
    run extract → map → migrate (not ``extract_only``).
    """
    st = (src_type or "").lower()
    dt = (dest_type or "").lower()
    if st != dt:
        return True
    if st in ("salesforce", "google_sheets", "gsheets", "sql"):
        sn = (classification.get("source_name") or "").strip().lower()
        dn = (classification.get("destination_name") or "").strip().lower()
        return bool(sn and dn and sn != dn)
    return False


def _resolve_plan_scope(
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
    plan: Optional[ExecutionPlan] = None,
) -> Dict[str, Any]:
    """Build plan scope goal dict from classification._plan_scope."""
    from services.planning.goal_parser import resolve_plan_scope_from_classification

    allow_fallback = bool(classification.get("_inner_plan_steps"))
    return resolve_plan_scope_from_classification(
        classification,
        prompt,
        plan=plan,
        allow_fallback=allow_fallback,
    )


def _goal_data_operations(
    goal: Dict[str, Any],
    classification: Dict[str, Any],
    prompt: Optional[str],
) -> List[str]:
    from services.planning.goal_parser import resolve_data_operations

    existing = list(goal.get("data_operations") or [])
    if existing:
        return existing
    return resolve_data_operations(
        classification, prompt, goal
    )


def _append_data_ops_step_if_requested(
    steps: List[PlanStep],
    *,
    goal: Dict[str, Any],
    classification: Dict[str, Any],
    prompt: Optional[str],
    depends_on: List[str],
    sid: str = "data_ops",
) -> Optional[str]:
    from services.planning.goal_parser import data_ops_step_label

    data_ops = _goal_data_operations(goal, classification, prompt)
    if not data_ops:
        return None
    extra: Dict[str, Any] = {}
    if classification.get("active_job_id"):
        extra["parent_job_id"] = classification.get("active_job_id")
    steps.append(
        _step(
            StepType.TRANSFORM,
            "run_data_ops_plan",
            label=data_ops_step_label(data_ops),
            depends_on=depends_on,
            steps=[
                {"operation_id": op, "prompt": (prompt or "").strip()}
                for op in data_ops
            ],
            **extra,
        )
    )
    steps[-1].parameters["step_id"] = sid
    return sid


def _goal_ops(goal: Dict[str, Any]) -> List[str]:
    return list(goal.get("operations") or [])


def _goal_requests_migrate(goal: Dict[str, Any]) -> bool:
    return "migrate" in _goal_ops(goal)


def _goal_requests_map(goal: Dict[str, Any], dest_type: str) -> bool:
    """Map step only when the user's goal operations explicitly include map."""
    return "map" in _goal_ops(goal)


def _is_migrate_only_goal(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    prompt: Optional[str],
) -> bool:
    """Existing extraction job + migrate in goal, no new extract or map step."""
    if not _goal_requests_migrate(goal):
        return False
    if "extract" in _goal_ops(goal):
        return False
    if _goal_requests_map(goal, (classification.get("destination") or "")):
        return False
    return bool(classification.get("active_job_id"))


def _needs_multi_source_build(classification: Dict[str, Any], goal: Dict[str, Any]) -> bool:
    if classification.get("planner_additional_sources") or classification.get("multi_source_pipeline"):
        return True
    return bool(goal.get("multi_source_extract"))


def _needs_load_flow(classification: Dict[str, Any], goal: Dict[str, Any]) -> bool:
    if classification.get("_inner_plan_steps"):
        return False
    return bool(
        classification.get("flow_id")
        or classification.get("load_flow")
        or goal.get("load_flow")
    )


def _goal_requests_save_flow(goal: Dict[str, Any], classification: Dict[str, Any]) -> bool:
    return bool(classification.get("save_flow") or goal.get("save_flow"))


def _build_multi_source_steps(
    steps: List[PlanStep],
    *,
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    prompt: Optional[str],
    src_type: str,
    src_name: Optional[str],
    src_ref: Optional[ResourceRef],
    dest_ref: Optional[ResourceRef],
    dest_name: Optional[str],
    dest_type: str,
    out_plan: ExecutionPlan,
) -> None:
    extra_sources = classification.get("planner_additional_sources") or []
    all_sources = [{"connector_type": src_type, "display_name": src_name, **{}}]
    if src_name:
        all_sources[0] = {
            "connector_type": src_type,
            "display_name": src_name,
            "sheet_name": classification.get("source_sheet_name"),
        }
    for item in extra_sources:
        if isinstance(item, dict) and item.get("display_name"):
            all_sources.append(item)
    dest_label = _destination_display_label(classification, dest_ref, dest_name)
    prev_dep: List[str] = []
    for idx, src_item in enumerate(all_sources):
        ct = (src_item.get("connector_type") or src_type or "unknown").lower()
        dn = src_item.get("display_name") or src_name
        sid = f"extract_{idx}"
        steps.append(
            _step(
                StepType.EXTRACT,
                _extract_tool(ct, classification=classification, prompt=prompt),
                label=f"Extract from {_format_source_label(src_item, dn or ct)}",
                source=_ref(ct, dn, sheet_name=src_item.get("sheet_name")),
                destination=dest_ref,
                source_name=dn,
                depends_on=prev_dep,
                classification=classification,
            )
        )
        steps[-1].parameters["step_id"] = sid
        prev_dep = [sid]
    if len(all_sources) > 1:
        source_names = [
            _format_source_label(item, item.get("display_name") or "")
            for item in all_sources
            if isinstance(item, dict)
        ]
        source_names = [n for n in source_names if n]
        combine_label = (
            f"Combine {' + '.join(source_names)}"
            if len(source_names) >= 2
            else "Combine sources"
        )
        steps.append(
            _step(
                StepType.JOIN,
                "gsheets_execute_operation",
                label=combine_label,
                source=src_ref,
                destination=dest_ref,
                depends_on=prev_dep,
                classification=classification,
            )
        )
        steps[-1].parameters["step_id"] = "join"
        steps[-1].parameters["operation"] = "join"
        prev_dep = ["join"]
    if _multi_source_needs_destination_step(
        classification, dest_type, dest_name, dest_ref, len(all_sources)
    ):
        needs_map = _destination_requires_mapping(dest_type)
        export_label = f"Export to {dest_label or dest_name or dest_type}"
        if needs_map:
            sid_map = "map"
            steps.append(
                _step(
                    StepType.MAP,
                    "map_fields",
                    label=f"Map fields to {dest_label or dest_name or dest_type}",
                    source=src_ref,
                    destination=dest_ref,
                    depends_on=prev_dep,
                    destination_name=dest_name,
                    classification=classification,
                )
            )
            steps[-1].parameters["step_id"] = sid_map
            prev_dep = [sid_map]
        steps.append(
            _step(
                StepType.MIGRATE,
                "start_migration",
                label=export_label,
                source=src_ref,
                destination=dest_ref,
                depends_on=prev_dep,
                destination_name=dest_name,
                classification=classification,
            )
        )


def _build_retry_partial_steps(
    steps: List[PlanStep],
    *,
    classification: Dict[str, Any],
    src_ref: Optional[ResourceRef],
    dest_ref: Optional[ResourceRef],
    dest_name: Optional[str],
    dest_type: str,
    sid_map: str = "map",
) -> None:
    map_params: Dict[str, Any] = {}
    mapping_ctx = (classification.get("recovery_mapping_context") or "").strip()
    if mapping_ctx:
        map_params["mapping_context_summary"] = mapping_ctx
    steps.append(
        _step(
            StepType.MAP,
            "map_fields",
            label="Re-map fields to fix migration errors",
            source=src_ref,
            destination=dest_ref,
            destination_name=dest_name,
            classification=classification,
            **map_params,
        )
    )
    steps[-1].parameters["step_id"] = sid_map
    steps.append(
        _step(
            StepType.MIGRATE,
            "start_migration",
            label=f"Retry migration to {dest_name or dest_type}",
            source=src_ref,
            destination=dest_ref,
            depends_on=[sid_map],
            destination_name=dest_name,
            classification=classification,
        )
    )


def _build_load_flow_with_inner_plan(
    steps: List[PlanStep],
    *,
    classification: Dict[str, Any],
    prompt: Optional[str],
    out_plan: ExecutionPlan,
) -> None:
    steps.append(
        _step(
            StepType.LOAD_FLOW,
            "load_flow",
            label="Load saved flow",
            flow_id=classification.get("flow_id"),
        )
    )
    inner_cls = dict(classification)
    inner_cls.pop("load_flow", None)
    inner_cls["_inner_plan_steps"] = True
    inner_plan = build_plan_steps(
        inner_cls,
        prompt=prompt,
        plan=ExecutionPlan(
            plan_id=out_plan.plan_id,
            status=out_plan.status,
            metadata=dict(out_plan.metadata),
        ),
    )
    prev_dep = "step_1"
    for st in inner_plan.steps:
        st.depends_on = [prev_dep]
        steps.append(st)
        prev_dep = st.parameters.get("step_id") or prev_dep


def _append_migrate_step_if_requested(
    steps: List[PlanStep],
    *,
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    src_ref: Optional[ResourceRef],
    dest_ref: Optional[ResourceRef],
    src_name: Optional[str],
    dest_name: Optional[str],
    dest_type: str,
    depends_on: List[str],
) -> None:
    """Append start_migration when the user's goal operations include migrate."""
    if not _goal_requests_migrate(goal):
        return
    dest_label = _destination_display_label(classification, dest_ref, dest_name)
    steps.append(
        _step(
            StepType.MIGRATE,
            "start_migration",
            label=f"Migrate to {dest_label or dest_name or dest_type}",
            source=src_ref,
            destination=dest_ref,
            depends_on=depends_on,
            destination_name=dest_name,
            classification=classification,
        )
    )


def _multi_source_goal_summary(
    source_items: List[Dict[str, Any]],
    dest_label: Optional[str],
    *,
    includes_mapping: bool = False,
) -> str:
    names = [
        _format_source_label(item, (item.get("connector_type") or "source"))
        for item in source_items
        if isinstance(item, dict)
    ]
    names = [n for n in names if n]
    if not names:
        return "Extract from multiple sources, combine, map, and export."
    src_phrase = " and ".join(names)
    if dest_label and includes_mapping:
        return (
            f"Extract from {src_phrase}, combine the results, "
            f"map fields to {dest_label}, then export."
        )
    if dest_label:
        return f"Extract from {src_phrase}, combine the results, then export to {dest_label}."
    return f"Extract from {src_phrase} and combine the results."


def _multi_source_needs_destination_step(
    classification: Dict[str, Any],
    dest_type: str,
    dest_name: Optional[str],
    dest_ref: Optional[ResourceRef],
    source_count: int,
) -> bool:
    if not dest_ref or not dest_type or dest_type == "unknown":
        return False
    if not (dest_name or (dest_ref.display_name if dest_ref else None)):
        return False
    if source_count > 1:
        return True
    src_type = (classification.get("source") or "").lower()
    return dest_type != src_type


def _step(
    step_type: StepType,
    tool_id: str,
    *,
    label: str,
    source: Optional[ResourceRef] = None,
    destination: Optional[ResourceRef] = None,
    depends_on: Optional[List[str]] = None,
    classification: Optional[Dict[str, Any]] = None,
    **params: Any,
) -> PlanStep:
    parameters = dict(params)
    if classification:
        parameters = _stamp_entity_params(classification, **parameters)
    parameters["tool_id"] = tool_id
    return PlanStep(
        step_type=step_type,
        label=label,
        source=source,
        destination=destination,
        depends_on=list(depends_on or []),
        parameters=parameters,
    )


def _contract_side(classification: Dict[str, Any], role: str) -> Dict[str, Any]:
    contract = classification.get("plan_contract") or {}
    side = contract.get(role) if isinstance(contract, dict) else None
    return side if isinstance(side, dict) else {}


def _user_facing_entity(classification: Dict[str, Any], role: str) -> str:
    """User-facing entity fragment for plan step labels (via PlanEntityRef)."""
    from services.planning.schemas import PlanEntityRef

    side = _contract_side(classification, role)
    if not side:
        return ""
    try:
        return PlanEntityRef.model_validate(side).step_entity_label()
    except Exception:
        key = (side.get("entity_key") or "").strip()
        return key


def _entity_label(classification: Dict[str, Any], role: str) -> str:
    return _user_facing_entity(classification, role)


def _stamp_entity_params(
    classification: Dict[str, Any],
    **params: Any,
) -> Dict[str, Any]:
    out = dict(params)
    src = _contract_side(classification, "source")
    dest = _contract_side(classification, "destination")
    if src.get("entity_key"):
        out["source_entity_key"] = src["entity_key"]
        out["source_entity_label"] = src.get("catalog_label") or src.get("entity_label") or src["entity_key"]
    if dest.get("entity_key"):
        out["destination_entity_key"] = dest["entity_key"]
        out["destination_entity_label"] = dest.get("catalog_label") or dest.get("entity_label") or dest["entity_key"]
    intent = _migration_intent(classification)
    if intent.get("include_related_data"):
        anchor = (out.get("source_entity_key") or intent.get("driving_entity_key") or "").strip()
        driving = (intent.get("driving_entity_key") or "").strip()
        parent = (intent.get("parent_entity_key") or "").strip()
        join_partners: List[str] = []
        for key in (driving, parent):
            k = (key or "").strip()
            if not k:
                continue
            if anchor and k.lower() == anchor.lower():
                continue
            if not any(k.lower() == p.lower() for p in join_partners):
                join_partners.append(k)
        if join_partners:
            out["related_entity_keys"] = join_partners
    elif intent.get("parent_entity_key"):
        parent_key = intent.get("parent_entity_key")
        if parent_key:
            out["related_entity_keys"] = [parent_key]
    if intent.get("field_hints") and intent.get("field_hints_strict"):
        out["field_hints"] = intent.get("field_hints")
    if intent:
        from services.planning.extract_field_scope import build_extract_field_scope

        out["extract_field_scope"] = build_extract_field_scope(intent)
        out["migration_intent"] = dict(intent)
    return out


def _migration_intent(classification: Dict[str, Any]) -> Dict[str, Any]:
    from services.intent.migration_intent_view import read_migration_intent_dict

    mi = read_migration_intent_dict(classification)
    if mi:
        return mi
    contract = classification.get("plan_contract") or {}
    intent = contract.get("migration_intent") if isinstance(contract, dict) else None
    if isinstance(intent, dict):
        return intent
    alt = classification.get("migration_intent")
    return alt if isinstance(alt, dict) else {}


def _extract_step_label(
    src_name: Optional[str],
    src_type: str,
    classification: Dict[str, Any],
) -> str:
    entity = _user_facing_entity(classification, "source")
    base = src_name or src_type
    intent = _migration_intent(classification)
    if intent.get("include_related_data") and intent.get("parent_entity_label"):
        from services.planning.related_select_fields import format_related_extract_hints

        parent = intent.get("parent_entity_label")
        strict = bool(intent.get("field_hints_strict"))
        hint_part = format_related_extract_hints(
            primary_hints=intent.get("field_hints") or [],
            related_hints=intent.get("related_field_hints") or [],
            field_hints_strict=strict,
            include_related=True,
            parent_label=parent,
        )
        entity = _user_facing_entity(classification, "source")
        if entity:
            return f"Extract {entity} with related {parent} fields{hint_part} in {base}"
    hints = intent.get("field_hints") or []
    strict = bool(intent.get("field_hints_strict"))
    show_hints = hints if strict else []
    hint_part = f" ({', '.join(show_hints)})" if show_hints else ""
    entity = _user_facing_entity(classification, "source")
    if entity and show_hints:
        return f"Extract {entity} fields{hint_part} in {base}"
    if entity:
        return f"Extract from {entity} in {base}"
    return f"Extract from {base}"


def _map_step_label(
    dest_name: Optional[str],
    dest_type: str,
    classification: Dict[str, Any],
) -> str:
    entity = _user_facing_entity(classification, "destination")
    base = dest_name or dest_type
    if entity:
        return f"Map fields to {entity} in {base}"
    return f"Map fields to {base}"


def _migrate_step_label(
    dest_name: Optional[str],
    dest_type: str,
    classification: Dict[str, Any],
) -> str:
    entity = _user_facing_entity(classification, "destination")
    base = dest_name or dest_type
    if entity:
        return f"Migrate {entity} to {base}"
    return f"Migrate to {base}"


def _extract_tool(
    source: str,
    *,
    classification: Optional[Dict[str, Any]] = None,
    prompt: Optional[str] = None,
    conversation_history: Optional[List] = None,
) -> str:
    src = (source or "").lower()
    if src == "file":
        if classification and (
            classification.get("_pending_file_payload")
            or classification.get("_pending_file_ref")
        ):
            return "extract_file"
        active_job = (classification or {}).get("active_job_id")
        if active_job:
            from services.planning.plan_scope import classification_requests_fresh_extraction

            if not classification_requests_fresh_extraction(classification or {}):
                return "file_reextract"
        return "extract_file"
    return {
        "salesforce": "extract_salesforce",
        "sql": "extract_sql",
        "google_sheets": "extract_google_sheets",
        "gsheets": "extract_google_sheets",
    }.get(src, "general_ai_response")


def _build_steps_from_pipeline_tokens(
    tokens: List[str],
    steps: List[PlanStep],
    *,
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    prompt: Optional[str],
    file_extract_tool: str,
    src_type: str,
    src_ref: Optional[ResourceRef],
    dest_ref: Optional[ResourceRef],
    src_name: Optional[str],
    dest_name: Optional[str],
    dest_type: str,
    sid_extract: str = "extract",
    sid_map: str = "map",
) -> None:
    """Materialize PLAN_SCOPE-resolved tokens into PlanSteps with dependency chaining."""
    map_dep: List[str] = []

    if "extract" in tokens:
        steps.append(
            _step(
                StepType.EXTRACT,
                file_extract_tool,
                label=_extract_step_label(src_name, src_type, classification),
                source=src_ref,
                destination=dest_ref,
                source_name=src_name,
                destination_name=dest_name,
                entities=goal.get("entities"),
                classification=classification,
            )
        )
        steps[-1].parameters["step_id"] = sid_extract
        map_dep = [sid_extract]

    if "data_ops" in tokens:
        data_ops_sid = _append_data_ops_step_if_requested(
            steps,
            goal=goal,
            classification=classification,
            prompt=prompt,
            depends_on=map_dep or [],
        )
        if data_ops_sid:
            map_dep = [data_ops_sid]

    if "create" in tokens:
        from services.planning.create_metadata import resolved_spreadsheet_title
        from services.planning.source_column_headers import build_create_header_contract

        new_title = resolved_spreadsheet_title(classification, goal) or (
            (classification.get("planner_new_spreadsheet_name") or "").strip()
        )
        header_contract = build_create_header_contract(
            classification, goal, pipeline_tokens=tokens
        )
        create_dep = [sid_extract] if "extract" in tokens else list(map_dep)
        sid_create = "create_sheet"
        create_params: Dict[str, Any] = {
            "spreadsheet_title": new_title,
            "use_extraction_headers": header_contract.use_extraction_headers,
            "spreadsheet_header_source": header_contract.spreadsheet_header_source,
        }
        if header_contract.headers and not header_contract.use_extraction_headers:
            create_params["headers"] = header_contract.headers
        header_suffix = ""
        if header_contract.headers and not header_contract.use_extraction_headers:
            header_suffix = f" with columns: {', '.join(header_contract.headers[:8])}"
        steps.append(
            _step(
                StepType.CREATE,
                "google_sheets_create_from_prompt",
                label=f"Create spreadsheet{f' {new_title}' if new_title else ''}{header_suffix}",
                destination=_ref("google_sheets", new_title or dest_name),
                depends_on=create_dep or None,
                classification=classification,
                **create_params,
            )
        )
        steps[-1].parameters["step_id"] = sid_create
        map_dep = [sid_create]

    if "join" in tokens:
        # Join/combine runs via multi-source extract plans (_build_multi_source_steps).
        pass

    _GSHEETS_OP_LABELS = {
        "upsert": "Upsert rows",
        "search": "Search spreadsheet",
        "update": "Update spreadsheet metadata",
        "formula": "Write spreadsheet formula",
    }
    _GSHEETS_STEP_TYPES = {
        "upsert": StepType.UPSERT,
        "search": StepType.SEARCH,
        "update": StepType.UPDATE,
        "formula": StepType.FORMULA,
    }
    for op in ("upsert", "search", "update", "formula"):
        if op not in tokens:
            continue
        from services.planning.operation_resolver import resolve_tool_id

        connector = dest_type if dest_type and dest_type != "unknown" else src_type
        tool_id = resolve_tool_id(op, connector)
        op_dep = list(map_dep) or ([sid_extract] if "extract" in tokens else [])
        sid_op = op
        steps.append(
            _step(
                _GSHEETS_STEP_TYPES[op],
                tool_id,
                label=_GSHEETS_OP_LABELS[op],
                source=src_ref,
                destination=dest_ref,
                depends_on=op_dep or None,
                source_name=src_name,
                destination_name=dest_name,
                classification=classification,
                operation=op,
            )
        )
        steps[-1].parameters["step_id"] = sid_op
        map_dep = [sid_op]

    migrate_only = tokens == ["migrate"] or (
        tokens == ["data_ops", "migrate"]
        and "map" not in tokens
        and _is_migrate_only_goal(classification, goal, prompt)
    )

    if migrate_only:
        steps.append(
            _step(
                StepType.MIGRATE,
                "start_migration",
                label=_migrate_step_label(dest_name, dest_type, classification),
                source=src_ref,
                destination=dest_ref,
                depends_on=map_dep or None,
                destination_name=dest_name,
                classification=classification,
            )
        )
    elif "map" in tokens:
        steps.append(
            _step(
                StepType.MAP,
                "map_fields",
                label=_map_step_label(dest_name, dest_type, classification),
                source=src_ref,
                destination=dest_ref,
                depends_on=map_dep or None,
                destination_name=dest_name,
                classification=classification,
            )
        )
        steps[-1].parameters["step_id"] = sid_map
        if "migrate" in tokens:
            _append_migrate_step_if_requested(
                steps,
                classification=classification,
                goal=goal,
                src_ref=src_ref,
                dest_ref=dest_ref,
                src_name=src_name,
                dest_name=dest_name,
                dest_type=dest_type,
                depends_on=[sid_map],
            )
    elif "migrate" in tokens:
        _append_migrate_step_if_requested(
            steps,
            classification=classification,
            goal=goal,
            src_ref=src_ref,
            dest_ref=dest_ref,
            src_name=src_name,
            dest_name=dest_name,
            dest_type=dest_type,
            depends_on=map_dep or [],
        )

    if "deploy_schema" in tokens:
        steps.append(
            _step(
                StepType.DEPLOY_SCHEMA,
                "metadata_deploy_salesforce",
                label="Deploy Salesforce metadata",
                source=src_ref,
                destination=dest_ref,
                source_name=src_name,
                destination_name=dest_name,
                classification=classification,
            )
        )


def build_plan_steps(
    classification: Dict[str, Any],
    *,
    prompt: Optional[str] = None,
    plan: Optional[ExecutionPlan] = None,
) -> ExecutionPlan:
    """
    Populate plan.steps from resolved classification.
    Steps carry tool_id in parameters for AgentRuntime (no hardcoded tenant resources).
    """
    from services.intent.migration_intent_view import overlay_intent_state_on_classification

    classification = overlay_intent_state_on_classification(classification)
    goal = _resolve_plan_scope(classification, prompt, plan=plan)
    from services.planning.create_metadata import sync_create_assembly_readiness

    classification = sync_create_assembly_readiness(classification, goal, plan=plan)
    if classification.get("_plan_scope") is None and goal:
        classification["_plan_scope"] = goal
    out_plan = plan.model_copy(deep=True) if plan else ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        status="draft",
        goal_summary=None,
        metadata={"classification": dict(classification), "goal": goal},
    )

    src_type = normalize_connector_type(classification.get("source") or "unknown")
    dest_type = normalize_connector_type(classification.get("destination") or "unknown")
    file_extract_tool = _extract_tool(
        src_type,
        classification=classification,
        prompt=prompt,
    )
    src_name = classification.get("source_name")
    dest_name = classification.get("destination_name")
    src_ref = _ref(src_type, src_name)
    dest_ref = _ref(
        dest_type,
        dest_name,
        spreadsheet_id=classification.get("destination_spreadsheet_id"),
        sheet_name=classification.get("destination_sheet_name"),
    )

    from services.planning.pipeline_step_resolver import (
        PlanContext,
        derive_pipeline_label,
        resolve_pipeline_steps,
    )

    ctx = PlanContext.from_classification(classification)
    pipeline_tokens = resolve_pipeline_steps(
        goal,
        ctx,
        precomputed_steps=classification.get("_continuation_steps"),
    )
    from services.planning.create_metadata import should_materialize_create_step

    if "create" in pipeline_tokens and not should_materialize_create_step(classification, goal):
        pipeline_tokens = [t for t in pipeline_tokens if t != "create"]
    out_plan.metadata["pipeline_steps"] = pipeline_tokens
    out_plan.metadata["pipeline_label"] = derive_pipeline_label(pipeline_tokens)
    out_plan.metadata["goal"] = goal
    if src_ref:
        out_plan.sources = [src_ref]
    skip_dest = pipeline_tokens == ["data_ops"]
    if dest_ref and not skip_dest:
        out_plan.destination = dest_ref
    if classification.get("plan_contract"):
        out_plan.metadata["plan_contract"] = classification.get("plan_contract")
    from utils.extraction_profile import resolve_extraction_profile

    out_plan.metadata["extraction_profile"] = resolve_extraction_profile(
        prompt=prompt,
        goal=goal,
        template=out_plan.metadata.get("pipeline_label"),
        source=(classification.get("source") or "").lower() or None,
    ).value
    steps: List[PlanStep] = []
    sid_extract = "extract"
    sid_map = "map"

    if classification.get("retry_after_partial"):
        _build_retry_partial_steps(
            steps,
            classification=classification,
            src_ref=src_ref,
            dest_ref=dest_ref,
            dest_name=dest_name,
            dest_type=dest_type,
            sid_map=sid_map,
        )
    elif _needs_load_flow(classification, goal):
        _build_load_flow_with_inner_plan(
            steps,
            classification=classification,
            prompt=prompt,
            out_plan=out_plan,
        )
    elif _needs_multi_source_build(classification, goal):
        _build_multi_source_steps(
            steps,
            classification=classification,
            goal=goal,
            prompt=prompt,
            src_type=src_type,
            src_name=src_name,
            src_ref=src_ref,
            dest_ref=dest_ref,
            dest_name=dest_name,
            dest_type=dest_type,
            out_plan=out_plan,
        )
    else:
        from services.planning.plan_scope import continuation_pipeline_tokens, read_plan_scope

        tokens = continuation_pipeline_tokens(classification, read_plan_scope(classification))
        continuation_map_without_dest = bool(
            classification.get("active_job_id")
            and any(t in tokens for t in ("map", "migrate"))
            and not _destination_is_known(dest_type, dest_name)
        )
        if not continuation_map_without_dest and pipeline_tokens:
            _build_steps_from_pipeline_tokens(
                pipeline_tokens,
                steps,
                classification=classification,
                goal=goal,
                prompt=prompt,
                file_extract_tool=file_extract_tool,
                src_type=src_type,
                src_ref=src_ref,
                dest_ref=dest_ref,
                src_name=src_name,
                dest_name=dest_name,
                dest_type=dest_type,
                sid_extract=sid_extract,
                sid_map=sid_map,
            )

    from services.planning.schema_gates import append_schema_gate_steps, plan_needs_schema_gate

    if plan_needs_schema_gate(classification, goal) and steps:
        extract_deps = [
            s.parameters.get("step_id")
            for s in steps
            if s.step_type == StepType.EXTRACT and s.parameters.get("step_id")
        ]
        pre_deps = [extract_deps[-1]] if extract_deps else []
        steps = append_schema_gate_steps(
            steps,
            source=src_ref,
            destination=dest_ref,
            source_name=src_name,
            destination_name=dest_name,
            depends_on=pre_deps,
        )

    if _goal_requests_save_flow(goal, classification):
        steps.append(
            _step(
                StepType.SAVE_FLOW,
                "save_flow",
                label="Save pipeline as flow",
                depends_on=[steps[-1].parameters.get("step_id")] if steps else [],
            )
        )

    for i, st in enumerate(steps):
        st.parameters["step_id"] = f"step_{i + 1}"
    out_plan.steps = steps

    return apply_plan_execution_summary(out_plan)


def _step_tool_id(step: PlanStep) -> str:
    return str((step.parameters or {}).get("tool_id") or "").lower()


def _is_extract_step(step: PlanStep) -> bool:
    if step.step_type == StepType.EXTRACT:
        return True
    return "extract" in _step_tool_id(step)


def _plan_has_extract_step(steps: List[PlanStep]) -> bool:
    return any(_is_extract_step(s) for s in steps)


def _continuation_summary_prefix(plan: ExecutionPlan, steps: List[PlanStep]) -> str:
    meta = plan.metadata or {}
    classification = meta.get("classification") or {}
    if not isinstance(classification, dict):
        classification = {}
    is_continuation = bool(
        meta.get("continuation")
        or meta.get("job_id")
        or classification.get("plan_route") == "continuation_path"
        or meta.get("continuation_steps")
    )
    if is_continuation and not _plan_has_extract_step(steps):
        return "Using data from your current job, "
    return ""


def _join_step_labels(labels: List[str]) -> str:
    cleaned = [lbl.strip() for lbl in labels if (lbl or "").strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", then ".join(cleaned)


def _create_only_execution_summary(
    *,
    classification: Dict[str, Any],
    goal: Dict[str, Any],
    pipeline_steps: List[str],
) -> Optional[str]:
    if not pipeline_steps or not set(pipeline_steps) <= {"create"}:
        return None
    title = (
        (classification.get("planner_new_spreadsheet_name") or "").strip()
        or (goal.get("spreadsheet_title") or "").strip()
    )
    headers = list(classification.get("planner_spreadsheet_headers") or [])
    if not headers:
        headers = list(goal.get("spreadsheet_headers") or [])
    summary = "Create a new Google Sheet"
    if title:
        summary += f' named "{title}"'
    if headers:
        summary += f" with columns: {', '.join(str(h) for h in headers[:10])}"
    return summary[:200]


def _extract_enriched_execution_summary(
    *,
    migration_intent: Any,
    plan_contract: Any,
) -> Optional[str]:
    entity_label = "records"
    field_hints: List[str] = []
    field_hints_strict = False
    include_related = False
    parent_label: Optional[str] = None

    if migration_intent is not None:
        from services.planning.migration_intent_schemas import PromptMigrationIntent

        mi = (
            migration_intent
            if isinstance(migration_intent, PromptMigrationIntent)
            else PromptMigrationIntent.model_validate(migration_intent)
        )
        entity_label = mi.driving_entity_label or mi.resolved_driving_key() or entity_label
        if mi.field_hints_strict:
            field_hints = [str(h) for h in (mi.field_hints or []) if str(h).strip()]
            field_hints_strict = True
        include_related = bool(mi.include_related_data)
        parent_label = mi.parent_entity_label or mi.parent_entity_key

    src_side: Any = None
    if plan_contract is not None:
        if hasattr(plan_contract, "source"):
            src_side = plan_contract.source
        elif isinstance(plan_contract, dict):
            src_side = plan_contract.get("source")

    if src_side is not None:
        from services.planning.schemas import PlanEntityRef

        try:
            ref = (
                src_side
                if isinstance(src_side, PlanEntityRef)
                else PlanEntityRef.model_validate(src_side)
            )
            label = ref.step_entity_label()
            if label:
                entity_label = label
        except Exception:
            key = (
                getattr(src_side, "entity_key", None)
                or (src_side.get("entity_key") if isinstance(src_side, dict) else "")
                or ""
            ).strip()
            if key:
                entity_label = key

    from services.planning.migration_intent import _build_extraction_summary

    return _build_extraction_summary(
        driving_label=entity_label,
        field_hints=field_hints,
        include_related=include_related,
        parent_label=parent_label,
        field_hints_strict=field_hints_strict,
    )


def apply_plan_execution_summary(
    plan: ExecutionPlan,
    *,
    migration_intent: Any = None,
    plan_contract: Any = None,
) -> ExecutionPlan:
    """User-facing synopsis of what Edison will perform (not classifier reasoning)."""
    plan = plan.model_copy(deep=True)
    steps = list(plan.steps or [])
    if not steps:
        plan.goal_summary = None
        return plan

    classification = (plan.metadata or {}).get("classification") or {}
    if not isinstance(classification, dict):
        classification = {}
    goal = (plan.metadata or {}).get("goal") or {}
    if not isinstance(goal, dict):
        goal = {}
    pipeline_steps = list((plan.metadata or {}).get("pipeline_steps") or [])

    create_summary = _create_only_execution_summary(
        classification=classification,
        goal=goal,
        pipeline_steps=pipeline_steps,
    )
    if create_summary:
        plan.goal_summary = create_summary
        return plan

    extract_summary = _extract_enriched_execution_summary(
        migration_intent=migration_intent,
        plan_contract=plan_contract,
    )
    non_extract_labels = [
        (s.label or "").strip()
        for s in steps
        if (s.label or "").strip() and not _is_extract_step(s)
    ]
    if extract_summary and _plan_has_extract_step(steps):
        if non_extract_labels:
            summary = f"{extract_summary}, then {_join_step_labels(non_extract_labels)}"
        else:
            summary = extract_summary
    else:
        all_labels = [(s.label or "").strip() for s in steps if (s.label or "").strip()]
        prefix = _continuation_summary_prefix(plan, steps)
        joined = _join_step_labels(all_labels)
        summary = f"{prefix}{joined}" if joined else ""

    plan.goal_summary = summary[:200] if summary else None
    return plan


def sync_plan_scope_summary(
    plan: ExecutionPlan,
    *,
    migration_intent: Any = None,
    plan_contract: Any = None,
) -> ExecutionPlan:
    """Backward-compatible alias for ``apply_plan_execution_summary``."""
    return apply_plan_execution_summary(
        plan,
        migration_intent=migration_intent,
        plan_contract=plan_contract,
    )


def finalize_plan_status(
    plan: ExecutionPlan,
    *,
    plan_auto_run: bool = False,
    force_auto_approve: bool = False,
) -> ExecutionPlan:
    """
    Set plan status after clarifications are resolved.

    Chat turns use ``plan_auto_run`` from the client (Ask vs Act). Scheduled flows
    pass ``force_auto_approve=True``.
    """
    plan = plan.model_copy(deep=True)
    if plan.blocking_clarifications():
        plan.status = "awaiting_clarification"
        return plan
    if not plan.steps:
        plan.status = "draft"
        return plan
    if force_auto_approve or plan_auto_run:
        plan.status = "approved"
    else:
        plan.status = "awaiting_approval"
    return plan

# --- Map intent context (merged P4.9) ---

from typing import Any, Dict, List, Optional


def migration_intent_from_snapshot(
    classification_snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    snap = classification_snapshot if isinstance(classification_snapshot, dict) else {}
    contract = snap.get("plan_contract") or {}
    intent = contract.get("migration_intent") if isinstance(contract, dict) else None
    if isinstance(intent, dict) and intent:
        return dict(intent)
    alt = snap.get("migration_intent")
    return dict(alt) if isinstance(alt, dict) else {}


def build_mapping_relationship_context_block(
    migration_intent: Optional[Dict[str, Any]],
) -> str:
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    if not intent:
        return ""

    dest_key = (
        intent.get("destination_entity_key")
        or ""
    ).strip()
    parent = (intent.get("parent_entity_key") or intent.get("related_entity_key") or "").strip()
    cross = bool(intent.get("cross_system"))
    include_related = bool(intent.get("include_related_data"))

    lines = ["**MIGRATION RELATIONSHIP INTENT (plan-pinned):**"]
    if dest_key:
        lines.append(
            f"- Primary destination object for this migration: `{dest_key}` "
            "(map extracted row fields to this object unless resolving a declared parent lookup)."
        )
    if cross:
        lines.append(
            "- Cross-system migration: do NOT map source Id / foreign-key columns to destination lookup Id fields."
        )
        lines.append(
            "- Use descriptive extracted fields (Name, Email, AccountName, etc.) and set `lookup_of` "
            "only when resolving a related parent record at migrate time."
        )
    if include_related and parent:
        lines.append(
            f"- Extract included related `{parent}` descriptive fields — map those to `{parent}` "
            f"with the correct `lookup_of` on `{dest_key}` (e.g. `{dest_key}.AccountId`)."
        )
        lines.append(
            f"- Do NOT map the driving entity's rows to `{parent}` as the base object."
        )
    elif parent and not include_related:
        lines.append(
            f"- Related `{parent}` data was not in extract scope — avoid inventing `{parent}` lookup mappings."
        )

    if intent.get("field_hints"):
        hints = ", ".join(str(h) for h in intent.get("field_hints") or [] if h)
        if hints:
            lines.append(f"- User field hints: {hints}")

    return "\n".join(lines) if len(lines) > 1 else ""


def enrich_mapping_instruction(
    mapping_instruction: Optional[str],
    classification_snapshot: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    intent = migration_intent_from_snapshot(classification_snapshot)
    block = build_mapping_relationship_context_block(intent)
    base = (mapping_instruction or "").strip()
    if not block:
        return base or None
    if block in base:
        return base
    return f"{block}\n\n{base}".strip() if base else block


def audit_migration_intent_scope(
    mapping: Dict[str, Any],
    flat_data: Dict[str, Any],
    migration_intent: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return mapping_audit-style issue dicts for out-of-scope cross-system / relationship mappings."""
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    if not mapping or not intent:
        return []

    issues: List[Dict[str, Any]] = []
    dest_key = (
        intent.get("destination_entity_key")
        or ""
    ).strip()
    parent = (intent.get("parent_entity_key") or intent.get("related_entity_key") or "").strip()
    cross = bool(intent.get("cross_system"))
    include_related = bool(intent.get("include_related_data"))
    data_keys = {str(k) for k in (flat_data or {}) if k and not str(k).startswith("_")}

    for src_key, cfg in (mapping or {}).items():
        if not isinstance(cfg, dict):
            if isinstance(cfg, list):
                for item in cfg:
                    if isinstance(item, dict):
                        issues.extend(
                            _audit_one_mapping_entry(
                                src_key,
                                item,
                                dest_key=dest_key,
                                parent=parent,
                                cross=cross,
                                include_related=include_related,
                                data_keys=data_keys,
                            )
                        )
            continue
        issues.extend(
            _audit_one_mapping_entry(
                src_key,
                cfg,
                dest_key=dest_key,
                parent=parent,
                cross=cross,
                include_related=include_related,
                data_keys=data_keys,
            )
        )
    return issues


def _audit_one_mapping_entry(
    src_key: str,
    cfg: Dict[str, Any],
    *,
    dest_key: str,
    parent: str,
    cross: bool,
    include_related: bool,
    data_keys: set,
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    obj = (cfg.get("object") or cfg.get("table") or "").strip()
    lookup_of = (cfg.get("lookup_of") or "").strip()
    src_l = (src_key or "").lower()

    if cross and (src_l == "id" or src_l.endswith("id") or src_l.endswith("_id")):
        issues.append(
            {
                "code": "CROSS_SYSTEM_SOURCE_ID_MAPPED",
                "severity": "warn",
                "message": (
                    f"Source field `{src_key}` looks like a reference id. "
                    "Cross-system migrations should map descriptive fields and resolve lookups at migrate time."
                ),
                "source_keys": [src_key],
            }
        )

    if dest_key and obj and not lookup_of and obj != dest_key:
        issues.append(
            {
                "code": "DEST_TARGET_MISMATCH",
                "severity": "warn",
                "message": (
                    f"Field `{src_key}` maps to `{obj}` but the plan targets `{dest_key}` "
                    f"as the primary destination object."
                ),
                "source_keys": [src_key],
            }
        )

    if lookup_of and parent and include_related:
        if parent not in obj and obj != parent:
            issues.append(
                {
                    "code": "LOOKUP_PARENT_SCOPE",
                    "severity": "info",
                    "message": (
                        f"Field `{src_key}` uses lookup `{lookup_of}` on `{obj}` — "
                        f"verify this matches related `{parent}` from extract intent."
                    ),
                    "source_keys": [src_key],
                }
            )
    elif lookup_of and not include_related:
        issues.append(
            {
                "code": "LOOKUP_WITHOUT_RELATED_EXTRACT",
                "severity": "warn",
                "message": (
                    f"Field `{src_key}` declares lookup `{lookup_of}` but extract did not "
                    "include related parent descriptive fields."
                ),
                "source_keys": [src_key],
            }
        )

    if src_key not in data_keys and src_l.replace("_", "") not in {
        k.lower().replace("_", "") for k in data_keys
    }:
        pass  # structural audit handles MAPPING_KEY_NOT_IN_EXTRACT

    return issues


def merge_intent_issues_into_audit(
    audit: Optional[Dict[str, Any]],
    intent_issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    out = dict(audit or {})
    if not intent_issues:
        return out
    existing = list(out.get("issues") or [])
    existing.extend(intent_issues)
    out["issues"] = existing
    if any(i.get("severity") == "error" for i in intent_issues):
        out["status"] = "error"
        out["allow_auto_migration"] = False
    elif any(i.get("severity") == "warn" for i in intent_issues) and out.get("status") == "ok":
        out["status"] = "warn"
    return out


