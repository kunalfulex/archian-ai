-- Pending Google Sheets chat actions (upsert confirmation, etc.)
CREATE TABLE IF NOT EXISTS chat_pending_sheet_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    action_type VARCHAR(64) NOT NULL DEFAULT 'upsert_confirm',
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours')
);

CREATE INDEX IF NOT EXISTS idx_chat_pending_sheet_actions_user
    ON chat_pending_sheet_actions(user_id, expires_at DESC);

-- One pending row per user (app deletes by user_id before insert).
-- Cannot use WHERE expires_at > NOW() — NOW() is not IMMUTABLE in PostgreSQL index predicates.
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_pending_sheet_actions_user_unique
    ON chat_pending_sheet_actions(user_id);
