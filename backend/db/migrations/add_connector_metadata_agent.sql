-- Connector Metadata Agent: scheduled schema sync, jobs, schema graphs

CREATE TABLE IF NOT EXISTS connector_schema_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    schedule_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    schedule_frequency VARCHAR(32),
    schedule_config JSONB NOT NULL DEFAULT '{}',
    schedule_timezone VARCHAR(100),
    sync_mode VARCHAR(32) NOT NULL DEFAULT 'schema_and_categorize',
    graph_scope VARCHAR(32) NOT NULL DEFAULT 'neighborhood',
    next_run_at TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error TEXT,
    last_job_id UUID,
    active_graph_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_connector_schema_schedules_user_connector
    ON connector_schema_schedules(user_id, connector_type);

CREATE INDEX IF NOT EXISTS idx_connector_schema_schedules_next_run
    ON connector_schema_schedules(next_run_at)
    WHERE schedule_enabled = TRUE AND next_run_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS connector_metadata_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    sync_mode VARCHAR(32) NOT NULL DEFAULT 'schema_and_categorize',
    scope VARCHAR(32) NOT NULL DEFAULT 'full',
    options JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    progress JSONB NOT NULL DEFAULT '{}',
    drift_report JSONB NOT NULL DEFAULT '{}',
    result_graph_id UUID,
    error_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_connector_metadata_jobs_user
    ON connector_metadata_jobs(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_connector_metadata_jobs_active
    ON connector_metadata_jobs(user_id, connector_type, status)
    WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS connector_schema_graphs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    job_id UUID REFERENCES connector_metadata_jobs(id) ON DELETE SET NULL,
    graph_scope VARCHAR(32) NOT NULL DEFAULT 'neighborhood',
    graph_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_connector_schema_graphs_connector
    ON connector_schema_graphs(user_id, connector_type, created_at DESC);

-- Soft-delete + drift fingerprints on cached schema tables
ALTER TABLE sql_extracted_schemas
    ADD COLUMN IF NOT EXISTS schema_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS schema_synced_at TIMESTAMPTZ;

ALTER TABLE salesforce_objects
    ADD COLUMN IF NOT EXISTS schema_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE gsheets_extracted_schemas
    ADD COLUMN IF NOT EXISTS schema_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS schema_synced_at TIMESTAMPTZ;
