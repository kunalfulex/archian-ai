-- User-configurable retention window (effective cap enforced in application by plan).
ALTER TABLE users
ADD COLUMN IF NOT EXISTS data_retention_days INTEGER NOT NULL DEFAULT 30;

-- Set when row-level extraction detail has been removed per retention policy; metadata row remains.
ALTER TABLE extraction_results
ADD COLUMN IF NOT EXISTS detail_purged_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_extraction_results_user_detail_purge
ON extraction_results (user_id)
WHERE detail_purged_at IS NULL;

-- Set when row-level migration records have been removed per retention policy; batch summary row remains.
ALTER TABLE migration_batches
ADD COLUMN IF NOT EXISTS detail_purged_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_migration_batches_user_detail_purge
ON migration_batches (user_id)
WHERE detail_purged_at IS NULL;
