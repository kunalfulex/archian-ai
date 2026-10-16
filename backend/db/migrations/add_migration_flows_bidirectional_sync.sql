-- Bidirectional sync: parent flow links to hidden reverse-leg child via reverse_flow_id.
-- Child rows set parent_flow_id -> ON DELETE CASCADE removes child when parent is deleted.

ALTER TABLE migration_flows
ADD COLUMN IF NOT EXISTS bidirectional_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS parent_flow_id UUID NULL REFERENCES migration_flows(id) ON DELETE CASCADE,
ADD COLUMN IF NOT EXISTS reverse_flow_id UUID NULL;

COMMENT ON COLUMN migration_flows.bidirectional_sync_enabled IS 'When true, manual/scheduled reuse also runs reverse_flow_id leg after parent leg';
COMMENT ON COLUMN migration_flows.parent_flow_id IS 'Set on reverse-leg flow only; points to primary migration_flows row';
COMMENT ON COLUMN migration_flows.reverse_flow_id IS 'Set on parent only; points to reverse-leg migration_flows.id (no FK to avoid insert cycles)';

CREATE INDEX IF NOT EXISTS idx_migration_flows_parent_flow_id
ON migration_flows(parent_flow_id)
WHERE parent_flow_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_migration_flows_reverse_flow_lookup
ON migration_flows(reverse_flow_id)
WHERE reverse_flow_id IS NOT NULL;
