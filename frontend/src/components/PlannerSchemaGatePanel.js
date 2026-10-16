import React from 'react';
import './PlannerSchemaGatePanel.css';

/**
 * Shown after schema_gate_propose step during plan execution.
 */
export function PlannerSchemaGatePanel({ proposal, onDismiss, onApproveDeploy, showDeployApproval }) {
  if (!proposal) return null;
  const missing = proposal.fields_missing_on_destination || [];
  const obj = proposal.object_api_name || 'Object';
  const needsDeploy = showDeployApproval && missing.length > 0;

  return (
    <div className="planner-schema-gate-panel">
      <p className="planner-schema-gate-title">Schema review — {obj}</p>
      {missing.length > 0 ? (
        <>
          <p className="planner-schema-gate-summary">
            {needsDeploy
              ? `${missing.length} custom field${missing.length === 1 ? '' : 's'} must be deployed to the destination org before mapping can continue.`
              : `${missing.length} custom field${missing.length === 1 ? '' : 's'} exist on the source org but not on the destination.`}
          </p>
          <ul className="planner-schema-gate-fields">
            {missing.slice(0, 20).map((f) => (
              <li key={f}>
                <code>{f}</code>
              </li>
            ))}
            {missing.length > 20 ? (
              <li className="planner-schema-gate-more">…and {missing.length - 20} more</li>
            ) : null}
          </ul>
        </>
      ) : (
        <p className="planner-schema-gate-summary">Destination schema looks aligned for this object.</p>
      )}
      <div className="planner-schema-gate-actions">
        {needsDeploy ? (
          <button
            type="button"
            className="planner-schema-gate-deploy"
            onClick={() => onApproveDeploy?.()}
          >
            Deploy fields &amp; continue
          </button>
        ) : null}
        <button type="button" className="planner-schema-gate-dismiss" onClick={() => onDismiss?.()}>
          {needsDeploy ? 'Skip deploy' : 'Got it'}
        </button>
      </div>
    </div>
  );
}
