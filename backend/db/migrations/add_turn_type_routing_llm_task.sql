-- Gate 0 turn-family routing (info / general / pipeline)
INSERT INTO llm_task_config (
    task_key, display_name, description, provider_id, model_id,
    temperature, max_tokens, max_concurrent, batch_size
)
VALUES (
    'turn_type_routing',
    'Turn type routing',
    'Gate 0: classify user turn as info, general, or pipeline before S01/classifier',
    'openai',
    'gpt-4o-mini',
    0,
    180,
    5,
    1
)
ON CONFLICT (task_key) DO NOTHING;
