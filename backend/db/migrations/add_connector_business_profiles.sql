-- Connector business profiles (AI-owned purpose + user description)

CREATE TABLE IF NOT EXISTS connector_business_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_instance_id VARCHAR(128) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    display_name VARCHAR(512),
    user_description TEXT,
    ai_profile JSONB,
    profile_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    profile_version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT connector_business_profiles_status_check
        CHECK (profile_status IN ('pending', 'processing', 'ready', 'stale', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_connector_business_profiles_unique
    ON connector_business_profiles(user_id, connector_type, connector_instance_id);

CREATE INDEX IF NOT EXISTS idx_connector_business_profiles_user
    ON connector_business_profiles(user_id, connector_type);
