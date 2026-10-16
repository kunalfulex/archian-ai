"""Apply user clarification selections to classification and execution plan."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef


_GENERIC_CONNECTOR_NAMES = {"SQL database", "Salesforce org", "Google Sheets"}


def _is_concrete_connector_ref(ref: ResourceRef) -> bool:
    if (ref.connector_id or "").strip():
        return True
    name = (ref.display_name or "").strip()
    return bool(name and name not in _GENERIC_CONNECTOR_NAMES)


def _source_connector_changed(
    prior: Dict[str, Any],
    ref: ResourceRef,
) -> bool:
    """True when the user replaces an already-resolved source connector."""
    old_id = (prior.get("source_connector_id") or "").strip()
    new_id = (ref.connector_id or "").strip()
    if old_id and new_id and old_id != new_id:
        return True
    old_name = (prior.get("source_name") or "").strip()
    new_name = (ref.display_name or "").strip()
    if (
        old_name
        and new_name
        and old_name not in _GENERIC_CONNECTOR_NAMES
        and new_name not in _GENERIC_CONNECTOR_NAMES
        and old_name.lower() != new_name.lower()
    ):
        return True
    old_type = (prior.get("source") or "").strip().lower()
    new_type = (ref.connector_type or "").strip().lower()
    if old_type and old_type not in ("", "unknown") and new_type and old_type != new_type:
        return True
    return False


def _should_clear_entity_state_on_source_pick(
    prior: Dict[str, Any],
    ref: ResourceRef,
    classification: Dict[str, Any],
) -> bool:
    """Clear stale entity/field intent when source connector changes or cls has stale intent."""
    if _source_connector_changed(prior, ref):
        return True
    if classification.get("migration_intent"):
        return True
    return False


def _needs_dest(classification: Dict[str, Any]) -> bool:
    dn = (classification.get("destination_name") or "").strip().lower()
    return not dn or dn == "unknown"


def _gsheets_account_pick_role(classification: Dict[str, Any]) -> str:
    """Source vs destination for pick_google_account."""
    from services.planning.destination_catalog_validate import read_needs_destination
    from services.planning.plan_scope import read_plan_scope
    from services.planning.source_catalog_validate import read_needs_source

    src = str(classification.get("source") or "").lower()
    dest = str(classification.get("destination") or "").lower()
    goal = read_plan_scope(classification) or {}
    needs_src = read_needs_source(classification, goal)
    needs_dest = read_needs_destination(classification, goal)
    if src in ("google_sheets", "gsheets") and needs_src:
        if dest in ("google_sheets", "gsheets") and needs_dest:
            return "destination"
        return "source"
    return "destination"


def _gsheets_workbook_pick_role(classification: Dict[str, Any]) -> str:
    """Source vs destination for pick_workbook when slot mapping is ambiguous."""
    from services.planning.destination_catalog_validate import read_needs_destination
    from services.planning.plan_scope import read_plan_scope
    from services.planning.source_catalog_validate import read_needs_source

    src = str(classification.get("source") or "").lower()
    dest = str(classification.get("destination") or "").lower()
    goal = read_plan_scope(classification) or {}
    ops = [str(o).lower() for o in (goal.get("operations") or classification.get("operations") or [])]
    extract_only = ops == ["extract"] or (
        "extract" in ops and not read_needs_destination(classification, goal)
    )
    if src in ("google_sheets", "gsheets") and read_needs_source(classification, goal):
        if extract_only or dest not in ("google_sheets", "gsheets"):
            return "source"
    if dest in ("google_sheets", "gsheets") and read_needs_destination(classification, goal):
        return "destination"
    return "source" if src in ("google_sheets", "gsheets") else "destination"


def _connector_slot_role(clarification_type: str) -> Optional[str]:
    """Map clarification type to source/destination via requirement slot."""
    from services.planning.clarification_queue import clarification_slot
    from services.planning.op_requirements import DESTINATION_CONNECTOR, SOURCE_CONNECTOR

    slot = clarification_slot(clarification_type or "")
    if slot == SOURCE_CONNECTOR:
        return "source"
    if slot == DESTINATION_CONNECTOR:
        return "destination"
    return None


def _workbook_pick_role(classification: Dict[str, Any], clarification_type: str) -> str:
    return _connector_slot_role(clarification_type) or _gsheets_workbook_pick_role(classification)


def _gsheets_ambiguous_workbook_role(classification: Dict[str, Any]) -> str:
    """
    pick_workbook_ambiguous resolves a workbook phrase on source or destination.
    Extract-from-sheets → SF/SQL uses source; export-to-sheets uses destination.
    """
    dest = str(classification.get("destination") or "").lower()
    src = str(classification.get("source") or "").lower()
    if dest in ("google_sheets", "gsheets") and src not in ("google_sheets", "gsheets"):
        return "destination"
    if src in ("google_sheets", "gsheets"):
        return "source"
    if dest in ("salesforce", "sql"):
        return "source"
    return "destination"


def _arm_create_destination_from_user_choice(classification: Dict[str, Any]) -> Dict[str, Any]:
    from services.planning.plan_scope import arm_create_destination_from_user_choice

    return arm_create_destination_from_user_choice(classification)


def _apply_google_sheets_workbook_ref(
    out: Dict[str, Any],
    ref: ResourceRef,
    *,
    role: str,
) -> None:
    if role == "source":
        out["source"] = "google_sheets"
        out["source_name"] = ref.display_name
        if ref.sheet_name:
            out["source_sheet_name"] = ref.sheet_name
        if ref.spreadsheet_id:
            out["source_spreadsheet_id"] = ref.spreadsheet_id
        if (ref.connector_id or "").strip():
            cid = str(ref.connector_id).strip()
            out["google_sheets_connection_id"] = cid
            out["source_connector_id"] = cid
        sid = (ref.spreadsheet_id or "").strip()
        tab = (ref.sheet_name or "").strip()
        if sid and tab:
            out["source_object_api_name"] = f"{sid}::{tab}"
            out["source_table_name"] = f"{sid}::{tab}"
        out.pop("_endpoint_ambiguous", None)
        return
    out["destination"] = "google_sheets"
    out["destination_name"] = ref.display_name
    if ref.sheet_name:
        out["destination_sheet_name"] = ref.sheet_name
    if ref.spreadsheet_id:
        out["destination_spreadsheet_id"] = ref.spreadsheet_id
    if (ref.connector_id or "").strip():
        cid = str(ref.connector_id).strip()
        out["google_sheets_connection_id"] = cid
        out["destination_connector_id"] = cid
        if ref.display_name:
            out["destination_account_name"] = ref.display_name
    out["planner_destination_confirmed"] = True
    out["_destination_endpoint_clarified"] = True
    out["_destination_connector_validated"] = True
    out["planner_create_spreadsheet"] = False
    out.pop("planner_new_spreadsheet_name", None)
    out.pop("_endpoint_ambiguous", None)
    sid = (ref.spreadsheet_id or "").strip()
    tab = (ref.sheet_name or "").strip()
    if sid and tab:
        out["destination_object_api_name"] = f"{sid}::{tab}"
        out["planner_destination_entity_confirmed"] = True
    elif tab:
        out["destination_object_api_name"] = tab
        out["planner_destination_entity_confirmed"] = True


def _restore_planner_endpoint_pins(
    out: Dict[str, Any],
    prior: Dict[str, Any],
    *,
    prior_plan: Optional[ExecutionPlan] = None,
    plan: Optional[ExecutionPlan] = None,
) -> tuple[Dict[str, Any], Optional[ExecutionPlan]]:
    """Keep connector picks from earlier planner steps after later clarifications."""
    from services.planning.plan_scope import read_plan_scope, write_plan_scope
    from services.planning.planning_checkpoint import should_restore_pin
    from services.planning.slot_fills import (
        read_slot_fills,
        restore_slot_fills,
        sync_authoritative_flags_from_slot_fills,
        write_slot_fills_to_plan,
    )

    merged = dict(out or {})
    prior = prior or {}
    for flag in ("_source_endpoint_clarified", "_destination_endpoint_clarified"):
        if prior.get(flag):
            merged[flag] = True
    for key in (
        "source",
        "source_name",
        "source_connector_id",
        "source_spreadsheet_id",
        "source_sheet_name",
        "source_table_name",
        "source_object_api_name",
        "destination",
        "destination_name",
        "destination_connector_id",
        "destination_spreadsheet_id",
        "destination_sheet_name",
        "destination_object_api_name",
        "google_sheets_connection_id",
        "operations",
        "planner_new_spreadsheet_name",
        "planner_spreadsheet_title_confirmed",
    ):
        if should_restore_pin(prior.get(key), merged.get(key)):
            merged[key] = prior.get(key)

    prior_scope = read_plan_scope(prior)
    if prior_scope and not read_plan_scope(merged):
        merged = write_plan_scope(merged, prior_scope)
        if prior_scope.get("operations") and not merged.get("operations"):
            merged["operations"] = prior_scope["operations"]

    prior_fills = read_slot_fills(prior, prior_plan)
    if prior_fills and plan is not None:
        merged_fills, plan = restore_slot_fills(merged, plan, prior, prior_plan)
        plan = write_slot_fills_to_plan(plan, merged_fills)
    elif prior_fills:
        merged["_slot_fills"] = {**prior_fills, **read_slot_fills(merged)}

    merged = sync_authoritative_flags_from_slot_fills(merged, plan=plan)
    return merged, plan


def _sync_migration_intent_to_classification(
    out: Dict[str, Any],
    raw_intent: Any,
) -> Dict[str, Any]:
    """Mirror confirmed extract field/table state onto classification for gap detection."""
    if not raw_intent:
        return out
    if isinstance(raw_intent, dict):
        intent = raw_intent
    elif hasattr(raw_intent, "model_dump"):
        intent = raw_intent.model_dump(mode="json")
    else:
        intent = {}
    if intent.get("extract_fields_confirmed"):
        out["extract_fields_confirmed"] = True
        out["needs_extract_clarification"] = False
    key = (intent.get("driving_entity_key") or "").strip()
    if key:
        from services.planning.schemas import normalize_connector_type

        ct = normalize_connector_type(out.get("source") or "")
        if ct == "sql" and not (out.get("source_table_name") or "").strip():
            out["source_table_name"] = key
        elif ct == "salesforce" and not (out.get("source_object_api_name") or "").strip():
            out["source_object_api_name"] = key
    return out


def _refresh_migration_intent_after_entity_pick(
    plan: ExecutionPlan,
    *,
    role: str,
    entity_key: str,
) -> None:
    """Keep migration intent across entity picks; only drop stale plan contract."""
    plan.metadata = dict(plan.metadata or {})
    plan.metadata.pop("plan_contract", None)
    raw = plan.metadata.get("migration_intent")
    if not raw:
        return
    from services.planning.migration_intent_schemas import PromptMigrationIntent

    intent = PromptMigrationIntent.model_validate(raw)
    key = (entity_key or "").strip()
    if not key:
        return
    if role == "source":
        intent = intent.model_copy(
            update={
                "driving_entity_key": key,
                "driving_entity_label": key,
            }
        )
    else:
        intent = intent.model_copy(
            update={
                "destination_entity_key": key,
                "destination_entity_label": key,
                "destination_entity_phrase": key,
            }
        )
    plan.metadata["migration_intent"] = intent.model_dump(mode="json")


def _clear_plan_entity_state_after_source_change(
    plan: ExecutionPlan,
    classification: Dict[str, Any],
) -> None:
    """Drop stale entity/field intent when the user changes extract source connector."""
    plan.metadata = dict(plan.metadata or {})
    plan.metadata.pop("migration_intent", None)
    plan.metadata.pop("plan_contract", None)
    classification.pop("plan_contract", None)
    classification.pop("migration_intent", None)
    snap = classification.get("intent_state")
    if isinstance(snap, dict):
        updated = dict(snap)
        updated["source_entity"] = {
            "entity_key": None,
            "entity_label": None,
            "confidence": 0.0,
            "resolved": False,
        }
        fields = dict(updated.get("fields") or {})
        fields.update(
            {
                "scope": "unspecified",
                "scope_confidence": 0.0,
                "hints": [],
                "hints_strict": False,
                "confirmed": False,
            }
        )
        updated["fields"] = fields
        classification["intent_state"] = updated
    for key in (
        "source_object_api_name",
        "source_table_name",
        "source_sheet_name",
        "source_spreadsheet_id",
    ):
        classification.pop(key, None)


def _strip_wrapping_quotes(label: str) -> str:
    """Remove surrounding quotes and trailing punctuation from pasted suggestion text."""
    cleaned = (label or "").strip()
    while cleaned and cleaned[-1] in ".,;":
        cleaned = cleaned[:-1].strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _label_from_header_option(opt: str) -> Optional[str]:
    opt = (opt or "").strip()
    if opt.startswith("text:"):
        return _strip_wrapping_quotes(opt[5:])
    if opt.startswith("header:"):
        return _strip_wrapping_quotes(opt[7:])
    return None


def _headers_from_create_header_selections(
    matching: List[Dict[str, Any]],
    clar: Any,
) -> List[str]:
    """Aggregate multi-select chip picks and optional comma-separated text_value."""
    headers: List[str] = []
    seen: set[str] = set()
    options = clar.options or []

    for sel in matching:
        text_val = (sel.get("text_value") or "").strip()
        opt = (sel.get("option_id") or "").strip()
        if text_val:
            from services.planning.extract_field_clarification import parse_field_clarification_reply

            parsed = parse_field_clarification_reply(text_val, options)
            raw = (parsed or {}).get("text_value") or text_val
            for part in str(raw).split(","):
                label = _strip_wrapping_quotes(part.strip())
                key = label.lower()
                if label and key not in seen:
                    headers.append(label)
                    seen.add(key)
            continue
        label = _label_from_header_option(opt)
        if label:
            key = label.lower()
            if key not in seen:
                headers.append(label)
                seen.add(key)
    return headers


def apply_clarification_selections(
    classification: Dict[str, Any],
    plan: ExecutionPlan,
    selections: List[Dict[str, Any]],
    *,
    catalog: Any = None,
) -> tuple[Dict[str, Any], ExecutionPlan]:
    """
    Update plan clarifications and classification destination from user choices.
    selections: [{ "clarification_id", "option_id", optional "text_value" / "new_workbook_title" }]
    """
    from services.planning.resource_catalog import ResourceCatalog, coerce_planning_catalog

    out = dict(classification or {})
    plan = plan.model_copy(deep=True)
    resolved_catalog = coerce_planning_catalog(catalog, out)
    if resolved_catalog is not None:
        out["_planning_catalog"] = resolved_catalog

    for clar in plan.clarifications:
        sel = next(
            (s for s in (selections or []) if s.get("clarification_id") == clar.id),
            None,
        )
        if not sel:
            continue
        opt_id = (sel.get("option_id") or "").strip()
        text_val = sel.get("text_value") or sel.get("new_workbook_title")
        clar.selected_option_id = opt_id

        if clar.clarification_type == "create_workbook_name":
            name = (text_val or "").strip() if text_val else ""
            if not name and opt_id.startswith("text:"):
                name = opt_id[5:].strip()
            if name:
                out["planner_new_spreadsheet_name"] = name
                out["destination"] = "google_sheets"
                out["destination_name"] = name
                out["planner_create_spreadsheet"] = True
                out["planner_spreadsheet_title_confirmed"] = True
                out["planner_destination_confirmed"] = True
                scope = dict(out.get("_plan_scope") or {})
                scope["spreadsheet_title"] = name
                out["_plan_scope"] = scope
            continue

        if clar.clarification_type == "pick_create_headers":
            matching = [s for s in (selections or []) if s.get("clarification_id") == clar.id]
            headers = _headers_from_create_header_selections(matching, clar)
            if headers:
                out["planner_spreadsheet_headers"] = headers
                out["planner_create_spreadsheet"] = True
                out["destination"] = out.get("destination") or "google_sheets"
                scope = dict(out.get("_plan_scope") or {})
                scope["spreadsheet_headers"] = headers
                from services.planning.source_column_headers import (
                    HEADER_SOURCE_USER_SPECIFIED_INLINE,
                )

                scope["spreadsheet_header_source"] = HEADER_SOURCE_USER_SPECIFIED_INLINE
                out["_plan_scope"] = scope
            clar.selected_option_id = clar.selected_option_id or "headers_confirmed"
            continue

        if clar.clarification_type == "pick_extract_fields":
            matching = [s for s in (selections or []) if s.get("clarification_id") == clar.id]
            if matching:
                clar.selected_option_id = (matching[0].get("option_id") or "").strip() or "fields_confirmed"
            continue

        if clar.clarification_type in ("pick_source_entity", "pick_destination_entity") and opt_id:
            entity_key = opt_id.split(":", 1)[-1].strip() if ":" in opt_id else opt_id
            role = "destination" if clar.clarification_type == "pick_destination_entity" else "source"
            from services.planning.schemas import normalize_connector_type

            ct = normalize_connector_type(
                out.get("source" if role == "source" else "destination") or ""
            )
            if role == "destination":
                if ct == "salesforce":
                    out["destination_object_api_name"] = entity_key
                elif ct == "sql":
                    out["destination_table_name"] = entity_key
                elif ct == "google_sheets":
                    out["destination_sheet_name"] = entity_key
                out["planner_destination_entity_confirmed"] = True
            else:
                if ct == "salesforce":
                    out["source_object_api_name"] = entity_key
                elif ct == "sql":
                    out["source_table_name"] = entity_key
                elif ct == "google_sheets":
                    out["source_sheet_name"] = entity_key
                out["planner_source_entity_confirmed"] = True
            _refresh_migration_intent_after_entity_pick(
                plan, role=role, entity_key=entity_key
            )
            continue

        chosen: Optional[ClarificationOption] = next(
            (o for o in clar.options if o.id == opt_id),
            None,
        )
        if not chosen:
            continue

        if clar.clarification_type == "pick_source_connector":
            if chosen.resource_ref:
                ref = chosen.resource_ref
                prior_source = {
                    "source": out.get("source"),
                    "source_name": out.get("source_name"),
                    "source_connector_id": out.get("source_connector_id"),
                }
                out["source"] = ref.connector_type
                if ref.display_name and ref.display_name not in _GENERIC_CONNECTOR_NAMES:
                    out["source_name"] = ref.display_name
                if ref.connector_id:
                    out["source_connector_id"] = ref.connector_id
                if ref.sheet_name:
                    out["source_sheet_name"] = ref.sheet_name
                if ref.spreadsheet_id:
                    out["source_spreadsheet_id"] = ref.spreadsheet_id
                if _is_concrete_connector_ref(ref):
                    out["_source_endpoint_clarified"] = True
                else:
                    out.pop("_source_endpoint_clarified", None)
                if _should_clear_entity_state_on_source_pick(prior_source, ref, out):
                    _clear_plan_entity_state_after_source_change(plan, out)
            continue

        if clar.clarification_type == "pick_destination_connector":
            if chosen.resource_ref:
                ref = chosen.resource_ref
                out["destination"] = ref.connector_type
                if ref.display_name and ref.display_name not in _GENERIC_CONNECTOR_NAMES:
                    out["destination_name"] = ref.display_name
                if ref.connector_id:
                    out["destination_connector_id"] = ref.connector_id
                if ref.sheet_name:
                    out["destination_sheet_name"] = ref.sheet_name
                if ref.spreadsheet_id:
                    out["destination_spreadsheet_id"] = ref.spreadsheet_id
                if ref.connector_type == "google_sheets":
                    out["planner_destination_confirmed"] = True
                if _is_concrete_connector_ref(ref):
                    out["_destination_endpoint_clarified"] = True
                else:
                    out.pop("_destination_endpoint_clarified", None)
                plan.metadata.pop("plan_contract", None)
                plan.metadata.pop("migration_intent", None)
            continue

        if clar.clarification_type == "pick_connector_by_label":
            if chosen.resource_ref:
                ref = chosen.resource_ref
                role = "destination" if opt_id.startswith("label_destination_") else "source"
                if opt_id.startswith("label_source_"):
                    role = "source"
                elif opt_id.startswith("label_destination_"):
                    role = "destination"
                if role == "destination":
                    out["destination"] = ref.connector_type
                    out["destination_name"] = ref.display_name
                    if ref.connector_id:
                        out["destination_connector_id"] = ref.connector_id
                    if ref.connector_type == "google_sheets":
                        out["planner_destination_confirmed"] = True
                else:
                    out["source"] = ref.connector_type
                    out["source_name"] = ref.display_name
                    if ref.connector_id:
                        out["source_connector_id"] = ref.connector_id
                out.pop("_label_domain_ambiguous", None)
            continue

        if clar.clarification_type == "pick_google_account":
            if chosen.resource_ref:
                from services.planning.google_sheets_destination import apply_google_sheets_account_pick
                from services.planning.google_sheets_source import (
                    apply_google_sheets_source_account_pick,
                )
                from services.planning.resource_catalog import ResourceCatalog, coerce_planning_catalog

                gs_catalog = coerce_planning_catalog(resolved_catalog, out) or ResourceCatalog()
                role = _gsheets_account_pick_role(out)
                if role == "source":
                    out = apply_google_sheets_source_account_pick(
                        out, chosen.resource_ref, gs_catalog
                    )
                else:
                    out = apply_google_sheets_account_pick(
                        out, chosen.resource_ref, gs_catalog
                    )
            continue

        if clar.clarification_type == "pick_salesforce_org":
            if chosen.resource_ref:
                ref = chosen.resource_ref
                role = "destination" if opt_id.startswith("sf_destination_") else "source"
                if opt_id.startswith("sf_destination_"):
                    role = "destination"
                elif opt_id.startswith("sf_source_"):
                    role = "source"
                else:
                    role = "destination" if out.get("destination") == "salesforce" and _needs_dest(
                        out
                    ) else "source"
                if role == "destination":
                    out["destination"] = "salesforce"
                    out["destination_name"] = ref.display_name
                    if ref.connector_id:
                        out["destination_connector_id"] = ref.connector_id
                    out["_destination_endpoint_clarified"] = True
                    plan.metadata.pop("plan_contract", None)
                else:
                    prior_source = {
                        "source": out.get("source"),
                        "source_name": out.get("source_name"),
                        "source_connector_id": out.get("source_connector_id"),
                    }
                    out["source"] = "salesforce"
                    out["source_name"] = ref.display_name
                    if ref.connector_id:
                        out["source_connector_id"] = ref.connector_id
                    out["_source_endpoint_clarified"] = True
                    if _should_clear_entity_state_on_source_pick(prior_source, ref, out):
                        _clear_plan_entity_state_after_source_change(plan, out)
            continue

        if clar.clarification_type == "pick_sql_connector":
            if chosen.resource_ref:
                ref = chosen.resource_ref
                if opt_id.startswith("sql_destination_"):
                    role = "destination"
                else:
                    role = "source"

                if role == "destination":
                    out["destination"] = "sql"
                    out["destination_name"] = ref.display_name
                    if ref.connector_id:
                        out["destination_connector_id"] = ref.connector_id
                    out["_destination_endpoint_clarified"] = True
                    plan.metadata.pop("plan_contract", None)
                else:
                    prior_source = {
                        "source": out.get("source"),
                        "source_name": out.get("source_name"),
                        "source_connector_id": out.get("source_connector_id"),
                    }
                    out["source"] = "sql"
                    out["source_name"] = ref.display_name
                    if ref.connector_id:
                        out["source_connector_id"] = ref.connector_id
                    out["_source_endpoint_clarified"] = True
                    if _should_clear_entity_state_on_source_pick(prior_source, ref, out):
                        _clear_plan_entity_state_after_source_change(plan, out)
            continue

        if clar.clarification_type == "pick_tab":
            if chosen.resource_ref:
                ref = chosen.resource_ref
                if opt_id.startswith("tab_destination_") or (
                    out.get("destination") in ("google_sheets", "gsheets")
                    and (out.get("destination_name") or "").strip() == ref.display_name
                ):
                    out["destination"] = "google_sheets"
                    out["destination_name"] = ref.display_name
                    if ref.sheet_name:
                        out["destination_sheet_name"] = ref.sheet_name
                    if ref.spreadsheet_id:
                        out["destination_spreadsheet_id"] = ref.spreadsheet_id
                    out["planner_destination_confirmed"] = True
                else:
                    out["source"] = "google_sheets"
                    out["source_name"] = ref.display_name
                    if ref.sheet_name:
                        out["source_sheet_name"] = ref.sheet_name
            continue

        if clar.clarification_type == "pick_flow":
            flow_id = opt_id.replace("flow_", "", 1) if opt_id.startswith("flow_") else opt_id
            if flow_id:
                out["flow_id"] = flow_id
                out["load_flow"] = True
            continue

        if clar.clarification_type == "pick_workbook_ambiguous":
            if chosen.resource_ref:
                role = _gsheets_ambiguous_workbook_role(out)
                _apply_google_sheets_workbook_ref(out, chosen.resource_ref, role=role)
                if role == "destination":
                    out["planner_workbook_picker_done"] = True
            continue

        if clar.clarification_type in ("pick_workbook", "pick_source_workbook"):
            role = (
                "source"
                if clar.clarification_type == "pick_source_workbook"
                else _workbook_pick_role(out, clar.clarification_type)
            )
            if role == "source":
                out["planner_source_workbook_picker_done"] = True
                if chosen.resource_ref:
                    _apply_google_sheets_workbook_ref(out, chosen.resource_ref, role="source")
                    if (chosen.resource_ref.connector_id or "").strip():
                        cid = str(chosen.resource_ref.connector_id).strip()
                        out["google_sheets_connection_id"] = cid
                        out["source_connector_id"] = cid
                    out["_source_endpoint_clarified"] = True
                    out["_source_connector_validated"] = True
                    out["planner_source_confirmed"] = True
                continue
            out["planner_workbook_picker_done"] = True
            if chosen.create_new:
                out["destination"] = "google_sheets"
                out["planner_create_spreadsheet"] = True
                title = (text_val or "").strip() if text_val else ""
                if title:
                    out["planner_new_spreadsheet_name"] = title
                    out["destination_name"] = title
                    out["destination_object_api_name"] = title
                    out["planner_destination_confirmed"] = True
                    out["planner_destination_entity_confirmed"] = True
                else:
                    out["destination_name"] = None
                    out["planner_destination_confirmed"] = False
                out = _arm_create_destination_from_user_choice(out)
            elif chosen.resource_ref:
                ref = chosen.resource_ref
                _apply_google_sheets_workbook_ref(out, ref, role="destination")
            ops = [str(o).lower() for o in (out.get("operations") or [])]
            intent = (out.get("intent") or out.get("mode") or "").lower()
            if intent not in ("extract",) and "extract" not in ops:
                out["intent"] = out.get("intent") or "map_data"
                out["mode"] = out.get("mode") or "map"

    raw_intent = (plan.metadata or {}).get("migration_intent")
    if raw_intent and selections:
        from services.planning.migration_intent import apply_extract_clarification_selections
        from services.planning.migration_intent_schemas import PromptMigrationIntent

        intent = PromptMigrationIntent.model_validate(raw_intent)
        patched = apply_extract_clarification_selections(
            intent, plan.clarifications, selections or []
        )
        if patched.model_dump(mode="json") != raw_intent:
            plan.metadata = dict(plan.metadata or {})
            plan.metadata["migration_intent"] = patched.model_dump(mode="json")
            out["_extract_intent_clarified"] = True
            out = _sync_migration_intent_to_classification(out, plan.metadata["migration_intent"])
            for clar in plan.clarifications:
                if clar.clarification_type == "pick_extract_fields":
                    clar.selected_option_id = clar.selected_option_id or "fields_confirmed"

    if not plan.blocking_clarifications():
        plan.status = "awaiting_approval"

    from services.planning.goal_reconciliation import reconcile_existing_destination_flags
    from services.planning.plan_scope import read_plan_scope
    from services.planning.schemas import PlanContract

    parsed_goal = read_plan_scope(out) or {}
    plan_contract = None
    raw_contract = (plan.metadata or {}).get("plan_contract")
    if raw_contract:
        try:
            plan_contract = PlanContract.model_validate(raw_contract)
        except Exception:
            plan_contract = None
    out, _, _ = reconcile_existing_destination_flags(out, parsed_goal, plan_contract)

    from services.planning.clarification_queue import merge_plan_clarification_queue
    from services.planning.op_requirements import read_plan_requirements
    from services.planning.observability import log_planner_debug
    from services.planning.resource_catalog import coerce_planning_catalog

    reqs = read_plan_requirements(out, parsed_goal=parsed_goal)
    catalog_ref = coerce_planning_catalog(resolved_catalog or catalog, out)
    plan = merge_plan_clarification_queue(
        plan,
        out,
        reqs,
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        catalog=catalog_ref,
    )
    log_planner_debug(
        "apply_clarification_selections_done",
        classification=out,
        plan=plan,
        extra={
            "selection_count": len(selections or []),
            "blocking_count": len(plan.blocking_clarifications()),
            "open_blocking_types": [
                c.clarification_type for c in plan.blocking_clarifications()
            ],
            "destination_connector_id": out.get("destination_connector_id"),
        },
    )

    from services.planning.slot_fills import (
        mirror_slot_fills_to_classification,
        sync_slot_fills_from_plan_clarifications,
    )

    _, plan = sync_slot_fills_from_plan_clarifications(plan, out)
    out = mirror_slot_fills_to_classification(out, plan)

    from services.planning.planning_checkpoint import persist_planning_checkpoint
    from services.planning.plan_scope import read_plan_scope

    out, plan = persist_planning_checkpoint(out, plan, parsed_goal=read_plan_scope(out))

    return out, plan


def build_planner_pending_payload(
    classification: Dict[str, Any],
    *,
    prompt: str,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Serializable blob stored in chat metadata and returned to the client."""
    from services.planning.schemas import sanitize_classification_for_json

    plan_raw = classification.get("execution_plan") or {}
    safe_plan = (
        sanitize_classification_for_json(plan_raw)
        if isinstance(plan_raw, dict)
        else plan_raw
    )
    blocking_clarifications: List[Dict[str, Any]] = list(classification.get("clarifications") or [])
    if isinstance(plan_raw, dict):
        try:
            ep = ExecutionPlan.model_validate(plan_raw)
            blocking_clarifications = [
                c.model_dump(mode="json") for c in ep.blocking_clarifications()
            ]
        except Exception:
            pass
    safe_classification = sanitize_classification_for_json(classification)
    plan_model = None
    if isinstance(plan_raw, dict) and plan_raw.get("plan_id"):
        try:
            plan_model = ExecutionPlan.model_validate(plan_raw)
        except Exception:
            plan_model = None
    if plan_model is not None:
        from services.planning.planning_checkpoint import persist_planning_checkpoint
        from services.planning.plan_scope import read_plan_scope

        safe_classification, plan_model = persist_planning_checkpoint(
            safe_classification,
            plan_model,
            parsed_goal=read_plan_scope(safe_classification),
        )
        safe_plan = sanitize_classification_for_json(plan_model.model_dump(mode="json"))
    pending: Dict[str, Any] = {
        "conversation_id": str(conversation_id) if conversation_id else None,
        "original_prompt": prompt,
        "classification": safe_classification,
        "execution_plan": safe_plan,
        "plan_id": (plan_raw.get("plan_id") if isinstance(plan_raw, dict) else None)
        or classification.get("plan_id"),
        "clarifications": blocking_clarifications,
    }
    plan_meta = plan_raw.get("metadata") if isinstance(plan_raw, dict) else {}
    if isinstance(plan_meta, dict):
        if plan_meta.get("continuation"):
            pending["continuation"] = True
        if plan_meta.get("job_id"):
            pending["job_id"] = plan_meta.get("job_id")
        if plan_meta.get("session_snapshot"):
            pending["session_snapshot"] = plan_meta.get("session_snapshot")
    intent_state = safe_classification.get("intent_state")
    if isinstance(intent_state, dict) and intent_state:
        pending["intent_state"] = intent_state
    return pending
