-- LLM model layer: provider catalog, models, per-task config, invocation metrics.
-- API keys remain in environment (OPENAI_API_KEY, ANTHROPIC_API_KEY).

CREATE TABLE IF NOT EXISTS llm_providers (
    id VARCHAR(32) PRIMARY KEY,
    display_name VARCHAR(128) NOT NULL,
    module_type VARCHAR(64) NOT NULL,
    supports_streaming BOOLEAN NOT NULL DEFAULT TRUE,
    supports_json BOOLEAN NOT NULL DEFAULT TRUE,
    supports_vision BOOLEAN NOT NULL DEFAULT FALSE,
    env_key_name VARCHAR(64) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS llm_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id VARCHAR(32) NOT NULL REFERENCES llm_providers(id),
    model_id VARCHAR(128) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    context_window INTEGER,
    max_output_tokens INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider_id, model_id)
);

CREATE TABLE IF NOT EXISTS llm_task_config (
    task_key VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(128) NOT NULL,
    description TEXT,
    provider_id VARCHAR(32) NOT NULL REFERENCES llm_providers(id),
    model_id VARCHAR(128) NOT NULL,
    fallback_provider_id VARCHAR(32) REFERENCES llm_providers(id),
    fallback_model_id VARCHAR(128),
    temperature REAL DEFAULT 0,
    max_tokens INTEGER,
    max_concurrent INTEGER DEFAULT 5,
    batch_size INTEGER DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by UUID REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS llm_task_config_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_key VARCHAR(64) NOT NULL,
    provider_id VARCHAR(32) NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    fallback_provider_id VARCHAR(32),
    fallback_model_id VARCHAR(128),
    temperature REAL,
    max_tokens INTEGER,
    updated_by UUID REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_task_config_history_task_key
    ON llm_task_config_history(task_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS llm_invocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_key VARCHAR(64) NOT NULL,
    provider_id VARCHAR(32) NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    latency_ms INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    estimated_cost_usd NUMERIC(12, 6),
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_code VARCHAR(64),
    json_parse_ok BOOLEAN,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    job_id VARCHAR(255),
    conversation_id VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_invocations_task_created
    ON llm_invocations(task_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_llm_invocations_provider_model_created
    ON llm_invocations(provider_id, model_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_llm_invocations_created_at
    ON llm_invocations(created_at DESC);

-- Seed providers
INSERT INTO llm_providers (id, display_name, module_type, supports_streaming, supports_json, supports_vision, env_key_name)
VALUES
    ('openai', 'OpenAI', 'openai_chat', TRUE, TRUE, TRUE, 'OPENAI_API_KEY'),
    ('anthropic', 'Anthropic (Claude)', 'anthropic_messages', TRUE, TRUE, TRUE, 'ANTHROPIC_API_KEY')
ON CONFLICT (id) DO NOTHING;

-- Seed models
INSERT INTO llm_models (provider_id, model_id, display_name, context_window, max_output_tokens)
VALUES
    ('openai', 'gpt-4o-mini', 'GPT-4o mini', 128000, 16384),
    ('openai', 'gpt-4o', 'GPT-4o', 128000, 16384),
    ('openai', 'gpt-4', 'GPT-4', 8192, 8192),
    ('openai', 'text-embedding-3-small', 'Embedding 3 Small', NULL, NULL),
    ('anthropic', 'claude-sonnet-4-20250514', 'Claude Sonnet 4', 200000, 8192),
    ('anthropic', 'claude-3-5-haiku-20241022', 'Claude 3.5 Haiku', 200000, 8192)
ON CONFLICT (provider_id, model_id) DO NOTHING;

-- Seed task config (defaults mirror current codebase)
INSERT INTO llm_task_config (task_key, display_name, description, provider_id, model_id, temperature, max_tokens, max_concurrent, batch_size)
VALUES
    ('classification', 'Intent classification', 'Routes architect requests to tools', 'openai', 'gpt-4o-mini', 0.1, 1000, 10, 1),
    ('extraction_rules', 'Extraction / cleanup rules', 'Generates filter, dedupe, transform rules', 'openai', 'gpt-4o-mini', 0, 4096, 5, 1),
    ('field_mapping', 'Field mapping', 'Maps source fields to destination schema', 'openai', 'gpt-4o-mini', 0, 4096, 5, 1),
    ('sql_query_generation', 'SQL query generation', 'Generates SQL from natural language', 'openai', 'gpt-4o', 0, 4096, 5, 1),
    ('sql_connection_detect', 'SQL connection detection', 'Detects DB connection from prompt', 'openai', 'gpt-4o', 0, 500, 10, 1),
    ('sql_table_select', 'SQL table selection', 'Picks relevant tables for context', 'openai', 'gpt-4o-mini', 0, 500, 10, 1),
    ('sql_extraction', 'SQL structured extraction', 'Extracts structured data from SQL results', 'openai', 'gpt-4o-mini', 0, 4096, 3, 1),
    ('salesforce_mapping', 'Salesforce field mapping', 'SF architect mapping calls', 'openai', 'gpt-4o-mini', 0, 4096, 5, 1),
    ('salesforce_extraction', 'Salesforce extraction', 'SF data extraction analysis', 'openai', 'gpt-4o-mini', 0, 4096, 3, 1),
    ('salesforce_info', 'Salesforce info / analyze', 'Schema and object Q&A', 'openai', 'gpt-4o-mini', 0.2, 2000, 5, 1),
    ('general_chat', 'General assistant chat', 'Streaming general AI responses', 'openai', 'gpt-4', 0.8, 1000, 5, 1),
    ('data_ops_step_order', 'Data ops step ordering', 'Orders transform/filter/dedupe steps', 'openai', 'gpt-4o-mini', 0, 200, 10, 1),
    ('ega_entity_link', 'EGA entity linking', 'Cross-connector entity match LLM', 'openai', 'gpt-4o-mini', 0.1, 400, 3, 5),
    ('ega_warehouse_design', 'EGA warehouse design', 'Warehouse table design LLM', 'openai', 'gpt-4o-mini', 0.1, 4096, 2, 1),
    ('error_explanation', 'Error explanation', 'User-friendly error messages', 'openai', 'gpt-4o-mini', 0.2, 400, 10, 1),
    ('pipeline_summary', 'Pipeline narrative', 'Post-extract pipeline summary', 'openai', 'gpt-4o-mini', 0.3, 800, 5, 1),
    ('assessment_narrative', 'Assessment report narrative', 'Data assessment synthesis', 'openai', 'gpt-4o-mini', 0.3, 2000, 2, 1),
    ('schema_categorization', 'Schema categorization', 'SQL/Sheets schema labeling', 'openai', 'gpt-4o', 0, 2048, 3, 10),
    ('google_sheets_detection', 'Google Sheets detection', 'Detect spreadsheet from prompt', 'openai', 'gpt-4o-mini', 0.1, 800, 10, 1),
    ('google_sheets_mapping', 'Google Sheets mapping', 'Sheets field mapping', 'openai', 'gpt-4o-mini', 0, 4096, 5, 1),
    ('google_sheets_formula', 'Google Sheets formula', 'Formula generation', 'openai', 'gpt-4o-mini', 0, 1000, 5, 1),
    ('file_extraction', 'File extraction', 'CSV/PDF/image structured extract', 'openai', 'gpt-4o-mini', 0, 4096, 3, 1),
    ('rules_proposer', 'Post-extract rules', 'Proposes cleanup rules after extract', 'openai', 'gpt-4o-mini', 0, 2048, 5, 1),
    ('general', 'General / legacy', 'Fallback when task not specified', 'openai', 'gpt-4o-mini', 0, 4096, 5, 1)
ON CONFLICT (task_key) DO NOTHING;
