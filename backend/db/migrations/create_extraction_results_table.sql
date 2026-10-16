-- Migration: Create extraction_results table
-- Purpose: Store all extraction versions for preview restoration and audit trail
-- Date: Phase 2 Implementation

-- Create extraction_results table
CREATE TABLE IF NOT EXISTS extraction_results (
    id VARCHAR PRIMARY KEY,
    job_id VARCHAR UNIQUE NOT NULL,
    parent_job_id VARCHAR,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES chat_conversations(id) ON DELETE SET NULL,
    
    -- Source information
    source_type VARCHAR(50),
    source_name VARCHAR(255),
    source_identifier VARCHAR(500),
    
    -- Extraction data
    extracted_data JSONB NOT NULL,
    extraction_goals TEXT,
    document_type VARCHAR(255),
    document_description TEXT,
    
    -- Metadata
    record_count INTEGER,
    extraction_version INTEGER DEFAULT 1,
    has_cleanup_rules BOOLEAN DEFAULT FALSE,
    has_transformation_rules BOOLEAN DEFAULT FALSE,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_extraction_results_user_id 
    ON extraction_results(user_id);

CREATE INDEX IF NOT EXISTS idx_extraction_results_job_id 
    ON extraction_results(job_id);

CREATE INDEX IF NOT EXISTS idx_extraction_results_parent_job_id 
    ON extraction_results(parent_job_id);

CREATE INDEX IF NOT EXISTS idx_extraction_results_conversation_id 
    ON extraction_results(conversation_id);

CREATE INDEX IF NOT EXISTS idx_extraction_results_user_created 
    ON extraction_results(user_id, created_at DESC);

-- Index for querying extraction chains
CREATE INDEX IF NOT EXISTS idx_extraction_results_parent_chain 
    ON extraction_results(parent_job_id, extraction_version);

