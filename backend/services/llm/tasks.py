"""Stable LLM task keys — used in DB llm_task_config and invocation logs."""
from __future__ import annotations

# Routing & orchestration
TURN_TYPE_ROUTING = "turn_type_routing"
SESSION_CONTINUATION = "session_continuation"
PLAN_SCOPE = "plan_scope"
MIGRATION_INTENT_DECOMPOSE = "migration_intent_decompose"
PLAN_BIND_SOURCE_EXTRACT = "plan_bind_source_extract"
PLAN_BIND_DESTINATION = "plan_bind_destination"
PLAN_CLARIFICATION_ENTITY = "plan_clarification_entity"
PLAN_CLARIFICATION_COMPOSE = "plan_clarification_compose"
PLAN_CLARIFICATION_REPLY = "plan_clarification_reply"
PIPELINE_TURN_ROUTING = "pipeline_turn_routing"
DATA_OPS_STEP_ORDER = "data_ops_step_order"
RECOVERY_TOOL_PICK = "recovery_tool_pick"
RECOVERY_PLAN = "recovery_plan"
AGENT_TOOL_PICK = "agent_tool_pick"
GENERAL_CHAT = "general_chat"
GENERAL = "general"

# Extraction & rules
EXTRACTION_RULES = "extraction_rules"
FILE_EXTRACTION = "file_extraction"
RULES_PROPOSER = "rules_proposer"

# Mapping
FIELD_MAPPING = "field_mapping"
SALESFORCE_MAPPING = "salesforce_mapping"
GOOGLE_SHEETS_MAPPING = "google_sheets_mapping"

# SQL
SQL_QUERY_GENERATION = "sql_query_generation"
SQL_CONNECTION_DETECT = "sql_connection_detect"
SQL_TABLE_SELECT = "sql_table_select"
SQL_EXTRACTION = "sql_extraction"

# Salesforce
SALESFORCE_EXTRACTION = "salesforce_extraction"
SALESFORCE_INFO = "salesforce_info"

# Google Sheets
GOOGLE_SHEETS_DETECTION = "google_sheets_detection"
GOOGLE_SHEETS_FORMULA = "google_sheets_formula"

# Enterprise graph
EGA_ENTITY_LINK = "ega_entity_link"
EGA_ENTITY_MAPPING = "ega_entity_mapping"
EGA_LAKE_DESIGN = "ega_lake_design"
EGA_WAREHOUSE_DESIGN = "ega_warehouse_design"

# Auxiliary
ERROR_EXPLANATION = "error_explanation"
PIPELINE_SUMMARY = "pipeline_summary"
ASSESSMENT_NARRATIVE = "assessment_narrative"
SCHEMA_CATEGORIZATION = "schema_categorization"

ALL_TASK_KEYS = (
    TURN_TYPE_ROUTING,
    SESSION_CONTINUATION,
    PLAN_SCOPE,
    MIGRATION_INTENT_DECOMPOSE,
    PLAN_BIND_SOURCE_EXTRACT,
    PLAN_BIND_DESTINATION,
    PLAN_CLARIFICATION_ENTITY,
    PLAN_CLARIFICATION_COMPOSE,
    PLAN_CLARIFICATION_REPLY,
    PIPELINE_TURN_ROUTING,
    DATA_OPS_STEP_ORDER,
    RECOVERY_TOOL_PICK,
    RECOVERY_PLAN,
    AGENT_TOOL_PICK,
    GENERAL_CHAT,
    GENERAL,
    EXTRACTION_RULES,
    FILE_EXTRACTION,
    RULES_PROPOSER,
    FIELD_MAPPING,
    SALESFORCE_MAPPING,
    GOOGLE_SHEETS_MAPPING,
    SQL_QUERY_GENERATION,
    SQL_CONNECTION_DETECT,
    SQL_TABLE_SELECT,
    SQL_EXTRACTION,
    SALESFORCE_EXTRACTION,
    SALESFORCE_INFO,
    GOOGLE_SHEETS_DETECTION,
    GOOGLE_SHEETS_FORMULA,
    EGA_ENTITY_LINK,
    EGA_ENTITY_MAPPING,
    EGA_LAKE_DESIGN,
    EGA_WAREHOUSE_DESIGN,
    ERROR_EXPLANATION,
    PIPELINE_SUMMARY,
    ASSESSMENT_NARRATIVE,
    SCHEMA_CATEGORIZATION,
)
