import React, { useMemo, useState } from 'react';
import ConnectorSchemaGraphView from './ConnectorSchemaGraphView';
import './AssessmentDashboard.css';

/**
 * Enterprise graph Schema Map: canonical ERD with optional per-member overlay.
 */
export default function EnterpriseCanonicalSchemaView({ schemaMap, members = [] }) {
  const canonicalGraph = schemaMap?.canonical_graph;
  const overlays = schemaMap?.per_member_overlays || {};
  const overlayKeys = Object.keys(overlays);

  const [overlayMemberId, setOverlayMemberId] = useState('');

  const displayGraph = useMemo(() => {
    if (!overlayMemberId || !overlays[overlayMemberId]?.graph) {
      return canonicalGraph;
    }
    const overlay = overlays[overlayMemberId].graph;
    const canonNodes = canonicalGraph?.nodes || [];
    const canonIds = new Set(canonNodes.map((n) => n.id));
    const overlayNodes = (overlay.nodes || []).map((n) => ({
      ...n,
      id: `overlay:${n.id}`,
      label: `${n.label || n.id} (source)`,
      node_type: n.node_type || 'source',
    }));
    return {
      nodes: [...canonNodes, ...overlayNodes.filter((n) => !canonIds.has(n.id))],
      edges: [
        ...(canonicalGraph?.edges || []),
        ...(overlay.edges || []).map((e) => ({
          ...e,
          source_id: `overlay:${e.source_id}`,
          target_id: `overlay:${e.target_id}`,
          edge_type: 'source_overlay',
        })),
      ],
      summary: {
        ...(canonicalGraph?.summary || {}),
        overlay_member_id: overlayMemberId,
      },
    };
  }, [canonicalGraph, overlayMemberId, overlays]);

  if (!canonicalGraph?.nodes?.length && !canonicalGraph?.edges?.length) {
    return (
      <article className="assessment-detail-card">
        <p className="assessment-dash-muted">
          No canonical schema map yet. Run assessment and confirm cross-connector mappings to build
          the unified model.
        </p>
      </article>
    );
  }

  return (
    <article className="assessment-detail-card">
      <div className="eg-schema-map-toolbar">
        <p className="assessment-dash-muted" style={{ margin: 0 }}>
          Unified canonical entities and relationships inferred across your connected sources.
        </p>
        {overlayKeys.length > 0 && (
          <label className="eg-schema-overlay-select">
            Source overlay
            <select
              value={overlayMemberId}
              onChange={(e) => setOverlayMemberId(e.target.value)}
            >
              <option value="">Canonical only</option>
              {overlayKeys.map((mid) => (
                <option key={mid} value={mid}>
                  {overlays[mid].display_name || mid}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <ConnectorSchemaGraphView graph={displayGraph} hideSummary={false} />
      {canonicalGraph?.summary?.canonical_entity_count != null && (
        <p className="assessment-dash-muted eg-schema-map-foot">
          {canonicalGraph.summary.canonical_entity_count} canonical entit
          {canonicalGraph.summary.canonical_entity_count === 1 ? 'y' : 'ies'}
          {' · '}
          {canonicalGraph.summary.edge_count ?? 0} relationship
          {(canonicalGraph.summary.edge_count ?? 0) === 1 ? '' : 's'}
        </p>
      )}
    </article>
  );
}
