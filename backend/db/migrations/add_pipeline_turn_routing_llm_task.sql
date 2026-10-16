-- Pipeline turn routing (planning continuation vs job vs fresh) after Gate 0
INSERT INTO llm_task_config (
    task_key, display_name, description, provider_id, model_id,
    temperature, max_tokens, max_concurrent, batch_size
)
VALUES (
    'pipeline_turn_routing',
    'Pipeline turn routing',
    'After Gate 0: route pipeline turn to planning continuation, job continuation, or fresh goal',
    'openai',
    'gpt-4o-mini',
    0,
    150,
    5,
    1
)
ON CONFLICT (task_key) DO NOTHING;
