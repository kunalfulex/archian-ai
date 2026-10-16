    -- Universal REST API connector tables

    CREATE TABLE IF NOT EXISTS rest_api_connectors (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        base_url TEXT NOT NULL,
        platform_slug VARCHAR(100),
        spec_url TEXT,
        auth_type VARCHAR(50) NOT NULL DEFAULT 'none',   -- none | api_key_header | api_key_query | bearer | basic | oauth2
        auth_config_encrypted TEXT,                       -- encrypted JSON blob: {key, value} / {username, password} / {access_token, refresh_token}
        discovery_source VARCHAR(50),                     -- openapi_spec | web_search | html_scrape | manual
        analysis_status VARCHAR(50) NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed
        analysis_error TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_rest_api_connectors_user
        ON rest_api_connectors(user_id, created_at DESC);

    CREATE INDEX IF NOT EXISTS idx_rest_api_connectors_user_active
        ON rest_api_connectors(user_id, is_active);


    CREATE TABLE IF NOT EXISTS rest_api_endpoints (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        connector_id UUID NOT NULL REFERENCES rest_api_connectors(id) ON DELETE CASCADE,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        path VARCHAR(500) NOT NULL,
        method VARCHAR(10) NOT NULL DEFAULT 'GET',
        operation_id VARCHAR(255),
        summary TEXT,
        category VARCHAR(100),                  -- AI-assigned: contacts | deals | products | etc.
        request_schema JSONB,                   -- JSON Schema for request body (POST/PUT endpoints)
        response_schema JSONB,                  -- JSON Schema for response
        pagination_strategy VARCHAR(50),        -- page_number | cursor | offset | link_header | none
        pagination_config JSONB,               -- strategy-specific config (param names, page size, etc.)
        sample_data JSONB,                      -- sample response captured during discovery
        is_writable BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_rest_api_endpoints_connector
        ON rest_api_endpoints(connector_id, created_at DESC);

    CREATE INDEX IF NOT EXISTS idx_rest_api_endpoints_connector_category
        ON rest_api_endpoints(connector_id, category);
