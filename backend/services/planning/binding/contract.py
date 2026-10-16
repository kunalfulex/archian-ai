"""Pydantic models for plan binding LLM I/O."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from services.planning.migration_intent_schemas import ExtractFieldScope, PromptMigrationIntent
from services.planning.schemas import PlanContract

ExtractFieldScopeBind = Literal["explicit", "vague", "all_descriptive", "unspecified"]
DestFieldScopeBind = Literal["explicit", "vague", "all_descriptive", "none", "unspecified"]


class FieldCandidate(BaseModel):
    key: str
    label: str


class RelationshipTargetCandidate(BaseModel):
    """Lookup parent object reachable from a primary extract entity."""

    related_entity_key: str
    lookup_field: Optional[str] = None
    relationship: Optional[str] = None
    label: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    suggested_match_score: Optional[float] = None
    match_reasons: List[str] = Field(default_factory=list)


class EntityCandidate(BaseModel):
    entity_key: str
    label: str
    category: Optional[str] = None
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    match_score: Optional[float] = None
    match_reasons: List[str] = Field(default_factory=list)
    fields: List[FieldCandidate] = Field(default_factory=list)
    relationship_targets: List[RelationshipTargetCandidate] = Field(default_factory=list)


class SourceBindLLMOutput(BaseModel):
    entity_key: str = ""
    extract_field_scope: ExtractFieldScopeBind = "unspecified"
    field_keys: List[str] = Field(default_factory=list)
    related_entity_key: Optional[str] = None
    related_field_keys: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    reasoning_short: Optional[str] = None


class DestinationBindLLMOutput(BaseModel):
    entity_key: str = ""
    field_scope: DestFieldScopeBind = "none"
    field_keys: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    reasoning_short: Optional[str] = None


class PlanBindingResult(BaseModel):
    contract: PlanContract
    migration_intent: Optional[PromptMigrationIntent] = None
    source_clarification: Optional[str] = None
    destination_clarification: Optional[str] = None
    field_clarification: bool = False
    validate_errors: List[str] = Field(default_factory=list)
