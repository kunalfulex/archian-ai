import React from 'react';
import './PlannerRecoveryPanel.css';

/**
 * Shown after partial/failed migration when recovery is offered.
 */
export function PlannerRecoveryPanel({
  proposal,
  onApplyFix,
  onDismiss,
  showRecoveryApproval,
  isBuilding = false,
  isRunning = false,
}) {
  if (isBuilding) {
    return (
      <div className="planner-recovery-panel planner-recovery-panel--loading">
        <p className="planner-recovery-title">Migration recovery</p>
        <p className="planner-recovery-loading">Analyzing failures and building a recovery plan…</p>
      </div>
    );
  }

  if (!proposal) return null;

  const whatWentWrong = (proposal.what_went_wrong || proposal.narrative || '').trim();
  const rationale = (proposal.decision?.rationale || '').trim();
  const relationshipNotice = (proposal.relationship_notice || '').trim();
  const errorClusters = proposal.error_clusters || [];
  const recoverableCount = Number(proposal.recoverable_count || 0);
  const unrecoverableCount = Number(proposal.unrecoverable_count || 0);
  const unrecoverableNotice = (proposal.unrecoverable_notice || '').trim();
  const execPlan = proposal.recovery_execution_plan || {};
  const steps = execPlan.steps || [];
  const canApply =
    showRecoveryApproval &&
    Boolean(proposal.requires_write) &&
    recoverableCount > 0 &&
    !isRunning;
  const confidence = (proposal.recovery_confidence || execPlan.metadata?.confidence || '').trim();

  const recoverableClusters = errorClusters.filter((c) => c.resolvable);
  const informClusters = errorClusters.filter((c) => !c.resolvable);

  const applyLabel =
    recoverableCount > 0
      ? `Apply fix (${recoverableCount} record${recoverableCount === 1 ? '' : 's'})`
      : 'Apply fix';

  return (
    <div className="planner-recovery-panel">
      <p className="planner-recovery-title">
        {canApply ? 'Migration recovery plan' : 'Migration failure summary'}
      </p>
      {confidence ? (
        <p className="planner-recovery-confidence">
          Confidence: <span>{confidence}</span>
        </p>
      ) : null}
      {whatWentWrong ? (
        <p className="planner-recovery-summary">{whatWentWrong}</p>
      ) : null}
      {relationshipNotice ? (
        <p className="planner-recovery-relationship">{relationshipNotice}</p>
      ) : null}
      {rationale && rationale !== whatWentWrong ? (
        <p className="planner-recovery-rationale">{rationale}</p>
      ) : null}

      {informClusters.length > 0 || unrecoverableCount > 0 ? (
        <div className="planner-recovery-clusters planner-recovery-clusters--inform">
          <p className="planner-recovery-fields-label">
            Cannot auto-fix{unrecoverableCount ? ` (${unrecoverableCount})` : ''}
          </p>
          <ul className="planner-recovery-cluster-list">
            {informClusters.slice(0, 6).map((cluster, idx) => (
              <li key={cluster.theme || idx}>
                <strong>{cluster.theme || 'Errors'}</strong>
                {cluster.count ? ` (${cluster.count})` : ''}
                {cluster.sample_message ? (
                  <span className="planner-recovery-cluster-sample"> — {cluster.sample_message}</span>
                ) : null}
              </li>
            ))}
          </ul>
          {unrecoverableNotice && !informClusters.length ? (
            <p className="planner-recovery-inform-notice">{unrecoverableNotice}</p>
          ) : null}
        </div>
      ) : null}

      {recoverableClusters.length > 0 || recoverableCount > 0 ? (
        <div className="planner-recovery-clusters planner-recovery-clusters--recoverable">
          <p className="planner-recovery-fields-label">
            Fixable via mapping{recoverableCount ? ` (${recoverableCount})` : ''}
          </p>
          <ul className="planner-recovery-cluster-list">
            {recoverableClusters.slice(0, 6).map((cluster, idx) => (
              <li key={cluster.theme || idx}>
                <strong>{cluster.theme || 'Errors'}</strong>
                {cluster.count ? ` (${cluster.count})` : ''}
                {cluster.sample_message ? (
                  <span className="planner-recovery-cluster-sample"> — {cluster.sample_message}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {steps.length > 0 ? (
        <>
          <p className="planner-recovery-steps-label">
            {canApply
              ? 'Recovery steps (review and approve):'
              : 'Suggested next steps (review manually):'}
          </p>
          <ol className="planner-recovery-steps">
            {steps.map((step, idx) => (
              <li key={step.tool_id || step.step_type || idx}>
                {step.label || step.step_type || `Step ${idx + 1}`}
              </li>
            ))}
          </ol>
        </>
      ) : null}

      <div className="planner-recovery-actions">
        {canApply ? (
          <button
            type="button"
            className="planner-recovery-apply"
            disabled={isRunning}
            onClick={() => onApplyFix?.()}
          >
            {isRunning ? 'Running recovery…' : applyLabel}
          </button>
        ) : null}
        <button
          type="button"
          className="planner-recovery-dismiss"
          disabled={isRunning}
          onClick={() => onDismiss?.()}
        >
          {canApply ? 'Dismiss' : 'Got it'}
        </button>
      </div>
    </div>
  );
}
