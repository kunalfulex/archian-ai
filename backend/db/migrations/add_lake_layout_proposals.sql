-- Lake layout proposals (bronze + silver medallion) for Enterprise Graph.

CREATE TABLE IF NOT EXISTS lake_layout_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id UUID NOT NULL REFERENCES enterprise_graphs(id) ON DELETE CASCADE,
    run_id UUID REFERENCES enterprise_graph_runs(id) ON DELETE SET NULL,
    version INT NOT NULL DEFAULT 1,
    dialect VARCHAR(32) NOT NULL DEFAULT 'snowflake',
    logical_model JSONB NOT NULL DEFAULT '{}',
    source_to_lake_map JSONB NOT NULL DEFAULT '{}',
    load_spec JSONB NOT NULL DEFAULT '[]',
    ddl_text TEXT,
    rationale TEXT,
    gaps JSONB NOT NULL DEFAULT '[]',
    load_status JSONB,
    status VARCHAR(24) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lake_layout_proposals_graph
    ON lake_layout_proposals(graph_id, version DESC);
