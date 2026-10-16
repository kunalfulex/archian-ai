"""Build assessable entity inventory from per-member assessment reports."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.enterprise_graph.models import GraphMember


@dataclass
class SourceEntity:
    member_id: str
    connector_type: str
    entity_id: str
    label: str
    normalized_name: str
    domain_tags: List[str] = field(default_factory=list)
    column_names: List[str] = field(default_factory=list)
    schema_columns: List[Dict[str, Any]] = field(default_factory=list)
    category: str = ""
    category_description: str = ""
    object_name: str = ""
    categorization_confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_id": self.member_id,
            "connector_type": self.connector_type,
            "entity_id": self.entity_id,
            "label": self.label,
            "normalized_name": self.normalized_name,
            "domain_tags": self.domain_tags,
            "column_names": self.column_names,
            "schema_columns": self.schema_columns,
            "category": self.category,
            "category_description": self.category_description,
            "object_name": self.object_name,
            "categorization_confidence": self.categorization_confidence,
        }


_CRM_ALIASES: Dict[str, str] = {
    "account": "party",
    "accounts": "party",
    "customer": "party",
    "customers": "party",
    "contact": "party",
    "contacts": "party",
    "lead": "party",
    "leads": "party",
    "user": "party",
    "users": "party",
    "opportunity": "sales",
    "opportunities": "sales",
    "deal": "sales",
    "deals": "sales",
    "order": "commerce",
    "orders": "commerce",
    "product": "catalog",
    "products": "catalog",
    "invoice": "finance",
    "invoices": "finance",
    "case": "support",
    "cases": "support",
}


def normalize_entity_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if s.endswith("ies") and len(s) > 4:
        s = s[:-3] + "y"
    elif s.endswith("s") and not s.endswith("ss") and len(s) > 3:
        s = s[:-1]
    return s


def _parse_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _entity_label(entity_id: str, meta: Dict[str, Any]) -> str:
    if meta.get("label"):
        return str(meta["label"])
    if "::" in entity_id:
        parts = entity_id.split("::", 1)
        return parts[1] if len(parts) > 1 else entity_id
    return entity_id


def _column_names_from_profile(ep: Dict[str, Any]) -> List[str]:
    cols: List[str] = []
    qr = ep.get("quality_report") or {}
    for key in ("missing_values", "format_issues", "inconsistencies"):
        block = qr.get(key)
        if isinstance(block, dict):
            cols.extend(block.keys())
    schema_cols = ep.get("columns") or ep.get("schema_columns")
    if isinstance(schema_cols, list):
        for c in schema_cols:
            if isinstance(c, str):
                cols.append(c)
            elif isinstance(c, dict) and c.get("name"):
                cols.append(str(c["name"]))
    return list(dict.fromkeys(cols))[:40]


async def build_inventory_from_member_results(
    members: List[GraphMember],
    member_results: List[Dict[str, Any]],
    *,
    user_id: Optional[str] = None,
) -> List[SourceEntity]:
    """Flatten entities from completed assessment reports."""
    from services.semantic_mapping.schema_inventory import build_schema_aware_inventory

    if user_id:
        return await build_schema_aware_inventory(user_id, members, member_results)

    member_by_id = {m.id: m for m in members}
    inventory: List[SourceEntity] = []

    for mr in member_results:
        if mr.get("error"):
            continue
        member = member_by_id.get(mr["member_id"])
        if not member:
            continue
        report = mr.get("report") or {}
        entities = _parse_json(report.get("entities")) or []
        if not isinstance(entities, list):
            continue
        for ep in entities:
            if not isinstance(ep, dict) or ep.get("error"):
                continue
            eid = str(ep.get("entity_id") or ep.get("label") or "")
            if not eid:
                continue
            meta = ep.get("meta") or {}
            label = _entity_label(eid, meta)
            norm = normalize_entity_name(label)
            domain = _CRM_ALIASES.get(norm, "")
            tags = [domain] if domain else []
            cat = ep.get("category") or meta.get("category")
            if cat:
                tags.append(str(cat).lower())
            inventory.append(
                SourceEntity(
                    member_id=member.id,
                    connector_type=member.connector_type,
                    entity_id=eid,
                    label=label,
                    normalized_name=norm,
                    domain_tags=tags,
                    column_names=_column_names_from_profile(ep),
                )
            )
    return inventory
