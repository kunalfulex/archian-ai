"""Convert planning ExecutionPlan → agent runtime ExecutionPlan."""
from __future__ import annotations

from typing import Any, Dict, List

from agent.plan import ExecutionPlan as AgentExecutionPlan
from agent.plan import PlanStep as AgentPlanStep
from agent.session import AgentSession


def agent_plan_from_execution_plan(
    session: AgentSession,
    execution_plan: Dict[str, Any],
) -> AgentExecutionPlan:
    """Map v2 plan steps (DB-backed) to AgentRuntime tool steps."""
    raw_steps = execution_plan.get("steps") or []
    mode = session.mode
    agent_steps: List[AgentPlanStep] = []

    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        params = dict(raw.get("parameters") or {})
        tool_id = params.pop("tool_id", None) or _tool_for_step_type(raw.get("step_type"))
        if not tool_id:
            continue
        args = _arguments_for_tool(tool_id, session, raw, params)
        for gate_key in (
            "requires_user_approval",
            "schema_gate_deploy",
            "step_id",
            "gate_phase",
            "depends_on",
            "use_extraction_headers",
        ):
            if gate_key in params:
                args[gate_key] = params[gate_key]
        raw_depends = raw.get("depends_on")
        if raw_depends and "depends_on" not in args:
            args["depends_on"] = list(raw_depends)
        if not args.get("step_id"):
            from services.planning.plan_execution_gates import resolve_step_id

            inferred = resolve_step_id(tool_id, params)
            if inferred:
                args["step_id"] = inferred
        agent_steps.append(
            AgentPlanStep(
                tool_id=tool_id,
                arguments=args,
                label=raw.get("label"),
            )
        )

    return AgentExecutionPlan(
        mode=mode,
        steps=agent_steps,
        reasoning=f"plan_scoped:{execution_plan.get('metadata', {}).get('pipeline_label', 'plan')}",
    )


def _tool_for_step_type(step_type: Any) -> str:
    from services.planning.operation_resolver import normalize_operation, resolve_tool_id

    key = str(step_type or "").lower().replace("steptype.", "")
    op = normalize_operation(key)
    if op == "extract":
        return resolve_tool_id("extract", "salesforce")
    return resolve_tool_id(op)


def _entity_args_from_params(params: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
    """Pass pinned entity keys from plan step parameters / classification snapshot."""
    out: Dict[str, Any] = {}
    contract = snap.get("plan_contract") or {}
    src = contract.get("source") if isinstance(contract, dict) else {}
    dest = contract.get("destination") if isinstance(contract, dict) else {}

    for key in (
        "source_entity_key",
        "destination_entity_key",
        "source_entity_label",
        "destination_entity_label",
        "related_entity_keys",
        "field_hints",
        "extract_field_scope",
    ):
        if params.get(key):
            out[key] = params[key]

    if not out.get("source_entity_key"):
        if isinstance(src, dict) and src.get("entity_key"):
            out["source_entity_key"] = src["entity_key"]
            out["source_entity_label"] = src.get("entity_label") or src["entity_key"]
        elif snap.get("source_object_api_name"):
            out["source_entity_key"] = snap["source_object_api_name"]

    if not out.get("destination_entity_key"):
        if isinstance(dest, dict) and dest.get("entity_key"):
            out["destination_entity_key"] = dest["entity_key"]
            out["destination_entity_label"] = dest.get("entity_label") or dest["entity_key"]
        elif snap.get("destination_object_api_name"):
            out["destination_entity_key"] = snap["destination_object_api_name"]

    entities = params.get("entities")
    if entities:
        out["entities"] = entities
    return out


def _classification_for_extract_contract(
    snap: Dict[str, Any],
    entity_args: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge step-level entity pins into snapshot for authoritative contract build."""
    contract_snap = dict(snap)
    if entity_args.get("related_entity_keys") and not contract_snap.get("related_entity_keys"):
        contract_snap["related_entity_keys"] = entity_args["related_entity_keys"]
    return contract_snap


def _arguments_for_tool(
    tool_id: str,
    session: AgentSession,
    raw: Dict[str, Any],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    snap = session.classification_snapshot or {}
    dest = (
        params.get("destination")
        or snap.get("destination")
        or session.destination
        or "unknown"
    )
    dest_name = (
        params.get("destination_name")
        or snap.get("destination_name")
        or session.destination_name
    )
    src_name = params.get("source_name") or snap.get("source_name") or session.source_name
    entity_args = _entity_args_from_params(params, snap)

    if tool_id in ("extract_salesforce", "extract_sql", "extract_google_sheets"):
        from services.planning.extract_contract_from_classification import (
            build_extract_contract_from_classification,
        )

        connector = {
            "extract_salesforce": "salesforce",
            "extract_sql": "sql",
            "extract_google_sheets": "google_sheets",
        }[tool_id]
        contract = build_extract_contract_from_classification(
            _classification_for_extract_contract(snap, entity_args),
            connector,
        )
        out: Dict[str, Any] = {
            "destination": dest,
            "destination_name": dest_name,
            "source_name": src_name,
            **entity_args,
        }
        if tool_id == "extract_google_sheets":
            out["source_sheet_name"] = snap.get("source_sheet_name")
            out["destination_sheet_name"] = snap.get("destination_sheet_name")
        if contract:
            out["extract_contract"] = contract
        return out
    if tool_id == "extract_file":
        return {
            "destination": dest,
            "destination_name": dest_name,
        }
    if tool_id == "google_sheets_create_from_prompt":
        from services.planning.create_contract import build_create_spreadsheet_contract
        from services.planning.plan_scope import read_plan_scope

        goal = read_plan_scope(snap) or None
        contract = build_create_spreadsheet_contract(snap, goal)
        title = params.get("spreadsheet_title") or contract.get("spreadsheet_title")
        out: Dict[str, Any] = {
            "spreadsheet_title": title,
            "create_contract": contract,
            **params,
        }
        headers = params.get("headers") or contract.get("headers")
        if headers:
            out["headers"] = headers
        job_id = (
            session.active_job_id
            or contract.get("extraction_job_id")
            or snap.get("active_job_id")
        )
        if job_id:
            out["extraction_job_id"] = job_id
        use_ext = bool(
            params.get("use_extraction_headers") or contract.get("use_extraction_headers")
        )
        if use_ext:
            out["use_extraction_headers"] = True
        conn_id = contract.get("google_sheets_connection_id") or (
            snap.get("google_sheets_connection_id") or snap.get("destination_connector_id")
        )
        if conn_id:
            out["google_sheets_connection_id"] = conn_id
        return out
    if tool_id == "start_migration":
        args: Dict[str, Any] = {
            "destination": dest,
            "destination_name": dest_name or params.get("destination_name"),
            **entity_args,
        }
        if session.active_job_id:
            args["job_id"] = session.active_job_id
        last = session.last_result or {}
        if last.get("mapping_id"):
            args["mapping_id"] = last.get("mapping_id")
        return args
    if tool_id == "map_fields":
        args = {
            "destination": dest,
            "destination_name": dest_name,
            **entity_args,
        }
        recovery_ctx = (snap.get("recovery_mapping_context") or "").strip()
        if recovery_ctx:
            args["mapping_context_summary"] = recovery_ctx
        if session.active_job_id:
            args["job_id"] = session.active_job_id
        return args
    if tool_id == "metadata_deploy_salesforce":
        from services.planning.deploy_contract import build_deploy_contract
        from services.planning.plan_scope import read_plan_scope

        goal = read_plan_scope(snap) or None
        return {
            "source_name": src_name,
            "destination_name": dest_name,
            "deploy_contract": build_deploy_contract(snap, goal),
            **params,
        }
    if tool_id in ("schema_gate_assess", "schema_gate_propose"):
        return {
            "source_name": src_name,
            "destination_name": dest_name,
            "gate_phase": params.get("gate_phase"),
        }
    if tool_id == "load_flow":
        return {"flow_id": params.get("flow_id") or snap.get("flow_id")}
    if tool_id == "save_flow":
        return {
            "flow_name": params.get("flow_name") or snap.get("planner_new_spreadsheet_name"),
            "job_id": session.active_job_id,
            "destination": dest,
            "destination_name": dest_name,
        }
    if tool_id == "run_data_ops_plan":
        parent_job = (
            params.get("parent_job_id")
            or snap.get("active_job_id")
            or session.active_job_id
        )
        return {
            "parent_job_id": parent_job,
            "steps": params.get("steps")
            or [{"operation_id": op, "prompt": session.prompt} for op in session.operations],
        }
    if tool_id == "gsheets_execute_operation":
        from services.planning.plan_scope import read_plan_scope

        out = dict(params)
        step_type = str(raw.get("step_type") or "").lower()
        op = (params.get("operation") or "").strip().lower()
        goal = read_plan_scope(snap) or None
        if step_type.endswith("join") or op == "join":
            out["operation"] = "join"
            from services.planning.join_contract import build_join_contract

            out["join_contract"] = build_join_contract(snap, goal)
        elif op == "upsert":
            out["operation"] = "upsert"
            from services.planning.upsert_contract import build_upsert_contract

            out["upsert_contract"] = build_upsert_contract(snap, goal)
        elif op == "search":
            out["operation"] = "search"
            from services.planning.search_contract import build_search_contract

            out["search_contract"] = build_search_contract(snap, goal)
        elif op == "formula":
            out["operation"] = "formula"
        elif op == "update":
            out["operation"] = "update"
        return out
    return dict(params)


def refresh_step_arguments(
    tool_id: str,
    args: Dict[str, Any],
    session: AgentSession,
) -> Dict[str, Any]:
    """Re-apply session state before each runtime step (job id, resolved destination)."""
    out = dict(args)
    snap = session.classification_snapshot or {}
    snap_name = (snap.get("destination_name") or "").strip()
    snap_dest = (snap.get("destination") or "").strip()
    planner_confirmed = bool(snap.get("planner_destination_confirmed"))

    if tool_id == "start_migration":
        if session.active_job_id:
            out["job_id"] = session.active_job_id
        if planner_confirmed and snap_name:
            out["destination_name"] = snap_name
        elif session.destination_name and not out.get("destination_name"):
            out["destination_name"] = session.destination_name
        if planner_confirmed and snap_dest:
            out["destination"] = snap_dest
        elif session.destination and not out.get("destination"):
            out["destination"] = session.destination
    elif tool_id == "map_fields":
        if session.active_job_id:
            out["job_id"] = session.active_job_id
        if planner_confirmed and snap_name:
            out["destination_name"] = snap_name
        elif session.destination_name and not out.get("destination_name"):
            out["destination_name"] = session.destination_name
        if planner_confirmed and snap_dest:
            out["destination"] = snap_dest
        elif session.destination and not out.get("destination"):
            out["destination"] = session.destination
        if snap.get("destination_connector_id"):
            out["destination_connector_id"] = snap.get("destination_connector_id")
        contract_dest = (snap.get("plan_contract") or {}).get("destination") or {}
        entity_key = (
            (contract_dest.get("entity_key") if isinstance(contract_dest, dict) else None)
            or snap.get("destination_object_api_name")
        )
        if entity_key:
            out["destination_entity_key"] = entity_key
        if snap.get("destination_spreadsheet_id"):
            out["spreadsheet_id"] = snap["destination_spreadsheet_id"]
        if snap.get("destination_sheet_name"):
            out["destination_sheet_name"] = snap["destination_sheet_name"]
    elif tool_id == "google_sheets_create_from_prompt":
        from services.planning.create_contract import build_create_spreadsheet_contract
        from services.planning.plan_scope import read_plan_scope

        goal = read_plan_scope(snap) or None
        contract = build_create_spreadsheet_contract(snap, goal)
        if contract.get("spreadsheet_title"):
            out["spreadsheet_title"] = contract["spreadsheet_title"]
        refreshed_headers = out.get("headers") or contract.get("headers")
        if refreshed_headers:
            out["headers"] = refreshed_headers
        if contract.get("use_extraction_headers") or out.get("use_extraction_headers"):
            out["use_extraction_headers"] = True
        job_id = (
            session.active_job_id
            or contract.get("extraction_job_id")
            or snap.get("active_job_id")
        )
        if job_id:
            out["extraction_job_id"] = job_id
        conn_id = contract.get("google_sheets_connection_id") or (
            snap.get("google_sheets_connection_id") or snap.get("destination_connector_id")
        )
        if conn_id:
            out["google_sheets_connection_id"] = conn_id
        out["create_contract"] = contract
    elif tool_id in ("extract_salesforce", "extract_sql", "extract_google_sheets"):
        from services.planning.extract_contract_from_classification import (
            build_extract_contract_from_classification,
        )

        connector = {
            "extract_salesforce": "salesforce",
            "extract_sql": "sql",
            "extract_google_sheets": "google_sheets",
        }[tool_id]
        entity_args = _entity_args_from_params(out, snap)
        contract = build_extract_contract_from_classification(
            _classification_for_extract_contract(snap, entity_args),
            connector,
        )
        if contract:
            out["extract_contract"] = contract
    elif tool_id == "metadata_deploy_salesforce":
        from services.planning.deploy_contract import build_deploy_contract
        from services.planning.plan_scope import read_plan_scope

        goal = read_plan_scope(snap) or None
        out["deploy_contract"] = build_deploy_contract(snap, goal)
    elif tool_id == "run_data_ops_plan":
        out["parent_job_id"] = (
            out.get("parent_job_id")
            or (session.classification_snapshot or {}).get("active_job_id")
            or session.active_job_id
        )
        if not out.get("steps") and session.operations:
            out["steps"] = [
                {"operation_id": op, "prompt": session.prompt}
                for op in session.operations
            ]
    return out
