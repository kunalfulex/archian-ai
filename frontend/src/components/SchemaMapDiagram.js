import React, { useMemo, useState } from 'react';
import { layoutSchemaMap, selectFocusSubgraph } from '../utils/schemaMapLayout';
import './SchemaMapDiagram.css';

function edgePath(x1, y1, x2, y2) {
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
}

function nodeCenter(pos, w, h) {
  return { cx: pos.x + w / 2, cy: pos.y + h / 2 };
}

export default function SchemaMapDiagram({ graph }) {
  const [showAll, setShowAll] = useState(false);

  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];

  const { focusNodes, focusEdges, layout, hiddenCount } = useMemo(() => {
    const subgraph = showAll
      ? { nodes, edges }
      : selectFocusSubgraph(nodes, edges);
    const layoutResult = layoutSchemaMap(subgraph.nodes, subgraph.edges);
    return {
      focusNodes: subgraph.nodes,
      focusEdges: subgraph.edges,
      layout: layoutResult,
      hiddenCount: showAll ? 0 : Math.max(0, nodes.length - subgraph.nodes.length),
    };
  }, [nodes, edges, showAll]);

  if (!nodes.length) return null;

  const { positions, width, height, nodeWidth: w, nodeHeight: h } = layout;

  return (
    <div className="schema-map-diagram">
      <div className="schema-map-toolbar">
        <span className="schema-map-toolbar-title">Schema map</span>
        {hiddenCount > 0 && (
          <span className="schema-map-toolbar-hint">
            Showing {focusNodes.length} of {nodes.length} objects (most connected)
          </span>
        )}
        {nodes.length > 12 && (
          <button type="button" className="schema-map-toggle" onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Show focus view' : 'Show all objects'}
          </button>
        )}
      </div>

      <div className="schema-map-canvas-wrap">
        <svg
          className="schema-map-svg"
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="Schema relationship map"
        >
          <defs>
            <marker
              id="schema-arrow"
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
            >
              <path d="M0,0 L8,4 L0,8 Z" fill="#94a3b8" />
            </marker>
          </defs>

          {focusEdges.map((e, i) => {
            const src = positions[e.source_id];
            const tgt = positions[e.target_id];
            if (!src || !tgt) return null;
            const s = nodeCenter(src, w, h);
            const t = nodeCenter(tgt, w, h);
            const startX = s.cx + w / 2 - 8;
            const endX = t.cx - w / 2 + 8;
            const severity = e.gap_severity;
            return (
              <g key={`edge-${i}`}>
                <path
                  d={edgePath(startX, s.cy, endX, t.cy)}
                  className={`schema-map-edge${severity ? ` schema-map-edge--${severity}` : ''}`}
                  fill="none"
                  markerEnd="url(#schema-arrow)"
                />
              </g>
            );
          })}

          {focusNodes.map((n) => {
            const pos = positions[n.id];
            if (!pos) return null;
            const edgeCount = focusEdges.filter(
              (e) => e.source_id === n.id || e.target_id === n.id,
            ).length;
            return (
              <g key={n.id} transform={`translate(${pos.x}, ${pos.y})`}>
                <rect
                  className={[
                    'schema-map-node',
                    n.is_deleted ? 'schema-map-node--deleted' : '',
                    n.orphan_kind === 'structural' ? 'schema-map-node--orphan' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  width={w}
                  height={h}
                  rx="10"
                  ry="10"
                />
                <text className="schema-map-node-title" x={w / 2} y={28} textAnchor="middle">
                  {(n.label || n.id).length > 22
                    ? `${(n.label || n.id).slice(0, 20)}…`
                    : n.label || n.id}
                </text>
                <text className="schema-map-node-meta" x={w / 2} y={48} textAnchor="middle">
                  {edgeCount} relationship{edgeCount === 1 ? '' : 's'}
                </text>
                {n.gap_severities?.length > 0 && (
                  <text className="schema-map-node-badge" x={w / 2} y={64} textAnchor="middle">
                    needs attention
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
