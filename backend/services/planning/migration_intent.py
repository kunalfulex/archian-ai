"""LLM prompt decomposition + catalog grounding for migration entity intent."""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.planning.migration_intent_schemas import (
    EntityMention,
    ExtractAmbiguity,
    ExtractShape,
    PromptMigrationIntent,
    RelationshipStatus,
)
from services.planning.schemas import Clarification, ExecutionPlan, normalize_connector_type

logger = logging.getLogger(__name__)

_ROLE_ALIASES = {
    "driving": "extract_primary",
    "related": "extract_related",
}
_VALID_ROLES = frozenset(
    {
        "extract_primary",
        "extract_related",
        "extract_secondary",
        "destination",
        "ambiguous",
        "unspecified",
        "driving",
        "related",
    }
)
_AMBIGUITY_CONFIDENCE = 0.7


def _normalize_entity_role(role: str) -> str:
    r = (role or "unspecified").strip().lower()
    if r not in _VALID_ROLES:
        return "unspecified"
    return _ROLE_ALIASES.get(r, r)


def _is_extract_primary_role(role: str) -> bool:
    return _normalize_entity_role(role) == "extract_primary"


def _is_extract_related_role(role: str) -> bool:
    return _normalize_entity_role(role) == "extract_related"


def _is_extract_secondary_role(role: str) -> bool:
    return _normalize_entity_role(role) == "extract_secondary"


def _is_destination_role(role: str) -> bool:
    return _normalize_entity_role(role) == "destination"


def _is_ambiguous_role(role: str) -> bool:
    return _normalize_entity_role(role) == "ambiguous"


def _parse_entity_mentions(raw_mentions: Any) -> List[EntityMention]:
    mentions: List[EntityMention] = []
    for item in raw_mentions or []:
        if not isinstance(item, dict):
            continue
        phrase = str(item.get("phrase") or "").strip()
        if not phrase:
            continue
        role = _normalize_entity_role(str(item.get("role") or "unspecified"))
        fields_req = [
            str(f).strip()
            for f in (item.get("fields_requested") or [])
            if str(f).strip()
        ]
        try:
            confidence = float(item.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        mentions.append(
            EntityMention(
                phrase=phrase,
                role=role,  # type: ignore[arg-type]
                fields_requested=fields_req,
                confidence=max(0.0, min(1.0, confidence)),
                ambiguity_reason=_opt_str(item.get("ambiguity_reason")),
            )
        )
    return mentions


def _infer_field_hints_strict(field_hints: List[str], *, prompt: str = "") -> bool:
    """Legacy helper — decomposer now emits extract_field_scope; kept for tests."""
    if not field_hints:
        return False
    from services.planning.extract_field_clarification import fields_explicit_in_prompt

    if prompt and fields_explicit_in_prompt(prompt, field_hints):
        return True
    return False


def _build_extraction_summary(
    *,
    driving_label: str,
    field_hints: List[str],
    include_related: bool,
    parent_label: Optional[str] = None,
    field_hints_strict: bool = False,
    related_field_hints: Optional[List[str]] = None,
) -> str:
    from services.planning.related_select_fields import format_related_extract_hints

    hint_part = format_related_extract_hints(
        primary_hints=field_hints,
        related_hints=related_field_hints or [],
        field_hints_strict=field_hints_strict,
        include_related=include_related,
        parent_label=parent_label,
    )
    if include_related and parent_label:
        return f"Extract {driving_label} with related {parent_label} fields{hint_part}"
    if field_hints_strict and field_hints:
        return f"Extract {driving_label} fields{hint_part}"
    return f"Extract from {driving_label}"


def _collect_extract_ambiguities(intent: PromptMigrationIntent) -> List[ExtractAmbiguity]:
    ambiguities: List[ExtractAmbiguity] = []
    for mention in intent.entity_mentions:
        if _is_ambiguous_role(mention.role) or mention.confidence < _AMBIGUITY_CONFIDENCE:
            if _is_ambiguous_role(mention.role) or mention.ambiguity_reason:
                ambiguities.append(
                    ExtractAmbiguity(
                        ambiguity_type="extract_entity_role",
                        prompt=(
                            f"For '{mention.phrase}': is this related fields on the primary extract object, "
                            "a separate extract object, or the migration destination?"
                        ),
                        entity_phrase=mention.phrase,
                    )
                )
    if intent.extract_shape == "ambiguous":
        ambiguities.append(
            ExtractAmbiguity(
                ambiguity_type="extract_related_vs_secondary",
                prompt=(
                    "Should the second object be related fields in the same extract query, "
                    "or a separate object to extract on its own?"
                ),
            )
        )
    seen: set[str] = set()
    unique: List[ExtractAmbiguity] = []
    for item in ambiguities:
        key = item.ambiguity_type + (item.entity_phrase or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _is_cross_system(classification: Dict[str, Any]) -> bool:
    from services.planning.extract_field_scope import extract_portability_context

    return bool(extract_portability_context(classification).get("heterogeneous"))


async def _load_salesforce_catalog_summary(
    user_id: str,
    org_id: Optional[str],
) -> List[Dict[str, str]]:
    if not org_id:
        return []
    from services.mapping_plan.catalog_target_resolver import _load_salesforce_org_objects

    rows = await _load_salesforce_org_objects(user_id, org_id)
    return [
        {
            "api_name": (row.get("object_api_name") or "").strip(),
            "label": (row.get("label") or row.get("object_api_name") or "").strip(),
        }
        for row in rows or []
        if (row.get("object_api_name") or "").strip()
    ]


def _opt_str(value: Any) -> Optional[str]:
    s = str(value or "").strip()
    return s or None


def _relationship_name_from_field(field_api_name: str) -> str:
    if field_api_name.endswith("Id"):
        return field_api_name[:-2]
    if field_api_name.endswith("__c"):
        return field_api_name[:-3] + "__r"
    return field_api_name


async def _lookup_relationship_between(
    user_id: str,
    org_id: Optional[str],
    entity_a: str,
    entity_b: str,
) -> Optional[Tuple[str, str, str, str]]:
    if not org_id or not entity_a or not entity_b:
        return None
    from db.connection import database

    rows = await database.fetch_all(
        """
        SELECT object_api_name, field_api_name, lookup_object_api_name, relationship_name
        FROM salesforce_object_fields
        WHERE user_id = :user_id AND org_id = :org_id
          AND COALESCE(is_lookup, false) = true
          AND lookup_object_api_name IS NOT NULL
          AND (
            (LOWER(object_api_name) = LOWER(:a) AND LOWER(lookup_object_api_name) = LOWER(:b))
            OR (LOWER(object_api_name) = LOWER(:b) AND LOWER(lookup_object_api_name) = LOWER(:a))
          )
        ORDER BY object_api_name
        LIMIT 1
        """,
        {"user_id": str(user_id), "org_id": org_id, "a": entity_a, "b": entity_b},
    )
    if not rows:
        return None
    row = dict(rows[0])
    child = (row.get("object_api_name") or "").strip()
    parent = (row.get("lookup_object_api_name") or "").strip()
    field = (row.get("field_api_name") or "").strip()
    rel = (row.get("relationship_name") or "").strip() or _relationship_name_from_field(field)
    if not child or not parent or not field:
        return None
    return child, parent, field, rel


def _normalize_entity_phrase(phrase: str) -> str:
    p = (phrase or "").lower().strip()
    for drop in ("business ", "customer ", "the "):
        if p.startswith(drop):
            p = p[len(drop) :]
    if p.endswith("ies"):
        return p[:-3] + "y"
    if p.endswith("s") and not p.endswith("ss"):
        return p[:-1]
    return p


def _resolve_phrase_to_key(
    phrase: str,
    catalog: List[Dict[str, str]],
    *,
    prompt: str,
    hints: List[str],
) -> Optional[Tuple[str, str]]:
    from services.planning.entity_resolver import _match_salesforce_catalog

    catalog_rows = [
        {"object_api_name": row["api_name"], "label": row["label"]}
        for row in catalog
        if row.get("api_name")
    ]
    matched = _match_salesforce_catalog(phrase, catalog_rows, [phrase])
    if matched:
        api, label, _ = matched
        return api, label
    matched = _match_salesforce_catalog(prompt, catalog_rows, [phrase] + hints)
    if matched:
        api, label, _ = matched
        return api, label

    norm = _normalize_entity_phrase(phrase)
    best: Optional[Tuple[str, str, int]] = None
    for row in catalog:
        api = (row.get("api_name") or "").strip()
        label = (row.get("label") or api).strip()
        if not api:
            continue
        candidates = [
            api.lower(),
            label.lower(),
            _normalize_entity_phrase(label),
            _normalize_entity_phrase(api),
        ]
        for c in candidates:
            if not c:
                continue
            if norm == c:
                return api, label
            if norm in c or c in norm:
                if len(c) < 5 and c in norm:
                    if not re.search(rf"\b{re.escape(c)}\b", norm):
                        continue
                if len(norm) < 5 and norm in c:
                    if not re.search(rf"\b{re.escape(norm)}\b", c):
                        continue
                score = len(c)
                if best is None or score > best[2]:
                    best = (api, label, score)
    if best:
        return best[0], best[1]
    return None


async def _resolve_related_entity_key_after_clarification(
    intent: PromptMigrationIntent,
    *,
    user_id: str,
    classification: Dict[str, Any],
) -> Optional[str]:
    """Resolve related parent entity key after user clarifications (single catalog lookup)."""
    parent = (intent.parent_entity_key or intent.related_entity_key or "").strip()
    if parent:
        return parent

    phrase = (intent.related_entity_phrase or "").strip()
    if not phrase:
        for mention in intent.entity_mentions:
            if _is_extract_related_role(mention.role) and mention.phrase:
                phrase = mention.phrase.strip()
                break
    if not phrase:
        for mention in intent.entity_mentions:
            if _is_ambiguous_role(mention.role) and mention.phrase:
                phrase = mention.phrase.strip()
                break
    if not phrase:
        return None

    from services.planning.catalog_grounding import (
        load_side_catalog_summary,
        resolve_phrase_to_catalog_key,
    )

    src_type = normalize_connector_type(classification.get("source") or "")
    catalog, ct, _ctx = await load_side_catalog_summary(
        str(user_id), classification, role="source"
    )
    if not catalog:
        return None
    hit = resolve_phrase_to_catalog_key(
        phrase,
        catalog,
        ct,
        scoped_text=phrase,
        hints=[phrase],
        cross_system=bool(intent.cross_system or _is_cross_system(classification)),
        allow_prompt_fallback=True,
    )
    return hit[0] if hit else None


async def _apply_related_extract_pins_after_clarification(
    intent: PromptMigrationIntent,
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: str,
) -> PromptMigrationIntent:
    """FK lookup for related extract after clarifications — binding owns fresh planning."""
    if intent.needs_separate_pipelines or intent.extract_shape == "multi_entity":
        return intent

    if not intent.include_related_data:
        return intent.model_copy(
            update={
                "relationship_status": intent.relationship_status or "single_entity",
                "include_related_data": False,
            }
        )

    if intent.parent_entity_key and intent.lookup_field_path:
        return intent

    driving_key = (
        (intent.driving_entity_key or intent.resolved_driving_key() or "").strip()
        or (classification.get("source_object_api_name") or "").strip()
        or (classification.get("source_table_name") or classification.get("source_table") or "").strip()
        or (classification.get("source_sheet_name") or "").strip()
    )
    if not driving_key:
        return intent

    related_key = await _resolve_related_entity_key_after_clarification(
        intent,
        user_id=str(user_id),
        classification=classification,
    )
    if not related_key:
        return intent

    from services.planning.binding.contract import SourceBindLLMOutput
    from services.planning.binding.related_extract import finalize_related_extract_intent

    source_bind = SourceBindLLMOutput(
        entity_key=driving_key,
        related_entity_key=related_key,
        related_field_keys=list(intent.related_field_hints or []),
        confidence=0.9,
    )
    return await finalize_related_extract_intent(
        intent,
        source_bind,
        primary_entity_key=driving_key,
        primary_candidate=None,
        classification=classification,
        user_id=str(user_id),
        prompt=prompt or "",
    )


async def finalize_migration_intent_after_clarifications(
    intent: PromptMigrationIntent,
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> PromptMigrationIntent:
    """Finalize migration intent after extract clarifications (no keyword decompose/ground)."""
    from services.planning.connector_guards import classification_source_endpoint_resolved
    from services.planning.extract_field_clarification import (
        finalize_extract_field_clarification,
        has_entity_extract_ambiguities,
    )

    finalized = await _finalize_destination_intent(
        intent,
        user_id=user_id,
        classification=classification,
        prompt=prompt,
        parsed_goal=parsed_goal,
    )

    if has_entity_extract_ambiguities(finalized):
        return finalized

    if finalized.needs_extract_clarification and not classification_source_endpoint_resolved(
        classification
    ):
        stripped = [
            a
            for a in (finalized.extract_ambiguities or [])
            if (a.ambiguity_type or "") != "pick_extract_fields"
        ]
        return finalized.model_copy(
            update={
                "extract_ambiguities": stripped,
                "needs_extract_clarification": bool(stripped),
            }
        )

    updated = await _apply_related_extract_pins_after_clarification(
        finalized,
        user_id=str(user_id),
        classification=classification,
        prompt=prompt or "",
    )

    if not classification_source_endpoint_resolved(classification):
        return updated

    return await finalize_extract_field_clarification(
        updated,
        user_id=str(user_id),
        classification=classification,
        prompt=prompt or "",
    )


async def _infer_destination_from_extract_spec(
    intent: PromptMigrationIntent,
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: str,
) -> PromptMigrationIntent:
    """
    When no destination object was named, infer target from extract spec + dest catalog.

    Uses synced labels/semantics on the destination side — not a blind copy of source API name.
    """
    if (intent.destination_entity_key or "").strip():
        return intent
    if (intent.destination_entity_phrase or "").strip():
        return intent
    if (classification.get("destination_object_api_name") or "").strip():
        return intent
    table = (
        classification.get("destination_table_name")
        or classification.get("destination_table")
        or ""
    )
    if str(table).strip():
        return intent
    if (classification.get("destination_sheet_name") or "").strip():
        return intent

    driving = intent.resolved_driving_key()
    if not driving:
        return intent

    from services.planning.catalog_grounding import resolve_phrase_to_catalog_key

    dest_catalog_norm = await _load_destination_catalog_summary(user_id, classification)
    if not dest_catalog_norm:
        return intent
    dest_type = normalize_connector_type(classification.get("destination") or "")
    dest_catalog = [
        {
            "entity_key": r["api_name"],
            "api_name": r["api_name"],
            "label": r.get("label") or r["api_name"],
        }
        for r in dest_catalog_norm
        if r.get("api_name")
    ]
    if not dest_catalog:
        return intent

    cross_system = bool(intent.cross_system or _is_cross_system(classification))
    hints = [driving]
    label = (intent.driving_entity_label or "").strip()
    if label and label.lower() != driving.lower():
        hints.append(label)

    hit = resolve_phrase_to_catalog_key(
        driving,
        dest_catalog,
        dest_type,
        scoped_text=(prompt or "").strip() or driving,
        hints=hints,
        cross_system=cross_system,
        allow_prompt_fallback=True,
    )
    if not hit:
        return intent
    return intent.model_copy(
        update={"destination_entity_key": hit[0], "destination_entity_label": hit[1]}
    )


async def _load_destination_catalog_summary(
    user_id: str,
    classification: Dict[str, Any],
) -> List[Dict[str, str]]:
    from services.planning.catalog_grounding import load_side_catalog_summary

    entries, _ct, _ctx = await load_side_catalog_summary(
        user_id, classification, role="destination"
    )
    return [
        {"api_name": e["api_name"], "label": e.get("label") or e["api_name"]}
        for e in entries
        if e.get("api_name")
    ]


async def _finalize_destination_intent(
    intent: PromptMigrationIntent,
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: str,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> PromptMigrationIntent:
    """Resolve explicit migrate target from prompt; infer from extract spec when unnamed."""
    from services.planning.catalog_grounding import (
        resolve_phrase_to_catalog_key,
        scoped_side_text,
    )

    updates: Dict[str, Any] = {}
    cross_system = bool(intent.cross_system or _is_cross_system(classification))

    # Entity-level destination phrases only — not connector/org wording from goal parser.
    dest_phrase = (
        intent.destination_entity_phrase
        or (classification.get("destination_object_api_name") or "").strip()
        or (classification.get("destination_table_name") or classification.get("destination_table") or "").strip()
        or (classification.get("destination_sheet_name") or "").strip()
        or None
    )
    if dest_phrase and not intent.destination_entity_key:
        dest_catalog_norm = await _load_destination_catalog_summary(user_id, classification)
        dest_type = normalize_connector_type(classification.get("destination") or "")
        dest_catalog = [
            {
                "entity_key": r["api_name"],
                "api_name": r["api_name"],
                "label": r.get("label") or r["api_name"],
            }
            for r in dest_catalog_norm
            if r.get("api_name")
        ]
        if dest_catalog:
            scoped = scoped_side_text(
                parsed_goal,
                side="destination",
                phrase=str(dest_phrase),
                cross_system=cross_system,
            )
            hit = resolve_phrase_to_catalog_key(
                str(dest_phrase),
                dest_catalog,
                dest_type,
                scoped_text=scoped,
                hints=[str(dest_phrase)],
                cross_system=cross_system,
                allow_prompt_fallback=not cross_system,
            )
            if hit:
                updates["destination_entity_key"] = hit[0]
                updates["destination_entity_label"] = hit[1]

    dest_type = normalize_connector_type(classification.get("destination") or "")
    snap = None
    if dest_type == "salesforce":
        snap = (classification.get("destination_object_api_name") or "").strip()
    elif dest_type == "sql":
        snap = (
            classification.get("destination_table_name")
            or classification.get("destination_table")
            or ""
        ).strip()
    elif dest_type == "google_sheets":
        snap = (classification.get("destination_sheet_name") or "").strip()

    if snap and not updates.get("destination_entity_key") and not intent.destination_entity_key:
        dest_catalog_norm = await _load_destination_catalog_summary(user_id, classification)
        dest_catalog = [
            {
                "entity_key": r["api_name"],
                "api_name": r["api_name"],
                "label": r.get("label") or r["api_name"],
            }
            for r in dest_catalog_norm
            if r.get("api_name")
        ]
        if dest_catalog:
            from services.planning.catalog_grounding import label_for_catalog_key

            label = label_for_catalog_key(dest_catalog, snap)
            if dest_type == "google_sheets" and "::" not in snap:
                for row in dest_catalog:
                    tab = (row.get("label") or "").strip()
                    if tab.lower() == snap.lower():
                        updates["destination_entity_key"] = row["entity_key"]
                        updates["destination_entity_label"] = tab
                        break
            else:
                updates["destination_entity_key"] = snap
                updates["destination_entity_label"] = label

    result = intent.model_copy(update=updates) if updates else intent
    return await _infer_destination_from_extract_spec(
        result,
        user_id=user_id,
        classification=classification,
        prompt=prompt,
    )


def apply_extract_clarification_selections(
    intent: PromptMigrationIntent,
    clarifications: List[Any],
    selections: List[Dict[str, Any]],
) -> PromptMigrationIntent:
    """Apply user answers to ambiguous extract intent and clear clarification flags."""
    updated = intent.model_copy(deep=True)
    applied = False

    for clar in clarifications or []:
        ctype = getattr(clar, "clarification_type", None) or (clar.get("clarification_type") if isinstance(clar, dict) else None)
        if ctype not in (
            "extract_related_vs_secondary",
            "extract_entity_role",
            "pick_extract_fields",
        ):
            continue
        clar_id = getattr(clar, "id", None) or (clar.get("id") if isinstance(clar, dict) else None)
        matching = [s for s in (selections or []) if s.get("clarification_id") == clar_id]
        if not matching:
            continue
        sel = matching[0]
        opt_id = (sel.get("option_id") or "").strip()
        text_val = sel.get("text_value") or sel.get("new_workbook_title")
        applied = True

        if ctype == "pick_extract_fields":
            from services.planning.extract_field_clarification import (
                apply_field_clarification_selections,
                build_field_clarification_options,
            )

            options = build_field_clarification_options(
                updated.suggested_field_hints or [],
                entity_label=updated.driving_entity_label or updated.resolved_driving_key(),
            )
            opt_ids = [
                (s.get("option_id") or "").strip()
                for s in matching
                if (s.get("option_id") or "").strip()
            ]
            updated = apply_field_clarification_selections(
                updated,
                option_ids=opt_ids,
                text_value=text_val,
                options=options,
            )
            continue

        if ctype == "extract_related_vs_secondary":
            related_phrase = (updated.related_entity_phrase or "").strip()
            if not related_phrase:
                for mention in updated.entity_mentions:
                    if _is_ambiguous_role(mention.role) and mention.phrase:
                        related_phrase = mention.phrase.strip()
                        break
            if opt_id == "related_fields":
                patch: Dict[str, Any] = {
                    "extract_shape": "single_with_related",
                    "include_related_data": True,
                    "relationship_status": "related",
                }
                if related_phrase:
                    patch["related_entity_phrase"] = related_phrase
                updated = updated.model_copy(update=patch)
            elif opt_id == "separate_extract":
                updated = updated.model_copy(
                    update={
                        "extract_shape": "multi_entity",
                        "include_related_data": False,
                        "relationship_status": "unrelated",
                        "needs_separate_pipelines": True,
                        "separate_pipelines_message": (
                            "Your request extracts multiple objects. "
                            "Run separate pipelines for each object."
                        ),
                    }
                )
            amb = [
                a
                for a in (updated.extract_ambiguities or [])
                if a.ambiguity_type != "extract_related_vs_secondary"
            ]
            updated = updated.model_copy(update={"extract_ambiguities": amb})
            continue

        phrase: Optional[str] = None
        for amb in updated.extract_ambiguities or []:
            if amb.ambiguity_type == "extract_entity_role" and amb.entity_phrase:
                phrase = amb.entity_phrase
                break
        if not phrase:
            for m in updated.entity_mentions:
                if _is_ambiguous_role(m.role):
                    phrase = m.phrase
                    break
        if not phrase:
            continue

        role_map = {
            "extract_related": "extract_related",
            "extract_secondary": "extract_secondary",
            "destination": "destination",
        }
        new_role = role_map.get(opt_id)
        if not new_role:
            continue

        mentions = list(updated.entity_mentions)
        for idx, mention in enumerate(mentions):
            if mention.phrase == phrase:
                mentions[idx] = mention.model_copy(
                    update={"role": new_role, "confidence": 1.0, "ambiguity_reason": None}
                )
                break
        else:
            mentions.append(
                EntityMention(phrase=phrase, role=new_role, confidence=1.0)  # type: ignore[arg-type]
            )

        patch: Dict[str, Any] = {"entity_mentions": mentions}
        if opt_id == "extract_related":
            patch.update(
                {
                    "include_related_data": True,
                    "related_entity_phrase": phrase,
                    "relationship_status": "related",
                    "extract_shape": "single_with_related",
                }
            )
        elif opt_id == "extract_secondary":
            secs = list(updated.secondary_entity_phrases or [])
            if phrase not in secs:
                secs.append(phrase)
            patch.update(
                {
                    "secondary_entity_phrases": secs,
                    "extract_shape": "multi_entity",
                    "include_related_data": False,
                }
            )
        elif opt_id == "destination":
            patch["destination_entity_phrase"] = phrase
        updated = updated.model_copy(update=patch)
        amb = [
            a
            for a in (updated.extract_ambiguities or [])
            if not (
                a.ambiguity_type == "extract_entity_role"
                and (a.entity_phrase or "") == phrase
            )
        ]
        updated = updated.model_copy(update={"extract_ambiguities": amb})

    if applied:
        remaining = updated.extract_ambiguities or []
        updated = updated.model_copy(
            update={
                "needs_extract_clarification": bool(remaining),
                "extract_shape": "single_entity"
                if updated.extract_shape == "ambiguous" and not remaining
                else updated.extract_shape,
            }
        )
    return updated


def clarification_from_separate_pipelines_intent(
    intent: PromptMigrationIntent,
) -> Optional[Clarification]:
    if not intent.needs_separate_pipelines:
        return None
    message = (
        intent.separate_pipelines_message
        or "These entities need separate migration pipelines. Run one entity at a time."
    )
    return Clarification(
        id=str(uuid.uuid4()),
        clarification_type="migration_intent_separate_pipelines",
        prompt=message,
        options=[],
        required=True,
    )


def clarifications_from_migration_intent_ambiguities(
    intent: PromptMigrationIntent,
    *,
    exclude_types: Optional[frozenset[str]] = None,
) -> List[Clarification]:
    """Materialize extract-role ambiguities from migration intent (not pick_extract_fields)."""
    from services.planning.schemas import ClarificationOption

    skip = (
        exclude_types
        if exclude_types is not None
        else frozenset({"pick_extract_fields"})
    )
    out: List[Clarification] = []
    seen: set[str] = set()

    for amb in intent.extract_ambiguities or []:
        ctype = (amb.ambiguity_type or "").strip()
        if not ctype or ctype in skip or ctype in seen:
            continue
        options: List[Dict[str, str]] = []
        if ctype == "extract_related_vs_secondary":
            options = [
                {"id": "related_fields", "label": "Related fields on the primary object (one query)"},
                {"id": "separate_extract", "label": "Separate extract for the second object"},
            ]
        elif ctype == "extract_entity_role":
            options = [
                {"id": "extract_related", "label": "Related fields in extract"},
                {"id": "extract_secondary", "label": "Separate extract object"},
                {"id": "destination", "label": "Migration destination only"},
            ]
        elif ctype == "pick_extract_fields":
            from services.planning.extract_field_clarification import (
                build_field_clarification_options,
            )

            options = build_field_clarification_options(
                intent.suggested_field_hints or [],
                entity_label=intent.driving_entity_label or intent.resolved_driving_key(),
            )
        out.append(
            Clarification(
                id=str(uuid.uuid4()),
                clarification_type=ctype,
                prompt=amb.prompt,
                options=[
                    ClarificationOption(id=o["id"], label=o["label"]) for o in options
                ]
                if options
                else [],
                required=True,
            )
        )
        seen.add(ctype)
    return out
