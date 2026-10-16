import React, { useMemo, useState } from 'react';
import SchemaMapDiagram from './SchemaMapDiagram';
import './ConnectorSchemaGraphView.css';

export default function ConnectorSchemaGraphView({ graph, driftReport, hideSummary = false }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const summary = graph?.summary || {};

  const nodesById = useMemo(() => {
    const m = new Map();
    nodes.forEach((n) => m.set(n.id, n));
    return m;
  }, [nodes]);

  if (!nodes.length && !edges.length) {
    return <p className="schema-graph-empty">No schema graph data yet. Run a schema sync first.</p>;
  }

  return (
    <div className="schema-graph-view">
      {!hideSummary && (
        <div className="schema-graph-summary">
          {summary.as_of && <span>As of {new Date(summary.as_of).toLocaleString()}</span>}
          <span>{summary.node_count ?? nodes.length} objects</span>
          <span>{summary.edge_count ?? edges.length} relationships</span>
          {summary.scope && <span>Scope: {summary.scope}</span>}
        </div>
      )}

      {driftReport && (
        <div className="schema-drift-banner">
          {(driftReport.new_entities?.length > 0 ||
            driftReport.changed_entities?.length > 0 ||
            driftReport.removed_entities?.length > 0) && (
            <p>
              Drift: {driftReport.new_entities?.length || 0} new,{' '}
              {driftReport.changed_entities?.length || 0} changed,{' '}
              {driftReport.removed_entities?.length || 0} removed (soft-deleted)
            </p>
          )}
        </div>
      )}

      <SchemaMapDiagram graph={graph} />

      <button
        type="button"
        className="schema-graph-details-toggle"
        onClick={() => setDetailsOpen((o) => !o)}
        aria-expanded={detailsOpen}
      >
        {detailsOpen ? 'Hide' : 'Show'} object list & relationship details
      </button>

      {detailsOpen && (
        <div className="schema-graph-layout">
          <section className="schema-graph-nodes">
            <h5>Objects / tables</h5>
            <ul>
              {nodes.map((n) => (
                <li
                  key={n.id}
                  className={[
                    'schema-graph-node',
                    n.is_deleted ? 'schema-graph-node--deleted' : '',
                    n.orphan_kind ? `schema-graph-node--${n.orphan_kind}` : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  <strong>{n.label || n.id}</strong>
                  {n.orphan_kind && <span className="schema-graph-tag">{n.orphan_kind}</span>}
                  {n.is_deleted && <span className="schema-graph-tag">removed</span>}
                  {n.gap_severities?.length > 0 && <span className="schema-graph-tag">gap</span>}
                </li>
              ))}
            </ul>
          </section>
          <section className="schema-graph-edges">
            <h5>Relationships</h5>
            {edges.length === 0 ? (
              <p className="schema-graph-muted">No declared relationships in this scope.</p>
            ) : (
              <ul>
                {edges.map((e, i) => {
                  const src = nodesById.get(e.source_id);
                  const tgt = nodesById.get(e.target_id);
                  return (
                    <li
                      key={`${e.source_id}-${e.target_id}-${e.label}-${i}`}
                      className={e.gap_severity ? `schema-graph-edge--${e.gap_severity}` : ''}
                    >
                      <span className="schema-graph-edge-src">{src?.label || e.source_id}</span>
                      <span className="schema-graph-edge-arrow">→</span>
                      <span className="schema-graph-edge-tgt">{tgt?.label || e.target_id}</span>
                      <span className="schema-graph-edge-label">({e.label})</span>
                      {e.gap_severity && <span className="schema-graph-tag">{e.gap_severity}</span>}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
