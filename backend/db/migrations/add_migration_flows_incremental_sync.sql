-- Incremental sync (no CDC): watermark + fingerprint on migration_flows
-- Safe to run if columns already exist (IF NOT EXISTS).

ALTER TABLE migration_flows
ADD COLUMN IF NOT EXISTS incremental_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS incremental_cursor_field TEXT,
ADD COLUMN IF NOT EXISTS incremental_high_watermark TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS incremental_high_watermark_text TEXT,
ADD COLUMN IF NOT EXISTS incremental_last_extraction_success_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS incremental_query_fingerprint TEXT;

COMMENT ON COLUMN migration_flows.incremental_sync_enabled IS 'When true, use watermark-based incremental extraction for SF/SQL';
COMMENT ON COLUMN migration_flows.incremental_cursor_field IS 'Cursor field (SF API name or SQL column), e.g. LastModifiedDate / updated_at';
COMMENT ON COLUMN migration_flows.incremental_high_watermark IS 'UTC high-water from MAX(cursor) after successful extraction';
COMMENT ON COLUMN migration_flows.incremental_high_watermark_text IS 'Optional SF datetime string if timestamptz is lossy';
COMMENT ON COLUMN migration_flows.incremental_last_extraction_success_at IS 'Last extraction success that updated incremental state (Phase 0 B: not migration)';
COMMENT ON COLUMN migration_flows.incremental_query_fingerprint IS 'Hash of saved sql_query/SOQL; mismatch clears watermark';
