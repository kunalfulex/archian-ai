-- Replay guard table to prevent bidirectional ping-pong updates.
-- Internal-only store; does not require source/destination schema changes.

CREATE TABLE IF NOT EXISTS sync_replay_guard (
    id UUID PRIMARY KEY,
    flow_pair_id UUID NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('A_TO_B', 'B_TO_A')),
    record_key TEXT NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_flow_id UUID NULL,
    destination_flow_id UUID NULL,
    flow_execution_id UUID NULL
);

-- Main lookup path: opposite direction, recent window, same key + hash.
CREATE INDEX IF NOT EXISTS idx_sync_replay_guard_lookup
    ON sync_replay_guard (flow_pair_id, direction, record_key, payload_hash, processed_at DESC);

-- Retention / cleanup path.
CREATE INDEX IF NOT EXISTS idx_sync_replay_guard_processed_at
    ON sync_replay_guard (processed_at);

COMMENT ON TABLE sync_replay_guard IS
    'Replay guard store to suppress bidirectional bounce-back writes using (flow_pair_id, direction, record_key, payload_hash).';

COMMENT ON COLUMN sync_replay_guard.flow_pair_id IS
    'Primary bidirectional flow id used to group both legs.';

COMMENT ON COLUMN sync_replay_guard.direction IS
    'Direction of the write for the stored hash: A_TO_B or B_TO_A.';

COMMENT ON COLUMN sync_replay_guard.record_key IS
    'Stable record correlation key (e.g., external id / duplicate-check key).';

COMMENT ON COLUMN sync_replay_guard.payload_hash IS
    'SHA-256 hash of normalized mapped outbound payload (business fields only).';
