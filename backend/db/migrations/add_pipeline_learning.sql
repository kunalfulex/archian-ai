-- Migration: Pipeline learning (feedback loop + pgvector)
-- Purpose: Store pipeline runs, stage corrections, and embedding chunks for RAG retrieval

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES chat_conversations(id) ON DELETE SET NULL,
    source_type VARCHAR(50),
    destination_type VARCHAR(50),
    source_fingerprint VARCHAR(64),
    latest_job_id VARCHAR,
    latest_mapping_message_id UUID,
    latest_migration_batch_id VARCHAR,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_user_id ON pipeline_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_conversation_id ON pipeline_runs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_user_updated ON pipeline_runs(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS stage_corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_run_id UUID NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stage VARCHAR(32) NOT NULL,
    stage_version INTEGER NOT NULL DEFAULT 1,
    ai_snapshot JSONB,
    final_snapshot JSONB,
    diff JSONB,
    context JSONB,
    outcome JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stage_corrections_pipeline ON stage_corrections(pipeline_run_id, stage);
CREATE INDEX IF NOT EXISTS idx_stage_corrections_user_stage ON stage_corrections(user_id, stage, created_at DESC);

CREATE TABLE IF NOT EXISTS learning_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pipeline_run_id UUID REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    stage VARCHAR(32) NOT NULL,
    chunk_type VARCHAR(64) NOT NULL,
    content_text TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_chunks_user_stage ON learning_chunks(user_id, stage);
CREATE INDEX IF NOT EXISTS idx_learning_chunks_pipeline ON learning_chunks(pipeline_run_id);

-- HNSW index for cosine similarity (requires pgvector 0.5+)
CREATE INDEX IF NOT EXISTS idx_learning_chunks_embedding_hnsw
    ON learning_chunks
    USING hnsw (embedding vector_cosine_ops);
