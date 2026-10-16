-- Migration: Retrieval audit log for pipeline learning / RAG observability
-- Purpose: Super-admin metrics — who retrieved what, when, and which chunks scored highest

CREATE TABLE IF NOT EXISTS learning_retrieval_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID,
    caller VARCHAR(64) NOT NULL,
    query_text TEXT,
    stage VARCHAR(32),
    chunk_type VARCHAR(64),
    source_type VARCHAR(50),
    destination_type VARCHAR(50),
    result_count INT NOT NULL DEFAULT 0,
    chunk_ids JSONB NOT NULL DEFAULT '[]',
    scores JSONB NOT NULL DEFAULT '[]',
    chunk_types JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_retrieval_log_created
    ON learning_retrieval_log(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_learning_retrieval_log_caller
    ON learning_retrieval_log(caller, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_learning_retrieval_log_user
    ON learning_retrieval_log(user_id, created_at DESC);
