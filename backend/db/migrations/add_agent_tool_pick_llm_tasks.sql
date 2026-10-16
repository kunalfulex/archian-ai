-- Agent Runtime: LLM task keys for tool picking and recovery
INSERT INTO llm_task_config (
    task_key, display_name, description, provider_id, model_id,
    temperature, max_tokens, max_concurrent, batch_size
)
VALUES
    (
        'recovery_tool_pick',
        'Recovery action pick',
        'Pick recovery action after partial/failed migration',
        'openai',
        'gpt-4o-mini',
        0,
        500,
        5,
        1
    ),
    (
        'agent_tool_pick',
        'Agent tool pick',
        'Pick next tool from agent registry during plan execution',
        'openai',
        'gpt-4o-mini',
        0,
        500,
        5,
        1
    )
ON CONFLICT (task_key) DO NOTHING;
