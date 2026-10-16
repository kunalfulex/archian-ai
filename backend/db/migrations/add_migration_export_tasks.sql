-- Migration: migration_export_tasks table for non-blocking migration results export
-- Purpose: Track background export tasks for migration batch results (success/failed/all).
-- The table is also created via CREATE TABLE IF NOT EXISTS in the API; this file is for documentation and manual runs.

CREATE TABLE IF NOT EXISTS migration_export_tasks (
    id VARCHAR PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    batch_id VARCHAR NOT NULL,
    status_filter VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    file_path TEXT,
    download_token VARCHAR(64),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_migration_export_tasks_user_id ON migration_export_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_migration_export_tasks_batch_id ON migration_export_tasks(batch_id);
CREATE INDEX IF NOT EXISTS idx_migration_export_tasks_status ON migration_export_tasks(status);
