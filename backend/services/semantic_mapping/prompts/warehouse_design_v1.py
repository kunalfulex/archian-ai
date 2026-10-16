"""Versioned prompts for warehouse logical model (LLM)."""

WAREHOUSE_DESIGN_SYSTEM = """You are a senior analytics warehouse architect.
Design a star-schema logical model for BI and analytics (not operational app tables).

Rules:
- Output ONLY valid JSON matching the user schema. No markdown.
- Use conformed dimensions across sources; facts at clear grain with measures.
- Table names: dim_<entity> or fact_<entity> (lowercase snake_case).
- Every table needs a surrogate key column: <table>_key (role: surrogate_key, not null).
- Map attributes from provided source columns only — do not invent columns.
- Do NOT include data quality metrics (missing_values, inconsistencies, case_inconsistency, etc.).
- Do NOT model platform/internal tables (migrations, chat, tokens, usage tracking).
- Prefer 8-25 tables unless the source set is tiny.
- Include grain text per table and a short rationale for the overall model.
"""

WAREHOUSE_DESIGN_USER_TEMPLATE = """Design a warehouse logical model for this enterprise data landscape.

Dialect for types: {dialect}
Modeling style: {modeling_preference}

Source bundle (JSON):
{bundle_json}

Return JSON:
{{
  "model_type": "star",
  "rationale": "2-4 sentences for a business reader",
  "tables": [
    {{
      "name": "dim_customer",
      "table_type": "dimension",
      "grain": "One row per customer",
      "canonical_entity_ids": ["<canonical entity uuid>"],
      "columns": [
        {{"name": "dim_customer_key", "type": "STRING", "role": "surrogate_key", "nullable": false}},
        {{"name": "email", "type": "STRING", "role": "attribute", "nullable": true,
          "source_refs": [{{"member_id": "...", "entity_id": "...", "source_column": "email"}}]}}
      ],
      "foreign_keys": [
        {{"column": "dim_other_key", "references_table": "dim_other", "references_column": "dim_other_key"}}
      ]
    }}
  ],
  "relationships": [
    {{"from_table": "fact_order", "to_table": "dim_customer", "via_column": "dim_customer_key", "cardinality": "many_to_one"}}
  ],
  "excluded_entities": [
    {{"canonical_entity_id": "...", "reason": "platform_table"}}
  ]
}}
"""
