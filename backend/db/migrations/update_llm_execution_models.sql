-- Use larger-context models for SQL/SOQL generation (avoid gpt-4 8192 overflow on wide schemas).
UPDATE llm_task_config
SET model_id = 'gpt-4o',
    max_tokens = 4096,
    updated_at = NOW(),
    notes = COALESCE(notes, '') || ' Upgraded from gpt-4 for large schema prompts.'
WHERE task_key IN ('sql_query_generation', 'sql_connection_detect');

UPDATE llm_task_config
SET max_tokens = 4096,
    updated_at = NOW()
WHERE task_key = 'salesforce_extraction';
