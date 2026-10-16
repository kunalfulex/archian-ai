"""Structured migration intent from business-language prompts."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

RelationshipStatus = Literal["related", "unrelated", "single_entity"]
ExtractShape = Literal["single_entity", "single_with_related", "multi_entity", "ambiguous"]
ExtractFieldScope = Literal["explicit", "vague", "all_descriptive", "unspecified"]
EntityRole = Literal[
    "extract_primary",
    "extract_related",
    "extract_secondary",
    "destination",
    "ambiguous",
    "unspecified",
    # legacy decompose roles
    "driving",
    "related",
]


class EntityMention(BaseModel):
    phrase: str
    role: EntityRole = "unspecified"
    fields_requested: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    ambiguity_reason: Optional[str] = None


class ExtractAmbiguity(BaseModel):
    ambiguity_type: str
    prompt: str
    entity_phrase: Optional[str] = None


class PromptMigrationIntent(BaseModel):
    """Catalog-grounded entity semantics for extract → map → migrate planning."""

    entity_mentions: List[EntityMention] = Field(default_factory=list)
    field_hints: List[str] = Field(default_factory=list)
    field_hints_strict: bool = False
    extract_field_scope: ExtractFieldScope = "unspecified"
    field_scope_confidence: float = 1.0
    suggested_field_hints: List[str] = Field(default_factory=list)
    extract_fields_confirmed: bool = False
    related_field_hints: List[str] = Field(default_factory=list)
    relationship_status: RelationshipStatus = "single_entity"
    extract_shape: ExtractShape = "single_entity"

    driving_entity_phrase: Optional[str] = None
    related_entity_phrase: Optional[str] = None
    destination_entity_phrase: Optional[str] = None
    secondary_entity_phrases: List[str] = Field(default_factory=list)

    driving_entity_key: Optional[str] = None
    driving_entity_label: Optional[str] = None
    parent_entity_key: Optional[str] = None
    parent_entity_label: Optional[str] = None
    related_entity_key: Optional[str] = None
    related_entity_label: Optional[str] = None

    lookup_field_path: Optional[str] = None
    lookup_relationship_name: Optional[str] = None

    cross_system: bool = False
    include_related_data: bool = False
    destination_entity_key: Optional[str] = None
    destination_entity_label: Optional[str] = None

    needs_separate_pipelines: bool = False
    separate_pipelines_message: Optional[str] = None
    needs_extract_clarification: bool = False
    extract_ambiguities: List[ExtractAmbiguity] = Field(default_factory=list)
    extraction_summary: Optional[str] = None
    intent_confidence: float = 1.0

    def resolved_driving_key(self) -> Optional[str]:
        return (self.driving_entity_key or "").strip() or None

    def resolved_destination_key(self) -> Optional[str]:
        return (self.destination_entity_key or "").strip() or None

    def related_parent_keys(self) -> List[str]:
        keys: List[str] = []
        for key in (self.parent_entity_key, self.related_entity_key):
            k = (key or "").strip()
            if k and k not in keys:
                keys.append(k)
        return keys

# --- Field scope normalize (merged P4.9) ---

"""Normalize decomposer field scope output; S08 is authoritative — no prompt regex fallback."""

from typing import Any, Dict, List, Sequence, Tuple

_SCOPE_VALUES = frozenset({"explicit", "vague", "all_descriptive", "unspecified"})
_SCOPE_CONFIDENCE_THRESHOLD = 0.7


def parse_decompose_field_scope(data: Dict[str, Any]) -> Tuple[ExtractFieldScope, float]:
    raw = str(data.get("extract_field_scope") or "unspecified").lower().strip()
    scope: ExtractFieldScope = raw if raw in _SCOPE_VALUES else "unspecified"  # type: ignore[assignment]
    try:
        confidence = float(data.get("field_scope_confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    return scope, max(0.0, min(1.0, confidence))


def normalize_decomposed_field_scope(
    data: Dict[str, Any],
    *,
    prompt: str,
    field_hints: Sequence[str],
    field_hints_strict: bool,
) -> Tuple[ExtractFieldScope, float, List[str], bool]:
    """
    Apply authoritative extract_field_scope from decomposer.

    When scope is unspecified or low confidence, stay vague — field picker runs via S10,
    not prompt regex inference.
    Returns (scope, confidence, field_hints, field_hints_strict).
    """
    from services.planning.extract_field_clarification import strip_vague_field_hints

    scope, confidence = parse_decompose_field_scope(data)
    hints = [str(h).strip() for h in field_hints if str(h).strip()]
    strict = bool(field_hints_strict)

    if scope == "vague":
        return "vague", confidence, [], False
    if scope == "all_descriptive":
        return "all_descriptive", confidence, [], False
    if scope == "explicit":
        concrete = strip_vague_field_hints(hints)
        strict = bool(data.get("field_hints_strict")) and bool(concrete)
        if not concrete:
            return "vague", confidence, [], False
        return "explicit", confidence, concrete, strict

    if scope == "unspecified" or confidence < _SCOPE_CONFIDENCE_THRESHOLD:
        concrete = strip_vague_field_hints(hints)
        if concrete and strict:
            return "explicit", max(confidence, _SCOPE_CONFIDENCE_THRESHOLD), concrete, True
        return "vague", min(confidence, 0.65) if scope == "unspecified" else confidence, [], False

    return scope, confidence, hints, strict


def scope_confidence_threshold() -> float:
    return _SCOPE_CONFIDENCE_THRESHOLD

