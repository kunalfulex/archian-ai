-- PLAN_SCOPE: slim operation-scope LLM (pipeline_ops + data_operations only)
INSERT INTO llm_task_config (
    task_key, display_name, description, provider_id, model_id,
    temperature, max_tokens, max_concurrent, batch_size
)
VALUES (
    'plan_scope',
    'Plan scope',
    'Decide pipeline scope: extract, data_ops, map, migrate (no resource names)',
    'openai',
    'gpt-4o-mini',
    0,
    256,
    5,
    1
)
ON CONFLICT (task_key) DO NOTHING;
