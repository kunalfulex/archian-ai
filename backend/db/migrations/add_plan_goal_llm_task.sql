-- Planner v2 GoalParser LLM task (Phase 4.1)
INSERT INTO llm_task_config (
    task_key, display_name, description, provider_id, model_id,
    temperature, max_tokens, max_concurrent, batch_size
)
VALUES (
    'plan_goal',
    'Plan goal parser',
    'Extract operations and entities from user prompts (no resource names)',
    'openai',
    'gpt-4o-mini',
    0,
    512,
    5,
    1
)
ON CONFLICT (task_key) DO NOTHING;
