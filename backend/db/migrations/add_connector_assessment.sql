-- Connector data assessment jobs and reports (no customer row storage)

CREATE TABLE IF NOT EXISTS connector_assessment_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    scope VARCHAR(16) NOT NULL DEFAULT 'standard',
    entity_list JSONB,
    options JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    progress JSONB NOT NULL DEFAULT '{}',
    checkpoint JSONB NOT NULL DEFAULT '{}',
    rate_limit_until TIMESTAMPTZ,
    error_summary TEXT,
    report_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_connector_assessment_jobs_user
    ON connector_assessment_jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_connector_assessment_jobs_status
    ON connector_assessment_jobs(status) WHERE status IN ('queued', 'running', 'paused');

CREATE TABLE IF NOT EXISTS connector_assessment_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES connector_assessment_jobs(id) ON DELETE SET NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    report_version INTEGER NOT NULL DEFAULT 1,
    is_partial BOOLEAN NOT NULL DEFAULT FALSE,
    scores JSONB NOT NULL DEFAULT '{}',
    sections JSONB NOT NULL DEFAULT '{}',
    entities JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_connector_assessment_reports_user
    ON connector_assessment_reports(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_connector_assessment_reports_connector
    ON connector_assessment_reports(user_id, connector_type, created_at DESC);

ALTER TABLE connector_assessment_jobs
    ADD CONSTRAINT fk_connector_assessment_jobs_report
    FOREIGN KEY (report_id) REFERENCES connector_assessment_reports(id) ON DELETE SET NULL;
