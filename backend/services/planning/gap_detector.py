"""Detect missing information before building or running a pipeline."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid

from services.planning.resource_catalog import GoogleSheetRef, ResourceCatalog
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef


def sanitize_unconfirmed_google_destination(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Drop unvalidated Google Sheets workbook pins (catalog + authoritative flags only)."""
    from services.planning.destination_catalog_validate import (
        destination_connector_authoritatively_confirmed,
        destination_connector_slot_satisfied,
    )
    from services.planning.plan_scope import read_plan_scope

    out = dict(classification or {})
    dest = str(out.get("destination") or "").lower()
    if dest not in ("google_sheets", "gsheets"):
        return out

    if destination_connector_authoritatively_confirmed(out):
        return out
    if destination_connector_slot_satisfied(
        out, catalog, parsed_goal=read_plan_scope(out)
    ):
        return out
    if not (out.get("destination_spreadsheet_id") or "").strip():
        out.pop("destination_name", None)
        out.pop("destination_sheet_name", None)
        out.pop("planner_destination_confirmed", None)
    return out


def _is_unknown_name(value: Optional[str]) -> bool:
    from services.planning.connector_guards import is_unknown_resource_name

    return is_unknown_resource_name(value)


def _intent_needs_extract_source(classification: Dict[str, Any]) -> bool:
    from services.planning.connector_guards import classification_needs_source_endpoint
    from services.planning.op_requirements import (
        read_plan_requirements,
        requirements_need_source_connector,
    )

    reqs = read_plan_requirements(classification)
    if reqs.pipeline_tokens or classification.get("_plan_requirements"):
        return requirements_need_source_connector(reqs)
    return classification_needs_source_endpoint(classification)


def _source_connector_type_clarification(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Optional[Clarification]:
    """Ask which source system when connector type is unresolved."""
    from services.file_extraction.pending_upload import is_pinned_file_upload
    from services.planning.connector_guards import is_known_connector

    if is_pinned_file_upload(classification):
        return None
    if is_known_connector(classification.get("source")):
        return None
    if not _intent_needs_extract_source(classification):
        return None

    options: List[ClarificationOption] = []
    if catalog.sql_connections:
        name = catalog.sql_names[0] if len(catalog.sql_names) == 1 else "SQL database"
        options.append(
            ClarificationOption(
                id="src_type_sql",
                label=f"SQL — {name}" if len(catalog.sql_names) == 1 else "SQL database",
                resource_ref=ResourceRef(connector_type="sql", display_name=name),
            )
        )
    if catalog.salesforce_connections:
        name = (
            catalog.salesforce_names[0]
            if len(catalog.salesforce_names) == 1
            else "Salesforce org"
        )
        options.append(
            ClarificationOption(
                id="src_type_salesforce",
                label=f"Salesforce — {name}" if len(catalog.salesforce_names) == 1 else "Salesforce org",
                resource_ref=ResourceRef(connector_type="salesforce", display_name=name),
            )
        )
    if catalog.has_google_sheets and catalog.google_sheet_rows:
        name = catalog.spreadsheet_names[0] if len(catalog.spreadsheet_names) == 1 else "Google Sheets"
        row = catalog.google_sheet_rows[0] if catalog.google_sheet_rows else None
        options.append(
            ClarificationOption(
                id="src_type_google_sheets",
                label=f"Google Sheets — {name}" if len(catalog.spreadsheet_names) == 1 else "Google Sheets",
                resource_ref=ResourceRef(
                    connector_type="google_sheets",
                    display_name=name,
                    sheet_name=row.sheet_name if row else None,
                    spreadsheet_id=row.spreadsheet_id if row else None,
                ),
            )
        )

    if not options:
        return None
    if len(options) == 1:
        return None

    return Clarification(
        id=str(uuid.uuid4()),
        clarification_type="pick_source_connector",
        prompt="",
        options=options,
        required=True,
    )


def _auto_pin_single_source_option(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """When only one source type exists, pin it without showing a picker."""
    from services.planning.connector_guards import is_known_connector

    out = dict(classification or {})
    if is_known_connector(out.get("source")):
        return out
    scope = out.get("_plan_scope") if isinstance(out.get("_plan_scope"), dict) else {}
    scope_ops = [str(o).lower() for o in (scope.get("operations") or out.get("operations") or [])]
    if scope_ops == ["create"] or (
        scope.get("create_destination") and "extract" not in scope_ops
    ):
        return out
    if not _intent_needs_extract_source(out):
        return out

    candidates: List[tuple[str, str, Optional[str]]] = []
    if catalog.sql_connections:
        candidates.append(("sql", catalog.sql_names[0], None))
    if catalog.salesforce_connections:
        candidates.append(("salesforce", catalog.salesforce_names[0], None))
    if catalog.has_google_sheets and catalog.google_sheet_rows:
        row = catalog.google_sheet_rows[0]
        candidates.append(
            (
                "google_sheets",
                row.spreadsheet_name or "Google Sheets",
                row.sheet_name,
            )
        )
    if len(candidates) != 1:
        return out

    ctype, name, tab = candidates[0]
    out["source"] = ctype
    out["source_name"] = name
    if tab:
        out["source_sheet_name"] = tab
    return out


def auto_pin_single_source_connector(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    return _auto_pin_single_source_option(classification, catalog)


def _intent_needs_migrate_destination(classification: Dict[str, Any]) -> bool:
    from services.planning.connector_guards import classification_needs_destination_endpoint

    return classification_needs_destination_endpoint(classification)


def _destination_connector_type_clarification(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Optional[Clarification]:
    """Ask which destination system when connector type is unresolved."""
    from services.planning.connector_guards import is_known_connector

    if is_known_connector(classification.get("destination")):
        return None
    if not _intent_needs_migrate_destination(classification):
        return None
    if classification.get("planner_create_spreadsheet"):
        return None

    options: List[ClarificationOption] = []
    if catalog.salesforce_connections:
        name = (
            catalog.salesforce_names[0]
            if len(catalog.salesforce_names) == 1
            else "Salesforce org"
        )
        options.append(
            ClarificationOption(
                id="dest_type_salesforce",
                label=f"Salesforce — {name}" if len(catalog.salesforce_names) == 1 else "Salesforce org",
                resource_ref=ResourceRef(connector_type="salesforce", display_name=name),
            )
        )
    if catalog.sql_connections:
        name = catalog.sql_names[0] if len(catalog.sql_names) == 1 else "SQL database"
        options.append(
            ClarificationOption(
                id="dest_type_sql",
                label=f"SQL — {name}" if len(catalog.sql_names) == 1 else "SQL database",
                resource_ref=ResourceRef(connector_type="sql", display_name=name),
            )
        )
    if catalog.has_google_sheets and catalog.google_sheet_rows:
        name = catalog.spreadsheet_names[0] if len(catalog.spreadsheet_names) == 1 else "Google Sheets"
        row = catalog.google_sheet_rows[0] if catalog.google_sheet_rows else None
        options.append(
            ClarificationOption(
                id="dest_type_google_sheets",
                label=f"Google Sheets — {name}" if len(catalog.spreadsheet_names) == 1 else "Google Sheets",
                resource_ref=ResourceRef(
                    connector_type="google_sheets",
                    display_name=name,
                    sheet_name=row.sheet_name if row else None,
                    spreadsheet_id=row.spreadsheet_id if row else None,
                ),
            )
        )

    if not options or len(options) == 1:
        return None

    return Clarification(
        id=str(uuid.uuid4()),
        clarification_type="pick_destination_connector",
        prompt="",
        options=options,
        required=True,
    )


def _tabs_for_workbook(name: str, catalog: ResourceCatalog) -> List[GoogleSheetRef]:
    needle = (name or "").strip().lower()
    if not needle:
        return []
    return [
        row
        for row in catalog.google_sheet_rows
        if (row.spreadsheet_name or "").strip().lower() == needle
    ]


def _pick_workbook_prompt(classification: Dict[str, Any], prompt: Optional[str]) -> str:
    """Prompt filled by clarification_compose before emit."""
    return ""


def _pick_google_account_prompt() -> str:
    return ""


def _workbook_rows_for_picker(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> List[Any]:
    """Catalog workbook rows, optionally scoped to a pinned Google account."""
    from services.planning.destination_catalog_validate import google_sheets_account_slot_satisfied

    rows = list(catalog.google_sheet_rows or [])
    if not google_sheets_account_slot_satisfied(classification, catalog):
        return rows
    conn_id = (classification.get("google_sheets_connection_id") or "").strip()
    if not conn_id:
        return rows
    scoped = [
        row
        for row in rows
        if not (row.google_sheets_connection_id or "").strip()
        or str(row.google_sheets_connection_id) == conn_id
    ]
    return scoped or rows


def _duplicate_workbook_names(catalog: ResourceCatalog) -> set[str]:
    counts: Dict[str, int] = {}
    for row in catalog.google_sheet_rows or []:
        sn = (row.spreadsheet_name or "").strip().lower()
        if sn:
            counts[sn] = counts.get(sn, 0) + 1
    return {name for name, count in counts.items() if count > 1}


def _workbook_option_label(row: Any, *, duplicate_names: set[str]) -> str:
    sn = row.spreadsheet_name
    tab = row.sheet_name or "Sheet1"
    label = f"{sn} ({tab})"
    account = (row.account_name or "").strip()
    if account and sn.lower() in duplicate_names:
        label = f"{sn} ({tab}) · {account}"
    return label


def _build_pick_google_account_clarification(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
    *,
    prompt: Optional[str] = None,
) -> Optional[Clarification]:
    from services.planning.destination_catalog_validate import needs_google_sheets_account_pick

    if not needs_google_sheets_account_pick(classification, catalog):
        return None
    options: List[ClarificationOption] = []
    for conn in catalog.google_sheets_connections or []:
        if not conn.connection_id or not conn.name:
            continue
        options.append(
            ClarificationOption(
                id=f"gs_account_{conn.connection_id}",
                label=conn.name,
                resource_ref=ResourceRef(
                    connector_type="google_sheets",
                    display_name=conn.name,
                    connector_id=str(conn.connection_id),
                ),
            )
        )
    if not options:
        return None
    return Clarification(
        id=str(uuid.uuid4()),
        clarification_type="pick_google_account",
        prompt=_pick_google_account_prompt(),
        options=options,
        required=True,
    )


def _build_pick_source_workbook_clarification(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> Optional[Clarification]:
    """Entity-layer GS source workbook picker (account must already be pinned)."""
    clar = _build_pick_workbook_clarification(classification, prompt, catalog)
    if clar is None:
        return None
    return clar.model_copy(update={"clarification_type": "pick_source_workbook"})


def _build_pick_workbook_clarification(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
) -> Optional[Clarification]:
    from services.planning.destination_catalog_validate import needs_google_sheets_account_pick

    if needs_google_sheets_account_pick(classification, catalog):
        return None
    options: List[ClarificationOption] = []
    seen_option_ids: set[str] = set()
    rows = _workbook_rows_for_picker(classification, catalog)
    duplicate_names = _duplicate_workbook_names(catalog)
    for row in rows:
        sn = row.spreadsheet_name
        if not sn:
            continue
        tab = row.sheet_name or "Sheet1"
        sid = (row.spreadsheet_id or "").strip()
        conn_id = (row.google_sheets_connection_id or "").strip()
        opt_id = f"gs_{sid}_{tab}" if sid else f"gs_{sn}_{tab}"
        if conn_id:
            opt_id = f"{opt_id}_{conn_id}"
        if opt_id in seen_option_ids:
            continue
        seen_option_ids.add(opt_id)
        options.append(
            ClarificationOption(
                id=opt_id,
                label=_workbook_option_label(row, duplicate_names=duplicate_names),
                resource_ref=ResourceRef(
                    connector_type="google_sheets",
                    display_name=sn,
                    spreadsheet_id=row.spreadsheet_id,
                    sheet_name=tab,
                    connector_id=conn_id or None,
                ),
            )
        )
    options.append(
        ClarificationOption(
            id="gs_create_new",
            label="Create new spreadsheet",
            create_new=True,
        )
    )
    if not options:
        return None
    return Clarification(
        id=str(uuid.uuid4()),
        clarification_type="pick_workbook",
        prompt=_pick_workbook_prompt(classification, prompt),
        options=options,
        required=True,
    )


def _endpoint_ambiguity_clarifications(
    classification: Dict[str, Any],
) -> List[Clarification]:
    """Ask user to pick when a phrase matches multiple similar catalog workbooks."""
    amb = classification.get("_endpoint_ambiguous") or []
    if not amb:
        return []

    clarifications: List[Clarification] = []
    for item in amb:
        if not isinstance(item, dict):
            continue
        phrase = (item.get("phrase") or "that spreadsheet").strip()
        candidates = item.get("candidates") or []
        options: List[ClarificationOption] = []
        seen: set = set()
        name_counts: Dict[str, int] = {}
        for cand in candidates:
            if isinstance(cand, dict):
                dn = (cand.get("display_name") or "").strip().lower()
                if dn:
                    name_counts[dn] = name_counts.get(dn, 0) + 1
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            name = (cand.get("display_name") or "").strip()
            tab = (cand.get("sheet_name") or "Sheet1").strip()
            sid = (cand.get("spreadsheet_id") or "").strip()
            opt_id = f"gs_amb_{sid}_{tab}" if sid else f"gs_amb_{name}_{tab}"
            if opt_id in seen or not name:
                continue
            seen.add(opt_id)
            label = f"{name} ({tab})"
            if name_counts.get(name.lower(), 0) > 1 and sid:
                label = f"{name} ({tab}) · …{sid[-6:]}"
            options.append(
                ClarificationOption(
                    id=opt_id,
                    label=label,
                    resource_ref=ResourceRef(
                        connector_type="google_sheets",
                        display_name=name,
                        spreadsheet_id=sid or None,
                        sheet_name=tab,
                    ),
                )
            )
        if options:
            clarifications.append(
                Clarification(
                    id=str(uuid.uuid4()),
                    clarification_type="pick_workbook_ambiguous",
                    prompt="",
                    options=options,
                    required=True,
                )
            )
    return clarifications


def _label_domain_ambiguity_clarifications(
    classification: Dict[str, Any],
) -> List[Clarification]:
    """Ask user to pick when business-language phrases match multiple labeled systems."""
    amb = classification.get("_label_domain_ambiguous") or []
    if not amb:
        return []

    clarifications: List[Clarification] = []
    for item in amb:
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "destination").strip()
        phrase = (item.get("phrase") or "that system").strip()
        candidates = item.get("candidates") or []
        options: List[ClarificationOption] = []
        seen: set = set()
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            ctype = (cand.get("connector_type") or "").strip()
            name = (cand.get("display_name") or "").strip()
            cid = (cand.get("connector_id") or "").strip()
            if not ctype or not name:
                continue
            opt_id = f"label_{role}_{ctype}_{cid or name}"
            if opt_id in seen:
                continue
            seen.add(opt_id)
            reasons = cand.get("match_reasons") or []
            hint = f" — {reasons[0]}" if reasons else ""
            options.append(
                ClarificationOption(
                    id=opt_id,
                    label=f"{name}{hint}",
                    resource_ref=ResourceRef(
                        connector_type=ctype,  # type: ignore[arg-type]
                        display_name=name,
                        connector_id=cid or None,
                    ),
                )
            )
        if len(options) < 2:
            continue
        role_label = "extract from" if role == "source" else "send data to"
        clarifications.append(
            Clarification(
                id=str(uuid.uuid4()),
                clarification_type="pick_connector_by_label",
                prompt="",
                options=options,
                required=True,
            )
        )
    return clarifications


def resolve_destination_connector_clarification(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
    prompt: Optional[str],
) -> Optional[Clarification]:
    from services.planning.connector_guards import is_known_connector
    from services.planning.schemas import normalize_connector_type

    dest = normalize_connector_type(classification.get("destination") or "")
    if not is_known_connector(dest):
        return _destination_connector_type_clarification(classification, catalog)

    if classification.get("planner_create_spreadsheet"):
        return None

    from services.planning.destination_catalog_validate import needs_google_sheets_account_pick

    if needs_google_sheets_account_pick(classification, catalog):
        return _build_pick_google_account_clarification(classification, catalog, prompt=prompt)

    _INSTANCE_TYPES = frozenset(
        {
            "pick_google_account",
            "pick_salesforce_org",
            "pick_sql_connector",
            "pick_workbook_ambiguous",
            "pick_connector_by_label",
            "create_workbook_name",
        }
    )
    for clar in _connector_clarifications(classification, catalog, prompt):
        if clar.clarification_type in _INSTANCE_TYPES:
            return clar
    return None


def resolve_source_connector_clarification(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
    prompt: Optional[str],
) -> Optional[Clarification]:
    """
    Catalog-backed source instance picker when connector type is already known.

    Google Sheets: account → workbook → tab (mirrors destination resolution).
    """
    from services.planning.connector_guards import is_known_connector
    from services.planning.schemas import normalize_connector_type
    from services.planning.source_catalog_validate import (
        needs_google_sheets_account_pick_source,
        needs_google_sheets_source_clarification,
        read_needs_source,
    )
    from services.planning.plan_scope import read_plan_scope

    src = normalize_connector_type(classification.get("source") or "")
    if not is_known_connector(src):
        return None

    parsed_goal = read_plan_scope(classification) or None
    if not read_needs_source(classification, parsed_goal):
        return None

    if src in ("google_sheets", "gsheets"):
        if needs_google_sheets_account_pick_source(classification, catalog):
            return _build_pick_google_account_clarification(
                classification, catalog, prompt=prompt
            )
        return None

    _INSTANCE_TYPES = frozenset(
        {
            "pick_salesforce_org",
            "pick_sql_connector",
            "pick_connector_by_label",
        }
    )
    for clar in _connector_clarifications(classification, catalog, prompt):
        if clar.clarification_type in _INSTANCE_TYPES:
            return clar
    return None


async def _supplement_connector_clarifications(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
    *,
    user_id: Optional[str] = None,
) -> List[Clarification]:
    """
    Non-slot clarifications: label/endpoint ambiguity, Google Sheets pickers, flows.

    Requirement slots are handled by clarification_queue.planner_clarifications_from_requirements.
    """
    from services.planning.destination_catalog_validate import (
        needs_google_sheets_destination_clarification,
    )
    from services.planning.plan_scope import read_plan_scope

    parsed_goal = read_plan_scope(classification) or None
    needs_gsheets_clarification = needs_google_sheets_destination_clarification(
        classification, catalog, parsed_goal=parsed_goal
    )

    clarifications: List[Clarification] = []
    clarifications.extend(_label_domain_ambiguity_clarifications(classification))
    clarifications.extend(_endpoint_ambiguity_clarifications(classification))

    if needs_gsheets_clarification:
        account_clar = _build_pick_google_account_clarification(
            classification, catalog, prompt=prompt
        )
        if account_clar:
            clarifications.append(account_clar)
        else:
            picker = _build_pick_workbook_clarification(classification, prompt, catalog)
            if picker:
                clarifications.append(picker)

    clarifications.extend(
        _connector_clarifications(
            classification,
            catalog,
            prompt,
            skip_types={c.clarification_type for c in clarifications},
        )
    )

    if user_id:
        from services.planning.flow_steps import detect_flow_clarifications, list_user_flows

        flows = await list_user_flows(user_id)
        clarifications.extend(detect_flow_clarifications(classification, prompt, flows))

    return clarifications


async def detect_clarifications(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
    *,
    user_id: Optional[str] = None,
) -> List[Clarification]:
    from services.planning.connector_endpoint_confirm import (
        sanitize_unconfirmed_connector_endpoints,
    )

    classification = sanitize_unconfirmed_connector_endpoints(
        dict(classification or {}), prompt, catalog
    )

    if classification.get("retry_after_partial") and classification.get("active_job_id"):
        dest = (classification.get("destination") or "").strip().lower()
        dest_name = (classification.get("destination_name") or "").strip()
        if dest and dest != "unknown" and dest_name and dest_name.lower() != "unknown":
            return []

    from services.planning.op_requirements import (
        derive_plan_requirements,
        read_plan_requirements,
    )
    from services.planning.plan_scope import read_plan_scope
    from services.planning.destination_catalog_validate import (
        needs_google_sheets_destination_clarification,
    )
    from services.planning.source_catalog_validate import (
        needs_google_sheets_source_clarification,
    )

    parsed_goal = read_plan_scope(classification) or None
    needs_gsheets_clarification = needs_google_sheets_destination_clarification(
        classification, catalog, parsed_goal=parsed_goal
    )
    needs_gsheets_source_clarification = needs_google_sheets_source_clarification(
        classification, catalog, parsed_goal=parsed_goal
    )

    plan_reqs = read_plan_requirements(classification, parsed_goal=parsed_goal)
    if classification.get("planner_create_spreadsheet"):
        tokens = list(plan_reqs.pipeline_tokens or [])
        if "create" not in tokens:
            from services.planning.create_metadata import resolve_create_pipeline_tokens

            expanded = resolve_create_pipeline_tokens(
                classification,
                parsed_goal or {},
                pipeline_tokens=[*tokens, "create"],
            )
            plan_reqs = derive_plan_requirements(
                expanded,
                classification=classification,
                parsed_goal=parsed_goal,
            )

    use_requirement_queue = bool(
        plan_reqs.pipeline_tokens
        or classification.get("_plan_requirements")
        or (parsed_goal and parsed_goal.get("operations"))
        or classification.get("planner_create_spreadsheet")
    )
    if use_requirement_queue:
        from services.planning.clarification_queue import planner_clarifications_from_requirements

        req_clars = planner_clarifications_from_requirements(
            classification,
            catalog,
            prompt=prompt,
            parsed_goal=parsed_goal,
        )
        if needs_gsheets_clarification:
            req_clars = [
                c for c in req_clars if c.clarification_type != "create_workbook_name"
            ]
        if req_clars:
            from services.planning.connector_guards import is_known_connector
            from services.planning.schemas import normalize_connector_type

            dest = normalize_connector_type(classification.get("destination") or "")
            src = normalize_connector_type(classification.get("source") or "")
            if is_known_connector(dest):
                req_clars = [
                    c
                    for c in req_clars
                    if c.clarification_type != "pick_destination_connector"
                ]
                if not req_clars:
                    specific = resolve_destination_connector_clarification(
                        classification, catalog, prompt
                    )
                    if specific:
                        return [specific]
            if is_known_connector(src) and src in ("google_sheets", "gsheets"):
                req_clars = [
                    c
                    for c in req_clars
                    if c.clarification_type != "pick_source_connector"
                ]
                if not req_clars and needs_gsheets_source_clarification:
                    specific = resolve_source_connector_clarification(
                        classification, catalog, prompt
                    )
                    if specific:
                        return [specific]
            if req_clars:
                return req_clars

    return await _supplement_connector_clarifications(
        classification, prompt, catalog, user_id=user_id
    )


def _defer_destination_connector_gaps(
    classification: Dict[str, Any],
    *,
    pipeline_tokens: Optional[List[str]] = None,
) -> bool:
    """
    Keep destination connector pickers behind source endpoint → table → columns.

    Avoids jumping to destination org before extract source is fully pinned.
    """
    from services.planning.connector_guards import (
        classification_needs_destination_endpoint,
        classification_needs_source_endpoint,
        classification_source_endpoint_resolved,
    )

    tokens = pipeline_tokens if pipeline_tokens is not None else classification.get("_pipeline_tokens")
    if tokens is not None and not any(t in tokens for t in ("map", "migrate")):
        return True

    if not classification_needs_destination_endpoint(classification, pipeline_tokens=tokens):
        return False
    if not classification_needs_source_endpoint(classification, pipeline_tokens=tokens):
        return False
    if not classification_source_endpoint_resolved(classification):
        return True
    if classification.get("extract_fields_confirmed"):
        return False
    raw_intent = classification.get("migration_intent")
    if isinstance(raw_intent, dict) and raw_intent.get("extract_fields_confirmed"):
        return False
    if classification.get("planner_source_entity_confirmed"):
        return False
    if (classification.get("source_table_name") or classification.get("source_object_api_name") or "").strip():
        return False
    if classification.get("needs_extract_clarification"):
        return True
    return True


def _connector_clarifications(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
    prompt: Optional[str],
    *,
    skip_types: Optional[set] = None,
) -> List[Clarification]:
    """SF org, SQL connection, and GSheets tab pickers when ambiguous."""
    skip = skip_types or set()
    out: List[Clarification] = []
    defer_destination = _defer_destination_connector_gaps(classification)

    def _sf_options(role: str) -> List[ClarificationOption]:
        options: List[ClarificationOption] = []
        for conn in catalog.salesforce_connections:
            if not conn.name:
                continue
            options.append(
                ClarificationOption(
                    id=f"sf_{role}_{conn.name}",
                    label=conn.name,
                    resource_ref=ResourceRef(
                        connector_type="salesforce",
                        display_name=conn.name,
                        connector_id=str(conn.token_id) if conn.token_id else None,
                    ),
                )
            )
        return options

    src = (classification.get("source") or "").lower()
    dest = (classification.get("destination") or "").lower()
    from services.planning.connector_guards import is_known_connector

    if (
        "pick_source_connector" not in skip
        and not is_known_connector(src)
        and _intent_needs_extract_source(classification)
    ):
        src_pick = _source_connector_type_clarification(classification, catalog)
        if src_pick:
            out.append(src_pick)
            return out

    if (
        not defer_destination
        and "pick_destination_connector" not in skip
        and not is_known_connector(dest)
        and _intent_needs_migrate_destination(classification)
    ):
        dest_pick = _destination_connector_type_clarification(classification, catalog)
        if dest_pick:
            out.append(dest_pick)
            return out

    from services.planning.connector_endpoint_confirm import (
        destination_endpoint_is_confirmed,
        source_connector_authoritatively_confirmed,
        source_endpoint_is_confirmed,
    )
    from services.planning.connector_guards import (
        classification_destination_endpoint_resolved,
        classification_source_endpoint_resolved,
    )
    from services.planning.destination_catalog_validate import (
        destination_connector_authoritatively_confirmed,
    )
    from services.planning.planner_path import classification_has_plan_scope

    use_catalog_gates = classification_has_plan_scope(classification)

    if (
        "pick_salesforce_org" not in skip
        and is_known_connector(src)
        and src == "salesforce"
        and len(catalog.salesforce_connections) > 1
    ):
        needs_source_org = (
            not source_connector_authoritatively_confirmed(classification)
            and not classification_source_endpoint_resolved(classification)
            if use_catalog_gates
            else not source_endpoint_is_confirmed(classification, prompt, catalog)
        )
        if needs_source_org:
            opts = _sf_options("source")
            if opts:
                out.append(
                    Clarification(
                        id=str(uuid.uuid4()),
                        clarification_type="pick_salesforce_org",
                        prompt="",
                        options=opts,
                        required=True,
                    )
                )

    if (
        "pick_google_account" not in skip
        and is_known_connector(src)
        and src in ("google_sheets", "gsheets")
        and _intent_needs_extract_source(classification)
    ):
        from services.planning.source_catalog_validate import (
            needs_google_sheets_account_pick_source,
            needs_google_sheets_source_clarification,
        )
        from services.planning.plan_scope import read_plan_scope

        parsed_goal = read_plan_scope(classification) or None
        if needs_google_sheets_account_pick_source(classification, catalog):
            account_pick = _build_pick_google_account_clarification(
                classification, catalog, prompt=prompt
            )
            if account_pick:
                out.append(account_pick)
                return out
        if needs_google_sheets_source_clarification(
            classification, catalog, parsed_goal=parsed_goal
        ):
            picker = _build_pick_workbook_clarification(classification, prompt, catalog)
            if picker:
                out.append(picker)
                return out

    if (
        not defer_destination
        and "pick_google_account" not in skip
        and is_known_connector(dest)
        and dest in ("google_sheets", "gsheets")
        and _intent_needs_migrate_destination(classification)
    ):
        account_pick = _build_pick_google_account_clarification(
            classification, catalog, prompt=prompt
        )
        if account_pick:
            out.append(account_pick)
            return out

    if (
        not defer_destination
        and "pick_salesforce_org" not in skip
        and is_known_connector(dest)
        and dest == "salesforce"
        and len(catalog.salesforce_connections) > 1
        and not classification.get("planner_create_spreadsheet")
    ):
        needs_dest_org = (
            not destination_connector_authoritatively_confirmed(classification)
            and not classification_destination_endpoint_resolved(
                classification, catalog=catalog
            )
            if use_catalog_gates
            else not destination_endpoint_is_confirmed(classification, prompt, catalog)
        )
        if needs_dest_org:
            opts = _sf_options("destination")
            if opts:
                out.append(
                    Clarification(
                        id=str(uuid.uuid4()),
                        clarification_type="pick_salesforce_org",
                        prompt="",
                        options=opts,
                        required=True,
                    )
                )

    if (
        "pick_sql_connector" not in skip
        and is_known_connector(src)
        and src == "sql"
        and not classification_source_endpoint_resolved(classification)
        and len(catalog.sql_connections) > 1
        and not classification.get("plan_thread_active")
    ):
        opts = [
            ClarificationOption(
                id=f"sql_source_{c.name}",
                label=c.name,
                resource_ref=ResourceRef(
                    connector_type="sql",
                    display_name=c.name,
                    connector_id=c.connector_id,
                ),
            )
            for c in catalog.sql_connections
            if c.name
        ]
        if opts:
            out.append(
                Clarification(
                    id=str(uuid.uuid4()),
                    clarification_type="pick_sql_connector",
                    prompt="",
                    options=opts,
                    required=True,
                )
            )

    if (
        not defer_destination
        and "pick_sql_connector" not in skip
        and is_known_connector(dest)
        and dest == "sql"
        and _intent_needs_migrate_destination(classification)
        and not destination_endpoint_is_confirmed(classification, prompt, catalog)
        and len(catalog.sql_connections) > 1
    ):
        opts = [
            ClarificationOption(
                id=f"sql_destination_{c.name}",
                label=c.name,
                resource_ref=ResourceRef(
                    connector_type="sql",
                    display_name=c.name,
                    connector_id=c.connector_id,
                ),
            )
            for c in catalog.sql_connections
            if c.name
        ]
        if opts:
            out.append(
                Clarification(
                    id=str(uuid.uuid4()),
                    clarification_type="pick_sql_connector",
                    prompt="",
                    options=opts,
                    required=True,
                )
            )

    if "pick_tab" not in skip and not classification.get("planner_create_spreadsheet"):
        for role, conn_key, name_key, tab_key in (
            ("source", "source", "source_name", "source_sheet_name"),
            ("destination", "destination", "destination_name", "destination_sheet_name"),
        ):
            if (classification.get(conn_key) or "").lower() not in ("google_sheets", "gsheets"):
                continue
            wb_name = (classification.get(name_key) or "").strip()
            if _is_unknown_name(wb_name) or classification.get(tab_key):
                continue
            tabs = _tabs_for_workbook(wb_name, catalog)
            unique_tabs = sorted({(t.sheet_name or "Sheet1") for t in tabs})
            if len(unique_tabs) <= 1:
                continue
            tab_opts = [
                ClarificationOption(
                    id=f"tab_{role}_{wb_name}_{tab}",
                    label=f"{wb_name} — {tab}",
                    resource_ref=ResourceRef(
                        connector_type="google_sheets",
                        display_name=wb_name,
                        sheet_name=tab,
                        spreadsheet_id=tabs[0].spreadsheet_id if tabs else None,
                    ),
                )
                for tab in unique_tabs
            ]
            if tab_opts:
                out.append(
                    Clarification(
                        id=str(uuid.uuid4()),
                        clarification_type="pick_tab",
                        prompt="",
                        options=tab_opts,
                        required=True,
                    )
                )

    return out
