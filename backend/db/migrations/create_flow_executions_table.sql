-- Flow execution timeline: one row per run (manual, scheduled, etc.)
-- Links a migration flow to optional extraction job + migration batch for drill-down.

CREATE TABLE IF NOT EXISTS flow_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- FK to migration_flows.id (must match column type: UUID)
    flow_id UUID NOT NULL,

    -- manual | scheduled | (future: api, retry, …)
    trigger_type VARCHAR(32) NOT NULL DEFAULT 'manual',

    -- running | succeeded | failed | cancelled | pending_upload | …
    status VARCHAR(32) NOT NULL DEFAULT 'running',

    -- extraction_results.job_id (UNIQUE); nullable until extraction row exists
    extraction_job_id VARCHAR UNIQUE REFERENCES extraction_results(job_id) ON DELETE SET NULL,

    -- migration_batches.id when migration ran
    migration_batch_id VARCHAR REFERENCES migration_batches(id) ON DELETE SET NULL,

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    error_summary TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_flow_executions_flow_id FOREIGN KEY (flow_id) REFERENCES migration_flows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_flow_executions_flow_started
    ON flow_executions(flow_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_flow_executions_user_flow
    ON flow_executions(user_id, flow_id);

CREATE INDEX IF NOT EXISTS idx_flow_executions_extraction_job
    ON flow_executions(extraction_job_id)
    WHERE extraction_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_flow_executions_migration_batch
    ON flow_executions(migration_batch_id)
    WHERE migration_batch_id IS NOT NULL;

COMMENT ON TABLE flow_executions IS 'Per-run history for a saved flow (manual vs scheduled); links to extraction + migration artifacts.';
COMMENT ON COLUMN flow_executions.trigger_type IS 'How the run was started: manual, scheduled, …';
COMMENT ON COLUMN flow_executions.flow_id IS 'FK to migration_flows.id — saved flow this execution belongs to.';
COMMENT ON COLUMN flow_executions.extraction_job_id IS 'extraction_results.job_id for this run';
COMMENT ON COLUMN flow_executions.migration_batch_id IS 'migration_batches.id when migration was created for this run';
