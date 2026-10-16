"""Unified entity resolution for planning (catalog-first; semantics as hints only)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.labeling.catalog_helpers import fetch_semantics_map
from services.planning.entity_scoring import (
    MIN_CATALOG_MATCH,
    rank_gsheets_catalog,
    rank_salesforce_catalog,
    rank_sql_catalog,
)
from services.planning.label_match import (
    match_entity_in_prompt,
    normalize_phrase,
)
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import (
    Clarification,
    ClarificationOption,
    ExecutionPlan,
    PlanContract,
    PlanEntityRef,
    normalize_connector_type,
)

_MIN_ENTITY_CONFIDENCE = 0.72


def _entity_summaries(semantics_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"entity_key": key, "semantics": sem}
        for key, sem in (semantics_map or {}).items()
    ]


from services.planning.entity_hints import (
    assemble_ranking_hints,
    classification_entity_hints as _classification_hints,
    goal_destination_user_hints as _goal_destination_hints,
    goal_entity_hints as _goal_entity_hints,
    goal_source_user_hints as _goal_source_hints,
    migration_intent_hints,
)


def _side_scoped_prompt(
    prompt: str,
    *,
    role: str,
    parsed_goal: Optional[Dict[str, Any]],
    classification: Dict[str, Any],
    migration_intent: Optional[Dict[str, Any]] = None,
) -> str:
    """Use side-specific goal phrases for catalog scoring on cross-connector prompts."""
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    cross = bool(intent.get("cross_system"))
    if not cross:
        src = normalize_connector_type(classification.get("source") or "")
        dest = normalize_connector_type(classification.get("destination") or "")
        cross = bool(src and dest and src != dest)
    if not cross:
        return prompt
    goal = parsed_goal or {}
    scoped = (
        (goal.get("source_phrase") or "").strip()
        if role == "source"
        else (goal.get("destination_phrase") or "").strip()
    )
    return scoped or prompt


def _rank_salesforce_catalog(
    prompt: str,
    catalog: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Tuple[str, str, float]]:
    return rank_salesforce_catalog(
        prompt,
        catalog,
        hints,
        semantics_map=semantics_map,
    )


def _match_salesforce_catalog(
    prompt: str,
    catalog: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Tuple[str, str, float]]:
    ranked = _rank_salesforce_catalog(
        prompt, catalog, hints, semantics_map=semantics_map
    )
    if not ranked:
        return None
    api, label, score = ranked[0]
    if score < MIN_CATALOG_MATCH:
        return None
    return api, label, min(0.95, score / 40.0)


def _rank_sql_catalog(
    prompt: str,
    tables: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Tuple[str, float]]:
    return rank_sql_catalog(
        prompt,
        tables,
        hints,
        semantics_map=semantics_map,
    )


def _match_sql_catalog(
    prompt: str,
    tables: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Tuple[str, float]]:
    ranked = _rank_sql_catalog(
        prompt, tables, hints, semantics_map=semantics_map
    )
    if not ranked:
        return None
    name, score = ranked[0]
    if score < MIN_CATALOG_MATCH:
        return None
    return name, min(0.9, score / 35.0)


def _rank_gsheets_catalog(
    prompt: str,
    sheets: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Tuple[str, str, str, float]]:
    return rank_gsheets_catalog(
        prompt,
        sheets,
        hints,
        semantics_map=semantics_map,
    )


def _match_gsheets_catalog(
    prompt: str,
    sheets: List[Dict[str, Any]],
    hints: List[str],
    *,
    semantics_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Tuple[str, str, str, float]]:
    ranked = _rank_gsheets_catalog(
        prompt, sheets, hints, semantics_map=semantics_map
    )
    if not ranked:
        return None
    entity_key, tab, label, score = ranked[0]
    if score < MIN_CATALOG_MATCH:
        return None
    return entity_key, tab, label, min(0.9, score / 30.0)


def _sf_ref(
    api: str,
    catalog_label: str,
    *,
    resolution_source: str,
    confidence: float,
) -> PlanEntityRef:
    label = (catalog_label or api).strip()
    return PlanEntityRef(
        connector_type="salesforce",
        entity_key=api,
        catalog_label=label,
        entity_label=label,
        resolution_source=resolution_source,
        confidence=confidence,
    )


async def _resolve_salesforce_entity(
    *,
    user_id: str,
    org_id: Optional[str],
    prompt: str,
    hints: List[str],
    semantics_map: Dict[str, Dict[str, Any]],
    grounded_key: Optional[str] = None,
    grounded_label: Optional[str] = None,
    classification: Optional[Dict[str, Any]] = None,
) -> Optional[PlanEntityRef]:
    from services.mapping_plan.catalog_target_resolver import _load_salesforce_org_objects
    from utils.salesforce_mapping_target import lookup_salesforce_object_in_org

    if grounded_key:
        meta = await lookup_salesforce_object_in_org(user_id, org_id, grounded_key)
        if meta:
            api = str(meta.get("object_api_name") or grounded_key)
            return _sf_ref(
                api,
                grounded_label or str(meta.get("label") or api),
                resolution_source="plan_pinned",
                confidence=0.95,
            )

    catalog = await _load_salesforce_org_objects(user_id, org_id)

    for hint in hints:
        meta = await lookup_salesforce_object_in_org(user_id, org_id, hint)
        if meta:
            api = str(meta.get("object_api_name") or hint)
            return _sf_ref(
                api,
                str(meta.get("label") or api),
                resolution_source="user_explicit",
                confidence=0.95,
            )

    matched = _match_salesforce_catalog(
        prompt,
        catalog,
        hints,
        semantics_map=semantics_map,
    )
    if matched:
        api, label, conf = matched
        from services.planning.entity_catalog_policy import (
            prefer_user_data_entity_from_catalog,
            should_replace_metadata_driving_entity,
        )

        if should_replace_metadata_driving_entity(
            prompt=prompt, connector_type="salesforce", entity_key=api
        ):
            preferred = prefer_user_data_entity_from_catalog(
                prompt=prompt,
                connector_type="salesforce",
                catalog=[
                    {
                        "api_name": (row.get("object_api_name") or "").strip(),
                        "label": (row.get("label") or row.get("object_api_name") or "").strip(),
                    }
                    for row in catalog
                    if (row.get("object_api_name") or "").strip()
                ],
                current_key=api,
            )
            if preferred:
                api, label = preferred[0], preferred[1]
        return _sf_ref(api, label, resolution_source="catalog_label", confidence=conf)

    return None


async def _resolve_sql_entity(
    *,
    user_id: str,
    connector_id: Optional[str],
    prompt: str,
    hints: List[str],
    semantics_map: Dict[str, Dict[str, Any]],
    grounded_key: Optional[str] = None,
    grounded_label: Optional[str] = None,
    classification: Optional[Dict[str, Any]] = None,
) -> Optional[PlanEntityRef]:
    from services.mapping_plan.catalog_target_resolver import _load_sql_connector_tables

    tables = await _load_sql_connector_tables(user_id, connector_id)
    table_names = {(row.get("table_name") or "").strip() for row in tables}

    if grounded_key and grounded_key in table_names:
        return PlanEntityRef(
            connector_type="sql",
            entity_key=grounded_key,
            catalog_label=grounded_label or grounded_key,
            entity_label=grounded_label or grounded_key,
            resolution_source="plan_pinned",
            confidence=0.95,
        )

    for hint in hints:
        for row in tables:
            if (row.get("table_name") or "").strip().lower() == hint.lower():
                name = row["table_name"]
                return PlanEntityRef(
                    connector_type="sql",
                    entity_key=name,
                    catalog_label=name,
                    entity_label=name,
                    resolution_source="user_explicit",
                    confidence=0.95,
                )

    matched = _match_sql_catalog(prompt, tables, hints, semantics_map=semantics_map)
    if matched:
        name, conf = matched
        from services.planning.entity_catalog_policy import (
            prefer_user_data_entity_from_catalog,
            should_replace_metadata_driving_entity,
        )

        if should_replace_metadata_driving_entity(
            prompt=prompt, connector_type="sql", entity_key=name
        ):
            preferred = prefer_user_data_entity_from_catalog(
                prompt=prompt,
                connector_type="sql",
                catalog=[
                    {"api_name": (row.get("table_name") or "").strip(), "label": (row.get("table_name") or "").strip()}
                    for row in tables
                    if (row.get("table_name") or "").strip()
                ],
                current_key=name,
            )
            if preferred:
                name = preferred[0]
        return PlanEntityRef(
            connector_type="sql",
            entity_key=name,
            catalog_label=name,
            entity_label=name,
            resolution_source="catalog_label",
            confidence=conf,
        )

    return None


async def _resolve_gsheets_entity(
    *,
    user_id: str,
    spreadsheet_name: Optional[str],
    spreadsheet_id: Optional[str],
    prompt: str,
    hints: List[str],
    semantics_map: Dict[str, Dict[str, Any]],
    grounded_key: Optional[str] = None,
    grounded_label: Optional[str] = None,
    classification: Optional[Dict[str, Any]] = None,
) -> Optional[PlanEntityRef]:
    from services.mapping_plan.catalog_target_resolver import _load_google_sheet_tabs

    sheets = await _load_google_sheet_tabs(
        user_id,
        spreadsheet_name=spreadsheet_name,
        spreadsheet_id=spreadsheet_id,
    )
    sheet_keys = {
        f"{(row.get('spreadsheet_id') or '').strip()}::{(row.get('sheet_name') or '').strip()}"
        for row in sheets
        if (row.get("spreadsheet_id") or "").strip() and (row.get("sheet_name") or "").strip()
    }

    if grounded_key and grounded_key in sheet_keys:
        tab = grounded_key.split("::", 1)[-1] if "::" in grounded_key else grounded_key
        sid = grounded_key.split("::", 1)[0] if "::" in grounded_key else spreadsheet_id
        return PlanEntityRef(
            connector_type="google_sheets",
            entity_key=grounded_key,
            catalog_label=grounded_label or tab,
            entity_label=grounded_label or tab,
            sheet_name=tab,
            spreadsheet_id=sid,
            resolution_source="plan_pinned",
            confidence=0.95,
        )

    matched = _match_gsheets_catalog(prompt, sheets, hints, semantics_map=semantics_map)
    if matched:
        entity_key, tab, label, conf = matched
        sid = entity_key.split("::", 1)[0] if "::" in entity_key else spreadsheet_id
        return PlanEntityRef(
            connector_type="google_sheets",
            entity_key=entity_key,
            catalog_label=tab,
            entity_label=tab,
            sheet_name=tab,
            spreadsheet_id=sid,
            resolution_source="catalog_label",
            confidence=conf,
        )

    return None


def _connector_id_for_role(classification: Dict[str, Any], role: str) -> Optional[str]:
    if role == "source":
        return (classification.get("source_connector_id") or "").strip() or None
    return (
        (classification.get("destination_connector_id") or "").strip()
        or (classification.get("destination_spreadsheet_id") or "").strip()
        or None
    )


async def _resolve_side_entity(
    *,
    user_id: str,
    classification: Dict[str, Any],
    role: str,
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]],
    migration_intent: Optional[Dict[str, Any]] = None,
) -> PlanEntityRef:
    from services.planning.binding.guards import binding_side_ref, entity_side_resolved_by_binding

    pinned = binding_side_ref(classification, role)
    if entity_side_resolved_by_binding(pinned):
        return pinned.model_copy()

    connector_type = normalize_connector_type(
        classification.get("source" if role == "source" else "destination") or "unknown"
    )
    display_name = (
        classification.get("source_name") if role == "source" else classification.get("destination_name")
    )
    connector_id = _connector_id_for_role(classification, role)
    base = PlanEntityRef(
        connector_type=connector_type,
        connector_id=connector_id,
        display_name=(display_name or "").strip() or None,
        resolution_source="pending",
        confidence=0.0,
    )
    if connector_type not in ("salesforce", "sql", "google_sheets"):
        return base

    intent = migration_intent if isinstance(migration_intent, dict) else {}
    source_key = (intent.get("driving_entity_key") or "").strip() if role == "destination" else None
    source_label = (intent.get("driving_entity_label") or "").strip() if role == "destination" else None
    unique_hints = assemble_ranking_hints(
        role=role,
        classification=classification,
        parsed_goal=parsed_goal,
        connector_type=connector_type,
        migration_intent=intent,
        source_entity_key=source_key,
        source_entity_label=source_label,
    )

    scoped_prompt = _side_scoped_prompt(
        prompt,
        role=role,
        parsed_goal=parsed_goal,
        classification=classification,
        migration_intent=intent,
    )

    grounded_key = ""
    grounded_label = ""
    from services.planning.entity_pins import pinned_entity_key

    if role == "destination" and classification.get("planner_destination_entity_confirmed"):
        pinned = pinned_entity_key(
            classification, role="destination", connector_type=connector_type
        )
        if pinned:
            grounded_key = pinned
            grounded_label = pinned
    elif role == "source" and classification.get("planner_source_entity_confirmed"):
        pinned = pinned_entity_key(
            classification, role="source", connector_type=connector_type
        )
        if pinned:
            grounded_key = pinned
            grounded_label = pinned
    if role == "source" and not grounded_key:
        grounded_key = (intent.get("driving_entity_key") or "").strip()
        grounded_label = (intent.get("driving_entity_label") or "").strip()
    elif role == "destination" and not grounded_key:
        grounded_key = (intent.get("destination_entity_key") or "").strip()
        grounded_label = (intent.get("destination_entity_label") or "").strip()

    semantics_map: Dict[str, Dict[str, Any]] = {}
    if connector_id:
        semantics_map = await fetch_semantics_map(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=str(connector_id),
        )

    resolved: Optional[PlanEntityRef] = None
    if connector_type == "salesforce":
        from utils.destination_connector_resolve import resolve_salesforce_token_record

        tr = await resolve_salesforce_token_record(
            user_id,
            destination_name=display_name,
            token_id=connector_id,
        )
        org_id = tr.get("org_id") if tr else None
        if tr and tr.get("id"):
            base.connector_id = str(tr["id"])
        resolved = await _resolve_salesforce_entity(
            user_id=str(user_id),
            org_id=org_id,
            prompt=scoped_prompt,
            hints=unique_hints,
            semantics_map=semantics_map,
            grounded_key=grounded_key or None,
            grounded_label=grounded_label or None,
            classification=classification,
        )
    elif connector_type == "sql":
        from utils.destination_connector_resolve import resolve_sql_connector_record

        tr = await resolve_sql_connector_record(user_id, destination_name=display_name)
        cid = str(tr.get("id")) if tr and tr.get("id") else connector_id
        if cid:
            base.connector_id = cid
        resolved = await _resolve_sql_entity(
            user_id=str(user_id),
            connector_id=cid,
            prompt=scoped_prompt,
            hints=unique_hints,
            semantics_map=semantics_map,
            grounded_key=grounded_key or None,
            grounded_label=grounded_label or None,
            classification=classification,
        )
    elif connector_type == "google_sheets":
        spreadsheet_id = (
            classification.get("destination_spreadsheet_id")
            if role == "destination"
            else classification.get("source_spreadsheet_id")
        )
        resolved = await _resolve_gsheets_entity(
            user_id=str(user_id),
            spreadsheet_name=display_name,
            spreadsheet_id=(spreadsheet_id or "").strip() or None,
            prompt=scoped_prompt,
            hints=unique_hints,
            semantics_map=semantics_map,
            grounded_key=grounded_key or None,
            grounded_label=grounded_label or None,
            classification=classification,
        )

    if not resolved:
        return base

    pinned_after = binding_side_ref(classification, role)
    if entity_side_resolved_by_binding(pinned_after):
        return pinned_after.model_copy()

    resolved.connector_type = connector_type
    resolved.connector_id = resolved.connector_id or base.connector_id
    resolved.display_name = base.display_name
    if unique_hints and resolved.resolution_source == "catalog_label":
        resolved.resolution_source = "goal_entity"
    if resolved.confidence < _MIN_ENTITY_CONFIDENCE:
        resolved.resolution_source = "pending"
    return resolved


def _entities_share_round_trip_source(
    source: PlanEntityRef,
    dest: PlanEntityRef,
) -> bool:
    """True when source and destination resolve to the same catalog entity."""
    if not source.is_resolved() or not dest.is_resolved():
        return False
    if source.connector_type != dest.connector_type:
        return False
    return (source.entity_key or "").lower() == (dest.entity_key or "").lower()


def _needs_entity_for_role(
    classification: Dict[str, Any],
    role: str,
    parsed_goal: Optional[Dict[str, Any]],
) -> bool:
    from services.planning.op_requirements import (
        DESTINATION_ENTITY,
        SOURCE_ENTITY,
        TARGET_ENTITY,
        read_plan_requirements,
    )

    ct = normalize_connector_type(
        classification.get("source" if role == "source" else "destination") or "unknown"
    )
    if ct not in ("salesforce", "sql", "google_sheets"):
        return False

    reqs = read_plan_requirements(classification, parsed_goal=parsed_goal)
    slots = {s.slot for s in reqs.all_slots()}
    if role == "source":
        return SOURCE_ENTITY in slots
    return DESTINATION_ENTITY in slots or TARGET_ENTITY in slots


def _migration_intent_hints(
    plan: ExecutionPlan,
    role: str,
) -> List[str]:
    raw = (plan.metadata or {}).get("migration_intent") or {}
    return migration_intent_hints(raw, role)


def _requires_destination_entity_confirmation(
    classification: Dict[str, Any],
    *,
    role: str,
) -> bool:
    if role != "destination":
        return False
    from services.planning.op_requirements import DESTINATION_ENTITY, read_plan_requirements

    reqs = read_plan_requirements(classification)
    if DESTINATION_ENTITY not in {s.slot for s in reqs.all_slots()}:
        return False
    return not classification.get("planner_destination_entity_confirmed")


async def build_entity_clarifications(
    contract: PlanContract,
    classification: Dict[str, Any],
    *,
    user_id: str,
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]],
    roles: Optional[Tuple[str, ...]] = None,
    plan_migration_intent: Optional[Any] = None,
) -> List[Clarification]:
    """Build pick-entity clarifications when catalog entity is not confidently pinned."""
    existing_types: set[str] = set()
    out: List[Clarification] = []
    allowed_roles = set(roles) if roles else None
    migration_intent_ref = plan_migration_intent

    async def _semantics_for_connector(
        connector_type: str,
        connector_id: Optional[str],
    ) -> Dict[str, Dict[str, Any]]:
        ct = normalize_connector_type(connector_type)
        if not connector_id or ct not in ("salesforce", "sql", "google_sheets"):
            return {}
        return await fetch_semantics_map(
            user_id=str(user_id),
            connector_type=ct,
            connector_instance_id=str(connector_id),
        )

    async def _sf_options(role: str) -> List[ClarificationOption]:
        from services.mapping_plan.catalog_target_resolver import _load_salesforce_org_objects
        from utils.destination_connector_resolve import resolve_salesforce_token_record

        name = classification.get("source_name" if role == "source" else "destination_name")
        side_ref = contract.source if role == "source" else contract.destination
        tr = await resolve_salesforce_token_record(user_id, destination_name=name)
        org_id = tr.get("org_id") if tr else None
        connector_id = (
            (tr.get("token_id") or tr.get("id") or side_ref.connector_id)
            if tr
            else side_ref.connector_id
        )
        catalog = await _load_salesforce_org_objects(user_id, org_id)
        src = contract.source if role == "destination" else None
        hints = assemble_ranking_hints(
            role=role,
            classification=classification,
            parsed_goal=parsed_goal,
            connector_type="salesforce",
            plan_migration_intent=migration_intent_ref,
            source_entity_key=(src.entity_key if src and src.is_resolved() else None),
            source_entity_label=(src.entity_label if src and src.is_resolved() else None),
            source_catalog_label=(src.catalog_label if src and src.is_resolved() else None),
        )
        semantics_map = await _semantics_for_connector("salesforce", str(connector_id) if connector_id else None)
        ranked = _rank_salesforce_catalog(prompt, catalog, hints, semantics_map=semantics_map)
        options: List[ClarificationOption] = []
        for api, label, _score in ranked[:8]:
            options.append(
                ClarificationOption(
                    id=f"{role}:{api}",
                    label=f"{api}" + (f" ({label})" if label and label.lower() != api.lower() else ""),
                )
            )
        return options

    for role, side in (("source", contract.source), ("destination", contract.destination)):
        if allowed_roles is not None and role not in allowed_roles:
            continue
        if not _needs_entity_for_role(classification, role, parsed_goal):
            continue
        force_dest_confirm = _requires_destination_entity_confirmation(classification, role=role)
        if (
            not force_dest_confirm
            and side.is_resolved()
            and side.confidence >= _MIN_ENTITY_CONFIDENCE
        ):
            continue
        ctype = f"pick_{role}_entity"
        if ctype in existing_types:
            continue
        conn_name = (side.display_name or classification.get(f"{role}_name") or "").strip()
        ct = normalize_connector_type(side.connector_type)
        if ct == "salesforce":
            options = await _sf_options(role)
            noun = "Salesforce object"
        elif ct == "sql":
            from services.mapping_plan.catalog_target_resolver import _load_sql_connector_tables
            from utils.destination_connector_resolve import resolve_sql_connector_record

            tr = await resolve_sql_connector_record(user_id, destination_name=conn_name)
            cid = str(tr.get("id")) if tr and tr.get("id") else side.connector_id
            tables = await _load_sql_connector_tables(user_id, cid)
            src = contract.source if role == "destination" else None
            hints = assemble_ranking_hints(
                role=role,
                classification=classification,
                parsed_goal=parsed_goal,
                connector_type="sql",
                plan_migration_intent=migration_intent_ref,
                source_entity_key=(src.entity_key if src and src.is_resolved() else None),
                source_entity_label=(src.entity_label if src and src.is_resolved() else None),
                source_catalog_label=(src.catalog_label if src and src.is_resolved() else None),
            )
            semantics_map = await _semantics_for_connector("sql", cid)
            ranked = _rank_sql_catalog(prompt, tables, hints, semantics_map=semantics_map)
            options = [
                ClarificationOption(id=f"{role}:{name}", label=name)
                for name, _ in ranked[:8]
            ]
            noun = "table"
        elif ct == "google_sheets":
            from services.mapping_plan.catalog_target_resolver import _load_google_sheet_tabs
            from services.planning.create_metadata import _goal_has_create
            from services.planning.google_sheets_destination import (
                gsheets_workbook_destination_resolved,
            )
            from services.planning.google_sheets_source import gsheets_workbook_source_resolved
            from services.planning.plan_scope import read_plan_scope
            from services.planning.resource_catalog import ResourceCatalog, coerce_planning_catalog

            goal = dict(parsed_goal or read_plan_scope(classification) or {})
            plan_catalog: Optional[ResourceCatalog] = coerce_planning_catalog(
                classification.get("_planning_catalog"), classification
            )
            if role == "destination":
                if _goal_has_create(goal) or classification.get("planner_create_spreadsheet"):
                    continue
                if plan_catalog is not None and not gsheets_workbook_destination_resolved(
                    classification, plan_catalog
                ):
                    continue
                spreadsheet_name = (classification.get("destination_name") or "").strip() or None
                spreadsheet_id = (classification.get("destination_spreadsheet_id") or "").strip() or None
            else:
                if plan_catalog is not None and not gsheets_workbook_source_resolved(
                    classification, plan_catalog
                ):
                    continue
                spreadsheet_name = (classification.get("source_name") or "").strip() or None
                spreadsheet_id = (classification.get("source_spreadsheet_id") or "").strip() or None

            sheets = await _load_google_sheet_tabs(
                user_id,
                spreadsheet_name=spreadsheet_name,
                spreadsheet_id=spreadsheet_id,
            )
            hints = assemble_ranking_hints(
                role=role,
                classification=classification,
                parsed_goal=parsed_goal,
                connector_type="google_sheets",
                plan_migration_intent=migration_intent_ref,
                source_entity_key=(
                    contract.source.entity_key if role == "destination" and contract.source.is_resolved() else None
                ),
                source_entity_label=(
                    contract.source.entity_label if role == "destination" and contract.source.is_resolved() else None
                ),
                source_catalog_label=(
                    contract.source.catalog_label if role == "destination" and contract.source.is_resolved() else None
                ),
            )
            gs_connector_id = side.connector_id or classification.get(
                "destination_connector_id" if role == "destination" else "source_connector_id"
            )
            semantics_map = await _semantics_for_connector("google_sheets", gs_connector_id)
            ranked = _rank_gsheets_catalog(prompt, sheets, hints, semantics_map=semantics_map)
            options = [
                ClarificationOption(
                    id=f"{role}:{entity_key}",
                    label=_label or tab,
                )
                for entity_key, tab, _label, _ in ranked[:8]
            ]
            noun = "sheet tab"
        else:
            continue

        if not options:
            if role == "source" and not side.is_resolved():
                if conn_name:
                    table_prompt = (
                        f"We need the exact {noun} to extract from in **{conn_name}**. "
                        "Reply in chat with the table or object name (e.g. users, contacts), "
                        "or sync connector metadata and resume."
                    )
                else:
                    table_prompt = (
                        f"We need the exact {noun} to extract from your source connection. "
                        "Reply in chat with the table or object name (e.g. users, contacts), "
                        "or sync connector metadata and resume."
                    )
                out.append(
                    Clarification(
                        id=str(uuid.uuid4()),
                        clarification_type="pick_source_entity",
                        prompt=table_prompt,
                        options=[],
                        required=True,
                    )
                )
                existing_types.add("pick_source_entity")
            elif role == "destination":
                if conn_name:
                    dest_fallback = (
                        f"We need the exact {noun} to receive data in **{conn_name}**. "
                        "Reply in chat with the tab name, or sync connector metadata and resume."
                    )
                else:
                    dest_fallback = (
                        f"We need the exact {noun} for the destination. "
                        "Reply in chat with the tab name, or sync connector metadata and resume."
                    )
                out.append(
                    Clarification(
                        id=str(uuid.uuid4()),
                        clarification_type="pick_destination_entity",
                        prompt=dest_fallback,
                        options=[],
                        required=True,
                    )
                )
                existing_types.add("pick_destination_entity")
            continue
        dest_prompt = (
            f"Which {noun} should receive this data in **{conn_name}**? "
            "Confirm the destination before we show the execution plan."
        )
        src_prompt = (
            f"Which {noun} should I use for the {role} "
            f"({conn_name})? Pick the exact catalog object/table/tab to extract or migrate."
        )
        out.append(
            Clarification(
                id=str(uuid.uuid4()),
                clarification_type=ctype,
                prompt=dest_prompt if role == "destination" else src_prompt,
                options=options,
                required=True,
            )
        )
        existing_types.add(ctype)
    return out


async def resolve_plan_entities(
    classification: Dict[str, Any],
    *,
    user_id: str,
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]] = None,
    catalog: Optional[ResourceCatalog] = None,
    migration_intent: Optional[Any] = None,
    existing_contract: Optional[PlanContract] = None,
    resolve_sides: Optional[tuple[str, ...]] = None,
) -> PlanContract:
    """Resolve source and/or destination entities once at plan time."""
    del catalog

    sides = resolve_sides or ("source", "destination")

    intent_dump: Optional[Dict[str, Any]] = None
    if migration_intent is not None:
        if hasattr(migration_intent, "model_dump"):
            intent_dump = migration_intent.model_dump(mode="json")
        elif isinstance(migration_intent, dict):
            intent_dump = migration_intent

    if existing_contract and "source" not in sides:
        source = existing_contract.source
    else:
        source = await _resolve_side_entity(
            user_id=str(user_id),
            classification=classification,
            role="source",
            prompt=prompt,
            parsed_goal=parsed_goal,
            migration_intent=intent_dump,
        )
    if existing_contract and "destination" not in sides:
        dest = existing_contract.destination
    else:
        dest = await _resolve_side_entity(
            user_id=str(user_id),
            classification=classification,
            role="destination",
            prompt=prompt,
            parsed_goal=parsed_goal,
            migration_intent=intent_dump,
        )

    src_type = normalize_connector_type(classification.get("source") or "")
    dest_type = normalize_connector_type(classification.get("destination") or "")
    same_endpoint = bool(
        (parsed_goal or {}).get("destination_same_as_source")
        or classification.get("destination_same_as_source")
    )
    if (
        same_endpoint
        and not source.is_resolved()
        and dest.is_resolved()
        and src_type == dest_type
        and src_type in ("salesforce", "sql", "google_sheets")
    ):
        updates: Dict[str, Any] = {
            "display_name": source.display_name or dest.display_name,
            "connector_id": source.connector_id or dest.connector_id,
            "resolution_source": "destination_same_as_source",
        }
        if src_type == "google_sheets":
            updates["sheet_name"] = source.sheet_name or dest.sheet_name
            updates["spreadsheet_id"] = source.spreadsheet_id or dest.spreadsheet_id
        source = dest.model_copy(update=updates)

    return PlanContract(
        source=source,
        destination=dest,
        mapping_target=dest.model_copy() if dest.is_resolved() else None,
        entities_from_goal=_goal_entity_hints(parsed_goal),
        migration_intent=intent_dump,
    )


def apply_plan_contract_to_classification(
    classification: Dict[str, Any],
    contract: PlanContract,
) -> Dict[str, Any]:
    """Merge plan contract into classification and legacy hint fields."""
    out = dict(classification or {})
    out["plan_contract"] = contract.model_dump(mode="json")

    src = contract.source
    dest = contract.destination

    if src.is_resolved():
        ct = normalize_connector_type(src.connector_type)
        if ct == "salesforce":
            out["source_object_api_name"] = src.entity_key
        elif ct == "sql":
            out["source_table_name"] = src.entity_key
        elif ct == "google_sheets":
            out["source_sheet_name"] = src.sheet_name or (
                src.entity_key.split("::", 1)[-1] if src.entity_key and "::" in src.entity_key else None
            )
            if src.spreadsheet_id:
                out["source_spreadsheet_id"] = src.spreadsheet_id
            if src.display_name:
                out["source_name"] = src.display_name

    ct_dest = normalize_connector_type(dest.connector_type or out.get("destination") or "")
    from services.planning.continuation_policy import (
        plan_contract_destination_stale_vs_classification,
        sync_plan_contract_destination_from_classification,
    )
    from services.planning.entity_pins import (
        pinned_entity_key,
        plan_entity_ref_from_pin,
        write_pinned_entity_key,
    )

    stale_job_dest = plan_contract_destination_stale_vs_classification(out, contract)

    if out.get("planner_destination_entity_confirmed") and not stale_job_dest:
        pin = pinned_entity_key(out, role="destination", connector_type=ct_dest)
        if pin:
            out = write_pinned_entity_key(
                out, role="destination", connector_type=ct_dest, entity_key=pin
            )
            if not dest.is_resolved() or (dest.entity_key or "").lower() != pin.lower():
                dest = plan_entity_ref_from_pin(
                    out, role="destination", entity_key=pin, label=pin
                )
                contract = contract.model_copy(update={"destination": dest})
                out["plan_contract"] = contract.model_dump(mode="json")
    elif dest.is_resolved() and not stale_job_dest:
        if ct_dest == "salesforce":
            out["destination_object_api_name"] = dest.entity_key
            out["object_api_name"] = dest.entity_key
        elif ct_dest == "sql":
            out["destination_table_name"] = dest.entity_key
        elif ct_dest == "google_sheets":
            out["destination_sheet_name"] = dest.sheet_name or (
                dest.entity_key.split("::", 1)[-1]
                if dest.entity_key and "::" in dest.entity_key
                else None
            )

    if contract.migration_intent:
        out["migration_intent"] = contract.migration_intent

    round_trip = bool(
        out.get("destination_same_as_source")
        or _entities_share_round_trip_source(src, dest)
    )
    if round_trip:
        out["destination_same_as_source"] = True

    if stale_job_dest:
        return sync_plan_contract_destination_from_classification(
            out, resolution_source="llm_bind", confidence=0.9
        )

    if dest.is_resolved():
        ct = normalize_connector_type(dest.connector_type or out.get("destination") or "")
        if ct:
            out["destination"] = ct
        if dest.display_name:
            out["destination_name"] = dest.display_name
        if dest.connector_id:
            out["destination_connector_id"] = dest.connector_id
        if ct == "google_sheets":
            sheet = dest.sheet_name or (
                dest.entity_key.split("::", 1)[-1]
                if dest.entity_key and "::" in dest.entity_key
                else None
            )
            if sheet:
                out["destination_sheet_name"] = sheet
            if dest.spreadsheet_id:
                out["destination_spreadsheet_id"] = dest.spreadsheet_id

    if round_trip and src.is_resolved():
        ct_src = normalize_connector_type(src.connector_type or out.get("source") or "")
        if ct_src:
            out["source"] = ct_src
        if src.display_name:
            out["source_name"] = src.display_name
        if src.connector_id:
            out["source_connector_id"] = src.connector_id

    return out
