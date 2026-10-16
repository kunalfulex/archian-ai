-- Phase 3: execution evidence for self-learning label refinement.

CREATE TABLE IF NOT EXISTS label_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type VARCHAR(32) NOT NULL,
    pipeline_run_id UUID,
    conversation_id VARCHAR(128),
    connector_type VARCHAR(32),
    connector_instance_id VARCHAR(128),
    connector_ref JSONB NOT NULL DEFAULT '{}',
    entity_key VARCHAR(512),
    entity_display_name VARCHAR(512),
    prompt_excerpt TEXT,
    user_phrase TEXT,
    context JSONB NOT NULL DEFAULT '{}',
    outcome JSONB,
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_label_evidence_pending
    ON label_evidence(user_id, connector_type, connector_instance_id, created_at DESC)
    WHERE processed = FALSE;

CREATE INDEX IF NOT EXISTS idx_label_evidence_run
    ON label_evidence(pipeline_run_id)
    WHERE pipeline_run_id IS NOT NULL;
