"""Validate LLM bind outputs against catalog; build PlanContract + migration_intent."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.planning.binding.contract import (
    DestinationBindLLMOutput,
    EntityCandidate,
    PlanBindingResult,
    SourceBindLLMOutput,
)
from services.planning.binding.observability import log_binding
from services.planning.migration_intent_schemas import PromptMigrationIntent
from services.planning.schemas import PlanContract, PlanEntityRef, normalize_connector_type

MIN_BIND_CONFIDENCE = 0.55


def _candidate_map(candidates: List[EntityCandidate]) -> Dict[str, EntityCandidate]:
    return {c.entity_key: c for c in candidates if c.entity_key}


def _allowed_field_keys(candidate: Optional[EntityCandidate]) -> Dict[str, str]:
    """normalized key -> canonical key"""
    if not candidate:
        return {}
    out: Dict[str, str] = {}
    for f in candidate.fields:
        k = (f.key or "").strip()
        lbl = (f.label or k).strip()
        if k:
            out[k.lower()] = k
        if lbl:
            out[lbl.lower()] = k or lbl
    return out


def _map_field_keys(
    raw_keys: List[str],
    candidate: Optional[EntityCandidate],
) -> Tuple[List[str], List[str]]:
    from services.planning.extract_field_clarification import _match_hint_to_catalog_label

    if not candidate:
        return [], list(raw_keys)
    allowed = _allowed_field_keys(candidate)
    label_to_key = {
        (f.label or "").strip().lower(): (f.key or "").strip()
        for f in candidate.fields
        if f.label and f.key
    }
    labels = [f.label for f in candidate.fields if f.label]
    matched: List[str] = []
    unmatched: List[str] = []
    for hint in raw_keys:
        low = hint.lower().strip()
        if low in allowed:
            canon = allowed[low]
            if canon not in matched:
                matched.append(canon)
            continue
        hit_label = _match_hint_to_catalog_label(hint, labels)
        if hit_label:
            canon = label_to_key.get(hit_label.lower()) or allowed.get(hit_label.lower())
            if canon and canon not in matched:
                matched.append(canon)
            elif not canon:
                unmatched.append(hint)
        else:
            unmatched.append(hint)
    return matched, unmatched


def _entity_ref_from_bind(
    *,
    connector_type: str,
    classification: Dict[str, Any],
    role: str,
    entity_key: str,
    label: str,
    confidence: float,
) -> PlanEntityRef:
    ct = normalize_connector_type(connector_type)
    name_key = "source_name" if role == "source" else "destination_name"
    id_key = "source_connector_id" if role == "source" else "destination_connector_id"
    display = (classification.get(name_key) or "").strip() or None
    cid = (classification.get(id_key) or "").strip() or None
    sheet_name = None
    spreadsheet_id = None
    if ct == "google_sheets" and "::" in entity_key:
        spreadsheet_id, sheet_name = entity_key.split("::", 1)
    return PlanEntityRef(
        connector_type=ct,
        connector_id=cid,
        display_name=display,
        entity_key=entity_key,
        catalog_label=label,
        entity_label=label,
        sheet_name=sheet_name,
        spreadsheet_id=spreadsheet_id,
        resolution_source="llm_bind",
        confidence=confidence,
    )


async def validate_plan_binding(
    *,
    classification: Dict[str, Any],
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]],
    source_bind: SourceBindLLMOutput,
    dest_bind: Optional[DestinationBindLLMOutput],
    source_candidates: List[EntityCandidate],
    dest_candidates: List[EntityCandidate],
    user_id: str,
    source_pinned: Optional[PlanEntityRef] = None,
    dest_pinned: Optional[PlanEntityRef] = None,
    catalog_ctx: Optional[Dict[str, Any]] = None,
    source_required: bool = True,
) -> PlanBindingResult:
    errors: List[str] = []
    src_map = _candidate_map(source_candidates)
    dest_map = _candidate_map(dest_candidates)

    src_ct = normalize_connector_type(classification.get("source") or "")
    dest_ct = normalize_connector_type(classification.get("destination") or "")

    source_ref = PlanEntityRef(connector_type=src_ct)
    dest_ref = PlanEntityRef(connector_type=dest_ct)

    field_clarification = False
    source_entity_gap: Optional[str] = None
    dest_entity_gap: Optional[str] = None

    migration_intent: Optional[PromptMigrationIntent] = None

    from services.file_extraction.pending_upload import is_file_source

    if is_file_source(classification):
        fname = (classification.get("source_name") or "upload").strip()
        source_ref = PlanEntityRef(
            connector_type="file",
            display_name=fname,
            entity_key=fname,
            catalog_label=fname,
            resolution_source="user_explicit",
            confidence=1.0,
        )
        migration_intent = PromptMigrationIntent(
            driving_entity_key=fname,
            driving_entity_label=fname,
            extract_field_scope="unspecified",
            extract_fields_confirmed=False,
            needs_extract_clarification=False,
            cross_system=src_ct != dest_ct and dest_ct not in ("unknown", ""),
        )
        log_binding("validate", f"source_file entity={fname}")
    elif not source_required:
        log_binding("validate", "source_skipped (not required by plan requirements)")
    elif source_pinned and source_pinned.is_resolved():
        source_ref = source_pinned
        mi_raw = classification.get("migration_intent") if isinstance(classification.get("migration_intent"), dict) else {}
        field_hints = list(mi_raw.get("field_hints") or [])
        migration_intent = PromptMigrationIntent(
            driving_entity_key=source_pinned.entity_key,
            driving_entity_label=source_pinned.entity_label or source_pinned.entity_key,
            field_hints=field_hints,
            field_hints_strict=bool(mi_raw.get("field_hints_strict")),
            extract_field_scope=mi_raw.get("extract_field_scope") or "explicit",
            extract_fields_confirmed=True,
            needs_extract_clarification=False,
            cross_system=src_ct != dest_ct and dest_ct not in ("unknown", ""),
        )
        log_binding(
            "validate",
            f"source_pinned active_job entity={source_pinned.entity_key or 'none'}",
        )
    else:
        # --- source entity (LLM bind path) ---
        skey = (source_bind.entity_key or "").strip()
        if skey and skey in src_map and source_bind.confidence >= MIN_BIND_CONFIDENCE:
            cand = src_map[skey]
            from services.planning.entity_catalog_policy import (
                is_metadata_entity,
                is_vague_user_data_migration_prompt,
                should_replace_metadata_driving_entity,
            )

            if should_replace_metadata_driving_entity(
                prompt=prompt,
                connector_type=src_ct,
                entity_key=skey,
            ) or (
                is_metadata_entity(skey, connector_type=src_ct)
                and is_vague_user_data_migration_prompt(prompt)
            ):
                errors.append(f"source_metadata_table:{skey}")
                source_entity_gap = "pick_source_entity"
                cand = src_map.get(skey)

            scope = source_bind.extract_field_scope
            matched_fields, unmatched = _map_field_keys(source_bind.field_keys, cand)
            field_hints = matched_fields
            strict = scope == "explicit" and bool(matched_fields)
            confirmed = scope == "explicit" and bool(matched_fields) and not unmatched

            if source_entity_gap:
                migration_intent = PromptMigrationIntent(
                    driving_entity_key=skey,
                    driving_entity_label=cand.label if cand else skey,
                    needs_extract_clarification=True,
                    extract_field_scope="vague",
                    cross_system=src_ct != dest_ct and dest_ct not in ("unknown", ""),
                )
            else:
                if scope == "explicit" and source_bind.field_keys and not matched_fields:
                    field_clarification = True
                    errors.append("source_fields_unmatched")
                elif scope == "vague" or (scope == "unspecified" and not confirmed):
                    if not confirmed:
                        field_clarification = True

                source_ref = _entity_ref_from_bind(
                    connector_type=src_ct,
                    classification=classification,
                    role="source",
                    entity_key=skey,
                    label=cand.label,
                    confidence=source_bind.confidence,
                )

                migration_intent = PromptMigrationIntent(
                    driving_entity_key=skey,
                    driving_entity_label=cand.label,
                    field_hints=field_hints,
                    field_hints_strict=strict,
                    extract_field_scope=scope if scope in ("explicit", "vague", "all_descriptive") else "unspecified",  # type: ignore[arg-type]
                    field_scope_confidence=source_bind.confidence,
                    extract_fields_confirmed=confirmed,
                    needs_extract_clarification=field_clarification and not confirmed,
                    cross_system=src_ct != dest_ct and dest_ct not in ("unknown", ""),
                )
                from services.planning.binding.related_extract import finalize_related_extract_intent

                migration_intent = await finalize_related_extract_intent(
                    migration_intent,
                    source_bind,
                    primary_entity_key=skey,
                    primary_candidate=cand,
                    classification=classification,
                    user_id=str(user_id),
                    catalog_ctx=catalog_ctx,
                    prompt=prompt or "",
                )
        else:
            errors.append("source_entity_unresolved")
            source_entity_gap = "pick_source_entity"
            migration_intent = PromptMigrationIntent(
                needs_extract_clarification=True,
                extract_field_scope="vague",
            )

    if migration_intent is None and source_required:
        migration_intent = PromptMigrationIntent(
            needs_extract_clarification=True,
            extract_field_scope="vague",
        )

    # --- destination entity ---
    if dest_pinned and dest_pinned.is_resolved():
        dest_ref = dest_pinned
        if migration_intent is not None:
            migration_intent.destination_entity_key = dest_pinned.entity_key
            migration_intent.destination_entity_label = (
                dest_pinned.entity_label or dest_pinned.entity_key
            )
        log_binding(
            "validate",
            f"dest_pinned user_confirmed entity={dest_pinned.entity_key or 'none'}",
        )
    elif dest_ct not in ("unknown", "") and (dest_bind or classification.get("planner_destination_entity_confirmed")):
        from services.planning.entity_pins import pinned_entity_key

        pinned_dest = pinned_entity_key(
            classification, role="destination", connector_type=dest_ct
        )
        dkey = (dest_bind.entity_key or "").strip() if dest_bind else ""
        if pinned_dest:
            match_key = None
            if pinned_dest in dest_map:
                match_key = pinned_dest
            else:
                for key in dest_map:
                    if key.lower() == pinned_dest.lower():
                        match_key = key
                        break
            if match_key:
                dkey = match_key
            elif classification.get("planner_destination_entity_confirmed"):
                dkey = pinned_dest
        use_pinned_dest = bool(
            pinned_dest and dkey and dkey.lower() == pinned_dest.lower()
        )
        bind_conf = float(dest_bind.confidence) if dest_bind else 0.0
        if dkey and (dkey in dest_map or use_pinned_dest) and (
            use_pinned_dest or bind_conf >= MIN_BIND_CONFIDENCE
        ):
            dcand = dest_map.get(dkey)
            label = dcand.label if dcand else dkey
            if use_pinned_dest and classification.get("planner_destination_entity_confirmed"):
                from services.planning.entity_pins import plan_entity_ref_from_pin

                dest_ref = plan_entity_ref_from_pin(
                    classification,
                    role="destination",
                    entity_key=dkey,
                    label=label,
                )
            else:
                dest_ref = _entity_ref_from_bind(
                    connector_type=dest_ct,
                    classification=classification,
                    role="destination",
                    entity_key=dkey,
                    label=label,
                    confidence=0.92 if use_pinned_dest else bind_conf,
                )
            if migration_intent is not None:
                migration_intent.destination_entity_key = dkey
                migration_intent.destination_entity_label = label
        elif dest_bind and dest_bind.entity_key:
            errors.append("destination_entity_unresolved")
            dest_entity_gap = "pick_destination_entity"

    mi_dump = (
        migration_intent.model_dump(mode="json")
        if migration_intent is not None
        else None
    )
    contract = PlanContract(
        source=source_ref,
        destination=dest_ref,
        entities_from_goal=[str(e) for e in (parsed_goal or {}).get("entities") or [] if e],
        migration_intent=mi_dump,
    )

    clar_parts = []
    if source_entity_gap:
        clar_parts.append(source_entity_gap)
    if dest_entity_gap:
        clar_parts.append(dest_entity_gap)
    if (
        field_clarification
        and migration_intent is not None
        and not migration_intent.extract_fields_confirmed
    ):
        clar_parts.append("pick_extract_fields")

    status = "ok" if not clar_parts else f"clarifications={','.join(clar_parts)}"
    related = (
        migration_intent.parent_entity_key
        if migration_intent is not None and migration_intent.include_related_data
        else None
    )
    log_binding(
        "validate",
        f"{status} src={source_ref.entity_key or 'none'} related={related or '-'} "
        f"fields={len(migration_intent.field_hints) if migration_intent else 0} "
        f"related_hints={len(migration_intent.related_field_hints or []) if migration_intent else 0} "
        f"confirmed={migration_intent.extract_fields_confirmed if migration_intent else False}",
    )

    return PlanBindingResult(
        contract=contract,
        migration_intent=migration_intent,
        source_clarification=source_entity_gap,
        destination_clarification=dest_entity_gap,
        field_clarification=(
            field_clarification
            and migration_intent is not None
            and not migration_intent.extract_fields_confirmed
        ),
        validate_errors=errors,
    )
