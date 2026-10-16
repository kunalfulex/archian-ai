"""Versioned prompts for medallion lake layout (LLM-first)."""

LAKE_DESIGN_SYSTEM = """You are a senior data lake architect designing a medallion layout (bronze landing + silver conformed views).

Rules:
- Output ONLY valid JSON. No markdown.
- Bronze: one raw landing table per assessed source entity (use exact member_id + entity_id from bundle).
- Silver: conformed views that UNION or join bronze sources mapped to the same canonical business concept.
- Use semantic_mappings and canonical_entities when present; align silver views to canonical clusters.
- Table/view names: lowercase snake_case (raw__<member_slug>__<entity_slug>, stg__<domain>__<name>).
- Columns must come from provided source columns only.
- Include lineage columns on bronze: _archian_source_member_id, _archian_source_entity_id, _archian_loaded_at.
- Do NOT invent sources not in the bundle.
"""

LAKE_DESIGN_USER_TEMPLATE = """Design a lake logical model for this enterprise graph.

Dialect: {dialect}
Bronze schema: {bronze_schema}
Silver schema: {silver_schema}

Source bundle (JSON):
{bundle_json}

Return JSON:
{{
  "rationale": "2-4 sentences",
  "bronze_tables": [
    {{
      "name": "raw__test_db__users",
      "source_member_id": "<uuid>",
      "source_entity_id": "users",
      "label": "users",
      "columns": [{{"name": "email", "type": "VARCHAR", "source_column": "email"}}]
    }}
  ],
  "silver_views": [
    {{
      "name": "stg__party__contact",
      "canonical_entity_id": "<uuid or null>",
      "canonical_name": "Contact",
      "source_bronze_tables": ["raw__test_db__users", "raw__archian_ai__salesforce_contacts"],
      "branch_select_lists": [
        "SELECT email, first_name, '<member_id>' AS _archian_source_member_id, 'users' AS _archian_source_entity_id FROM {{bronze}}.raw__test_db__users"
      ]
    }}
  ]
}}
"""
