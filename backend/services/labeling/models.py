"""Data models for entity semantics (multi-label)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PrimaryLabel:
    category: str
    description: str

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "PrimaryLabel":
        data = data or {}
        return cls(
            category=str(data.get("category") or "Unknown").strip(),
            description=str(data.get("description") or "").strip(),
        )

    def to_dict(self) -> Dict[str, str]:
        return {"category": self.category, "description": self.description}


@dataclass
class EntitySemantics:
    primary: PrimaryLabel
    domains: List[str] = field(default_factory=list)
    processes: List[str] = field(default_factory=list)
    synonyms: List[str] = field(default_factory=list)
    relationship_hints: List[Dict[str, Any]] = field(default_factory=list)
    entity_class: str = "business"
    descriptive_fields: List[str] = field(default_factory=list)
    confidence: float = 0.5
    source: str = "schema_bootstrap"

    @classmethod
    def from_llm_dict(cls, data: Dict[str, Any]) -> "EntitySemantics":
        primary = PrimaryLabel.from_dict(data.get("primary"))
        if not primary.category or primary.category == "Unknown":
            # Legacy single-label shape fallback
            primary = PrimaryLabel(
                category=str(data.get("document_type") or data.get("category") or "Unknown"),
                description=str(
                    data.get("document_description") or data.get("category_description") or ""
                ),
            )
        domains = [str(x).strip() for x in (data.get("domains") or []) if str(x).strip()]
        processes = [str(x).strip() for x in (data.get("processes") or []) if str(x).strip()]
        synonyms = [str(x).strip() for x in (data.get("synonyms") or []) if str(x).strip()]
        hints = data.get("relationship_hints") or []
        if not isinstance(hints, list):
            hints = []
        entity_class = str(data.get("entity_class") or "business").strip().lower()
        if entity_class not in ("business", "metadata", "junction", "system"):
            entity_class = "business"
        desc_fields = [
            str(x).strip()
            for x in (data.get("descriptive_fields") or [])
            if str(x).strip()
        ]
        conf = data.get("confidence")
        if conf is None and data.get("confidence_score") is not None:
            try:
                conf = float(data["confidence_score"]) / 100.0
            except (TypeError, ValueError):
                conf = 0.5
        try:
            confidence = float(conf) if conf is not None else 0.5
        except (TypeError, ValueError):
            confidence = 0.5
        return cls(
            primary=primary,
            domains=domains,
            processes=processes,
            synonyms=synonyms,
            relationship_hints=hints,
            entity_class=entity_class,
            descriptive_fields=desc_fields,
            confidence=max(0.0, min(1.0, confidence)),
            source=str(data.get("source") or "schema_bootstrap"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary": self.primary.to_dict(),
            "domains": self.domains,
            "processes": self.processes,
            "synonyms": self.synonyms,
            "relationship_hints": self.relationship_hints,
            "entity_class": self.entity_class,
            "descriptive_fields": self.descriptive_fields,
            "confidence": self.confidence,
            "source": self.source,
        }

    def legacy_ai_categorization(self) -> Dict[str, Any]:
        """Shape for sql_extracted_schemas / gsheets ai_categorization JSONB."""
        return {
            "document_type": self.primary.category,
            "document_description": self.primary.description,
            "confidence_score": int(round(self.confidence * 100)),
            "semantics": self.to_dict(),
        }


@dataclass
class ConnectorAiProfile:
    synopsis: str
    business_domains: List[str] = field(default_factory=list)
    system_roles: List[str] = field(default_factory=list)
    synonyms: List[str] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    rollup_stats: Dict[str, Any] = field(default_factory=dict)
    source: str = "schema_rollup"
    confidence: float = 0.5

    @classmethod
    def from_llm_dict(cls, data: Dict[str, Any]) -> "ConnectorAiProfile":
        caps = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
        stats = data.get("rollup_stats") if isinstance(data.get("rollup_stats"), dict) else {}
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        return cls(
            synopsis=str(data.get("synopsis") or "").strip(),
            business_domains=[
                str(x).strip() for x in (data.get("business_domains") or []) if str(x).strip()
            ],
            system_roles=[
                str(x).strip() for x in (data.get("system_roles") or []) if str(x).strip()
            ],
            synonyms=[str(x).strip() for x in (data.get("synonyms") or []) if str(x).strip()],
            capabilities=caps,
            rollup_stats=stats,
            source=str(data.get("source") or "schema_rollup"),
            confidence=max(0.0, min(1.0, confidence)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "synopsis": self.synopsis,
            "business_domains": self.business_domains,
            "system_roles": self.system_roles,
            "synonyms": self.synonyms,
            "capabilities": self.capabilities,
            "rollup_stats": self.rollup_stats,
            "source": self.source,
            "confidence": self.confidence,
        }
