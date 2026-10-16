-- Enterprise Graph: multi-connector landscape, runs, semantic mappings, warehouse proposals

CREATE TABLE IF NOT EXISTS enterprise_graphs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    settings JSONB NOT NULL DEFAULT '{}',
    latest_run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_enterprise_graphs_user
    ON enterprise_graphs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS enterprise_graph_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id UUID NOT NULL REFERENCES enterprise_graphs(id) ON DELETE CASCADE,
    connector_type VARCHAR(32) NOT NULL,
    connector_ref JSONB NOT NULL DEFAULT '{}',
    role VARCHAR(32) NOT NULL DEFAULT 'source',
    display_name VARCHAR(255) NOT NULL DEFAULT '',
    sort_order INT NOT NULL DEFAULT 0,
    last_schema_sync_job_id UUID,
    last_assessment_report_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_enterprise_graph_member UNIQUE (graph_id, connector_type, connector_ref)
);

CREATE INDEX IF NOT EXISTS idx_enterprise_graph_members_graph
    ON enterprise_graph_members(graph_id, sort_order);

CREATE TABLE IF NOT EXISTS enterprise_graph_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id UUID NOT NULL REFERENCES enterprise_graphs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_type VARCHAR(32) NOT NULL DEFAULT 'assessment',
    depth VARCHAR(16) NOT NULL DEFAULT 'partial',
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    progress JSONB NOT NULL DEFAULT '{}',
    member_jobs JSONB NOT NULL DEFAULT '[]',
    report_snapshot JSONB NOT NULL DEFAULT '{}',
    error_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_enterprise_graph_runs_graph
    ON enterprise_graph_runs(graph_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_enterprise_graph_runs_status
    ON enterprise_graph_runs(status) WHERE status IN ('queued', 'running');

ALTER TABLE enterprise_graphs
    ADD CONSTRAINT fk_enterprise_graphs_latest_run
    FOREIGN KEY (latest_run_id) REFERENCES enterprise_graph_runs(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS canonical_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id UUID NOT NULL REFERENCES enterprise_graphs(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    domain_tags JSONB NOT NULL DEFAULT '[]',
    attributes JSONB NOT NULL DEFAULT '{}',
    source_lineage JSONB NOT NULL DEFAULT '{}',
    version INT NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_canonical_entities_graph
    ON canonical_entities(graph_id, name);

CREATE TABLE IF NOT EXISTS semantic_entity_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id UUID NOT NULL REFERENCES enterprise_graphs(id) ON DELETE CASCADE,
    canonical_entity_id UUID REFERENCES canonical_entities(id) ON DELETE SET NULL,
    source_member_id UUID NOT NULL REFERENCES enterprise_graph_members(id) ON DELETE CASCADE,
    source_entity_id VARCHAR(255) NOT NULL,
    confidence NUMERIC(4, 3),
    mapping_kind VARCHAR(32) NOT NULL DEFAULT 'ai_suggested',
    evidence JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(16) NOT NULL DEFAULT 'suggested',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_semantic_entity_mapping UNIQUE (graph_id, source_member_id, source_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_entity_mappings_graph
    ON semantic_entity_mappings(graph_id, status);

CREATE TABLE IF NOT EXISTS warehouse_schema_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id UUID NOT NULL REFERENCES enterprise_graphs(id) ON DELETE CASCADE,
    run_id UUID REFERENCES enterprise_graph_runs(id) ON DELETE SET NULL,
    version INT NOT NULL DEFAULT 1,
    dialect VARCHAR(32) NOT NULL DEFAULT 'logical',
    logical_model JSONB NOT NULL DEFAULT '{}',
    source_to_warehouse_map JSONB NOT NULL DEFAULT '{}',
    ddl_text TEXT,
    rationale TEXT,
    gaps JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_warehouse_schema_proposals_graph
    ON warehouse_schema_proposals(graph_id, version DESC);
