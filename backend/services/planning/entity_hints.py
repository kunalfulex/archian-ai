"""Shared entity hint assembly for catalog retrieve, bind, and clarification ranking."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.planning.schemas import normalize_connector_type


@dataclass
class EntityRetrieveHints:
    """Hints for catalog entity scoring.

    user_hints — explicit user / planner entity phrases (tier-1 exact scoring).
    context_hints — job / session fallback phrases (tier-2/3 only; never beat user_hints).
    required_entity_keys — ensure these catalog rows appear in the shortlist.
    """

    user_hints: List[str] = field(default_factory=list)
    context_hints: List[str] = field(default_factory=list)
    required_entity_keys: List[str] = field(default_factory=list)


def _dedupe_preserve_order(hints: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for h in hints:
        text = (h or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def goal_entity_hints(parsed_goal: Optional[Dict[str, Any]]) -> List[str]:
    hints: List[str] = []
    for val in (parsed_goal or {}).get("entities") or []:
        s = str(val).strip()
        if s:
            hints.append(s)
    return hints


def goal_source_user_hints(
    parsed_goal: Optional[Dict[str, Any]],
    migration_intent: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Source-side user entity phrases — not destination names."""
    hints: List[str] = []
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    for key in ("driving_entity_phrase", "related_entity_phrase"):
        val = (intent.get(key) or "").strip()
        if val:
            hints.append(val)
    for phrase in intent.get("secondary_entity_phrases") or []:
        s = str(phrase).strip()
        if s:
            hints.append(s)
    for mention in intent.get("entity_mentions") or []:
        if not isinstance(mention, dict):
            continue
        role = str(mention.get("role") or "").lower()
        if role in (
            "extract_primary",
            "extract_related",
            "extract_secondary",
            "driving",
            "related",
        ):
            phrase = (mention.get("phrase") or "").strip()
            if phrase:
                hints.append(phrase)
    return hints


def goal_destination_user_hints(
    migration_intent: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """User-named destination entity phrases (not connector/org endpoint phrases)."""
    hints: List[str] = []
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    for key in ("destination_entity_key", "destination_entity_label"):
        val = (intent.get(key) or "").strip()
        if val:
            hints.append(val)
    dest_entity_phrase = (intent.get("destination_entity_phrase") or "").strip()
    if dest_entity_phrase:
        hints.append(dest_entity_phrase)
    for mention in intent.get("entity_mentions") or []:
        if not isinstance(mention, dict):
            continue
        role = str(mention.get("role") or "").lower()
        if role in ("destination", "migrate_target", "target", "load", "map_target"):
            phrase = (mention.get("phrase") or "").strip()
            if phrase:
                hints.append(phrase)
    return hints


def migration_intent_hints(
    migration_intent: Optional[Dict[str, Any]],
    role: str,
) -> List[str]:
    raw = migration_intent if isinstance(migration_intent, dict) else {}
    hints: List[str] = []
    if role == "destination":
        for col in raw.get("field_hints") or []:
            s = str(col).strip()
            if s:
                hints.append(s)
    return hints


def classification_entity_hints(
    classification: Dict[str, Any],
    role: str,
    connector_type: str,
) -> List[str]:
    hints: List[str] = []
    ct = normalize_connector_type(connector_type)
    if role == "source":
        for key in ("source_object_api_name", "primary_object"):
            val = (classification.get(key) or "").strip()
            if val:
                hints.append(val)
        if ct == "sql":
            val = (
                classification.get("source_table_name") or classification.get("table_name") or ""
            ).strip()
            if val:
                hints.append(val)
        if ct == "google_sheets":
            val = (
                classification.get("source_sheet_name") or classification.get("sheet_name") or ""
            ).strip()
            if val:
                hints.append(val)
    else:
        for key in ("destination_object_api_name", "object"):
            val = (classification.get(key) or "").strip()
            if val:
                hints.append(val)
        if ct == "sql":
            val = (
                classification.get("destination_table_name")
                or classification.get("destination_table")
                or ""
            ).strip()
            if val:
                hints.append(val)
        if ct == "google_sheets":
            val = (classification.get("destination_sheet_name") or "").strip()
            if val:
                hints.append(val)
    return hints


def _has_user_destination_entity(
    user_hints: List[str],
    migration_intent: Optional[Dict[str, Any]],
) -> bool:
    if goal_destination_user_hints(migration_intent):
        return True
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    for key in ("destination_entity_phrase", "destination_entity_key", "destination_entity_label"):
        if (intent.get(key) or "").strip():
            return True
    return bool(user_hints)


def build_retrieve_entity_hints(
    *,
    role: str,
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
    connector_type: str = "",
    migration_intent: Optional[Dict[str, Any]] = None,
    extra_hints: Optional[List[str]] = None,
    source_entity_key: Optional[str] = None,
    source_entity_label: Optional[str] = None,
) -> EntityRetrieveHints:
    """
    Assemble hints for retrieve / bind / clarification ranking.

    User-named entities always land in user_hints (tier-1). Active-job source entity
    is a context fallback for destination only when the user did not name a target entity.
    """
    intent = migration_intent if isinstance(migration_intent, dict) else {}
    goal = parsed_goal or {}
    ct = normalize_connector_type(
        connector_type
        or classification.get("source" if role == "source" else "destination")
        or ""
    )

    user_hints: List[str] = []
    user_hints.extend(goal_entity_hints(goal))
    for hint in extra_hints or []:
        text = str(hint or "").strip()
        if text:
            user_hints.append(text)

    if role == "source":
        user_hints.extend(goal_source_user_hints(goal, intent))
        driving = (intent.get("driving_entity_key") or "").strip()
        if driving:
            user_hints.insert(0, driving)
    else:
        dest_user = goal_destination_user_hints(intent)
        if dest_user:
            user_hints = dest_user + [h for h in user_hints if h.lower() not in {d.lower() for d in dest_user}]
        user_hints.extend(migration_intent_hints(intent, "destination"))

    user_hints.extend(classification_entity_hints(classification, role, ct))

    if role == "source":
        for key in ("document_type", "object", "primary_object", "entity"):
            val = (classification.get(key) or "").strip()
            if val:
                user_hints.append(val)
    elif role == "destination" and classification.get("planner_destination_entity_confirmed"):
        key = (classification.get("destination_object_api_name") or classification.get("object") or "").strip()
        if key:
            user_hints.insert(0, key)

    user_hints = _dedupe_preserve_order(user_hints)

    context_hints: List[str] = []
    required_keys: List[str] = []

    if role == "destination" and not _has_user_destination_entity(user_hints, intent):
        infer_key = (source_entity_key or intent.get("driving_entity_key") or "").strip()
        infer_label = (
            source_entity_label or intent.get("driving_entity_label") or infer_key or ""
        ).strip()
        if infer_key:
            required_keys.append(infer_key)
            for piece in (infer_label, infer_key):
                if piece and piece.lower() not in {h.lower() for h in user_hints}:
                    context_hints.append(piece)

    return EntityRetrieveHints(
        user_hints=user_hints,
        context_hints=_dedupe_preserve_order(context_hints),
        required_entity_keys=_dedupe_preserve_order(required_keys),
    )


def assemble_ranking_hints(
    *,
    role: str,
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
    connector_type: str,
    migration_intent: Optional[Dict[str, Any]] = None,
    plan_migration_intent: Optional[Dict[str, Any]] = None,
    source_entity_key: Optional[str] = None,
    source_entity_label: Optional[str] = None,
    source_catalog_label: Optional[str] = None,
) -> List[str]:
    """
    Flat hint list for entity_resolver catalog ranking (backward compatible).

    Prepends user destination phrases, then job source entity keys when user was vague.
    """
    merged_intent = dict(migration_intent or {})
    if plan_migration_intent:
        for key, val in plan_migration_intent.items():
            merged_intent.setdefault(key, val)

    built = build_retrieve_entity_hints(
        role=role,
        classification=classification,
        parsed_goal=parsed_goal,
        connector_type=connector_type,
        migration_intent=merged_intent,
        source_entity_key=source_entity_key,
        source_entity_label=source_entity_label or source_catalog_label,
    )
    hints = list(built.user_hints)
    if role == "destination" and source_entity_key:
        src = (source_entity_key or "").strip()
        if src and not _has_user_destination_entity(built.user_hints, merged_intent):
            for piece in (src, source_catalog_label, source_entity_label):
                if piece and str(piece).strip():
                    hints.append(str(piece).strip())
    return _dedupe_preserve_order(hints)
