import React from 'react';
import './AssessmentDashboard.css';

/**
 * Simple connected-environment visualization: members as nodes, cross_links as edges.
 */
export default function ConnectedEnvironmentView({ members = [], crossLinks = [] }) {
  if (!members.length) {
    return <p className="assessment-dash-muted">No connectors in this graph.</p>;
  }

  const memberById = Object.fromEntries(members.map((m) => [m.member_id, m]));

  return (
    <div className="eg-connected-env">
      <div className="eg-connected-nodes">
        {members.map((m) => (
          <div key={m.member_id} className="eg-connected-node">
            <strong>{m.display_name}</strong>
            <span className="eg-connected-node-meta">
              {m.connector_type}
              {m.entity_count != null ? ` · ${m.entity_count} entities` : ''}
            </span>
          </div>
        ))}
      </div>

      {crossLinks.length > 0 ? (
        <ul className="eg-connected-links">
          {crossLinks.map((link, i) => (
            <li key={link.mapping_id || link.canonical_entity_id || i} className="eg-connected-link">
              <span className="eg-connected-link-end">
                {link.source_display_name}: {link.source_entity_label || link.source_entity_id}
              </span>
              <span className="eg-connected-link-arrow" aria-hidden>
                ↔
              </span>
              <span className="eg-connected-link-end">
                {link.target_display_name || memberById[link.target_member_id]?.display_name}:{' '}
                {link.target_entity_label || link.target_entity_id}
              </span>
              {link.confidence != null && (
                <span className="eg-connected-link-conf">{Math.round(link.confidence * 100)}%</span>
              )}
              {link.status && link.status !== 'suggested' && (
                <span className={`eg-mapping-badge eg-mapping-badge--${link.status}`}>{link.status}</span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="assessment-dash-muted">No cross-connector links discovered yet.</p>
      )}
    </div>
  );
}
