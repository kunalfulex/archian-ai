-- Canonical mapping + migrate context per conversation + extraction job
CREATE TABLE IF NOT EXISTS conversation_migration_contexts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    conversation_id UUID NOT NULL,
    extraction_job_id UUID NOT NULL,
    mapping_id TEXT NOT NULL,
    mapping_version INT NOT NULL DEFAULT 1,
    destination TEXT NOT NULL,
    destination_name TEXT,
    mapping_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, conversation_id, extraction_job_id)
);

CREATE INDEX IF NOT EXISTS idx_cmc_conversation_job
    ON conversation_migration_contexts (conversation_id, extraction_job_id);
