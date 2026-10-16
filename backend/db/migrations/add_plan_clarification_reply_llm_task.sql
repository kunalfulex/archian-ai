-- LLM task: classify whether a user message answers an open planner clarification
INSERT INTO llm_task_config (
    task_key, display_name, description, provider_id, model_id,
    temperature, max_tokens, max_concurrent, batch_size
)
VALUES (
    'plan_clarification_reply',
    'Plan clarification reply',
    'Decide if user message continues an open planner clarification thread',
    'openai',
    'gpt-4o-mini',
    0,
    120,
    5,
    1
)
ON CONFLICT (task_key) DO NOTHING;
