-- Self-learning labeling: entity semantics + background labeling jobs

CREATE TABLE IF NOT EXISTS connector_entity_semantics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_instance_id VARCHAR(128) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    entity_key VARCHAR(512) NOT NULL,
    entity_display_name VARCHAR(512),
    semantics JSONB,
    labeling_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    schema_hash VARCHAR(64),
    semantics_version INTEGER NOT NULL DEFAULT 1,
    labeled_at TIMESTAMPTZ,
    error_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT connector_entity_semantics_status_check
        CHECK (labeling_status IN ('pending', 'processing', 'ready', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_connector_entity_semantics_unique
    ON connector_entity_semantics(user_id, connector_type, connector_instance_id, entity_key);

CREATE INDEX IF NOT EXISTS idx_connector_entity_semantics_connector
    ON connector_entity_semantics(user_id, connector_type, connector_instance_id);

CREATE INDEX IF NOT EXISTS idx_connector_entity_semantics_status
    ON connector_entity_semantics(user_id, labeling_status)
    WHERE labeling_status IN ('pending', 'processing', 'failed');

CREATE TABLE IF NOT EXISTS connector_labeling_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    connector_instance_id VARCHAR(128) NOT NULL,
    job_type VARCHAR(32) NOT NULL DEFAULT 'entity_batch',
    entity_keys JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    progress JSONB NOT NULL DEFAULT '{}',
    error_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT connector_labeling_jobs_status_check
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_connector_labeling_jobs_user
    ON connector_labeling_jobs(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_connector_labeling_jobs_active
    ON connector_labeling_jobs(user_id, connector_type, connector_instance_id, status)
    WHERE status IN ('queued', 'running');
