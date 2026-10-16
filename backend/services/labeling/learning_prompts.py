"""LLM prompts for label refinement from execution evidence."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def build_label_refinement_prompt(
    *,
    connector_type: str,
    connector_display_name: str,
    entity_summaries: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
) -> str:
    entities_block = json.dumps(entity_summaries, indent=2, default=str)[:12000]
    evidence_block = json.dumps(evidence_items, indent=2, default=str)[:8000]
    return f"""You refine connector entity labels using evidence from user-approved plans, mappings, and migrations.

Connector: {connector_display_name} ({connector_type})

Rules:
- NEVER change primary.category or primary.description in your output.
- Only propose additions to synonyms, domains, and processes when user language clearly maps to an entity.
- Prefer short lowercase snake_case or plain phrases for domains/processes.
- Skip entities with no signal in the evidence.
- connector_updates are optional (synonyms/domains for the whole system).

Current labeled entities (primary labels are fixed — do not propose changes to them):
{entities_block}

Execution evidence (user phrases, mappings, outcomes):
{evidence_block}

Return JSON only:
{{
  "entity_updates": [
    {{
      "entity_key": "ObjectOrTableName",
      "add_synonyms": ["phrase from user"],
      "add_domains": ["domain"],
      "add_processes": ["process"],
      "confidence": 0.0,
      "rationale": "one sentence"
    }}
  ],
  "connector_updates": {{
    "add_synonyms": [],
    "add_business_domains": [],
    "confidence": 0.0,
    "rationale": ""
  }}
}}
"""
