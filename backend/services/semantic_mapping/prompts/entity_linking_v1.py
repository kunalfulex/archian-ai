"""Versioned prompts for graph-level cross-connector entity mapping (LLM-first)."""

ENTITY_LINKING_SYSTEM = """You are a senior data architect mapping the same business entities across heterogeneous systems
(SQL databases, Salesforce, Google Sheets, etc.).

Rules:
- Output ONLY valid JSON. No markdown.
- Each cluster links the SAME business concept across connectors — one entity per member_id (pairwise).
- Each cluster MUST include exactly one entity from each of at least TWO different member_id values.
- NEVER put multiple tables/sheets from the same connector in one cluster.
- Use entity_class=business entities only; skip entity_class=metadata/system and junction tables.
- Use semantics.category, synonyms, domains, and column overlap — NOT physical table/sheet names alone.
- Google Sheets display labels like "Workbook / Sheet1" are locations; semantics.category is the business entity.
- Prefer clusters aligned with cross_map_intent pairs when provided.
- Assign confidence 0.0-1.0 per cluster; only include clusters you believe are valid matches.
"""

ENTITY_LINKING_USER_TEMPLATE = """Propose cross-connector entity mappings for this enterprise graph.

Graph: {graph_name}

Source bundle (JSON):
{bundle_json}

Return JSON:
{{
  "rationale": "2-4 sentences for a data steward",
  "clusters": [
    {{
      "canonical_name": "Contact",
      "confidence": 0.82,
      "evidence": "Shared contact semantics and email/name columns",
      "entities": [
        {{"member_id": "<uuid>", "entity_id": "<exact entity_id from bundle>"}},
        {{"member_id": "<uuid>", "entity_id": "<exact entity_id from bundle>"}}
      ]
    }}
  ]
}}
"""
