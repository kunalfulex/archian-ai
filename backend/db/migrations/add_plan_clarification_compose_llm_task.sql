-- PLAN_CLARIFICATION_COMPOSE: contextual clarification question from structured gap context
INSERT INTO llm_task_config (
    task_key, display_name, description, provider_id, model_id,
    temperature, max_tokens, max_concurrent, batch_size
)
VALUES (
    'plan_clarification_compose',
    'Plan clarification compose',
    'Generate contextual clarification questions for planner v2 gaps',
    'openai',
    'gpt-4o-mini',
    0,
    280,
    5,
    1
)
ON CONFLICT (task_key) DO NOTHING;
