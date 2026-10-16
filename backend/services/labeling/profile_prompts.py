"""LLM prompt for connector business profile rollup."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def build_connector_profile_prompt(
    *,
    connector_type: str,
    display_name: str,
    user_description: Optional[str],
    entity_summaries: List[Dict[str, Any]],
    stats: Dict[str, int],
) -> str:
    user_block = ""
    if user_description and user_description.strip():
        user_block = f"\nUser-provided context (trust as intent hint, not sole source):\n{user_description.strip()}\n"

    compact_entities = []
    for item in entity_summaries[:50]:
        sem = item.get("semantics") or {}
        primary = sem.get("primary") if isinstance(sem.get("primary"), dict) else {}
        compact_entities.append(
            {
                "entity_key": item.get("entity_key"),
                "display_name": item.get("entity_display_name"),
                "primary_category": primary.get("category"),
                "primary_description": (primary.get("description") or "")[:300],
                "domains": sem.get("domains") or [],
                "synonyms": (sem.get("synonyms") or [])[:5],
            }
        )

    return f"""You are Archian AI, a data platform analyst. Infer the business purpose of this connected system from its labeled entities.

Connector type: {connector_type}
Display name: {display_name}
Entity labeling stats: {json.dumps(stats, default=str)}
{user_block}
Labeled entities (sample):
{json.dumps(compact_entities, indent=2, default=str)}

Return JSON only:
{{
  "synopsis": "2-4 sentences on what this system is primarily used for in this tenant",
  "business_domains": ["finance", "sales", "service"],
  "system_roles": ["crm", "erp", "warehouse", "reporting"],
  "synonyms": ["phrases users might use for this system"],
  "capabilities": {{
    "record_families": [
      {{"label": "appointments", "description": "...", "entity_hints": ["ServiceAppointment"]}}
    ],
    "not_included": ["things this system likely does NOT hold"]
  }},
  "confidence": 0.85
}}

Rules:
- Base conclusions on entity labels and schema-derived semantics, not display name alone
- business_domains/system_roles: short tags
- Do not invent entities not supported by the entity list
"""
