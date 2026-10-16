import React from 'react';
import './PlannerPlanPanel.css';
import { resolvePlannerPlanContextLines } from '../architect/plannerPlanDisplay';

export function PlannerPlanPanel({
  steps = [],
  goalSummary,
  planContract,
  destination,
  destinationName,
  destinationSheetName,
  mode,
  sourceName,
  sourceEntityKey,
  sourceConnectorType,
  onApprove,
  onDismiss,
  disabled = false,
  buildErrorMessage = null,
}) {
  if (!steps.length) {
    if (!buildErrorMessage) return null;
    return (
      <div className="planner-plan-panel">
        <p className="planner-plan-title">Execution plan could not be built yet</p>
        <p className="planner-plan-goal">{buildErrorMessage}</p>
        <div className="planner-plan-actions">
          <button
            type="button"
            className="planner-plan-secondary"
            disabled={disabled}
            onClick={() => onDismiss?.()}
          >
            Dismiss
          </button>
        </div>
      </div>
    );
  }

  const ctx = resolvePlannerPlanContextLines(steps, {
    planContract,
    destination,
    destinationName,
    destinationSheetName,
    mode,
    sourceName,
    sourceEntityKey,
    sourceConnectorType,
  });

  return (
    <div className="planner-plan-panel">
      <p className="planner-plan-title">Execution plan (review and approve to continue)</p>
      {ctx.srcLine && ctx.sourceHeaderLabel ? (
        <p className="planner-plan-destination">
          <span className="planner-plan-destination-label">{ctx.sourceHeaderLabel}</span> {ctx.srcLine}
        </p>
      ) : null}
      {ctx.destLine && ctx.destHeaderLabel ? (
        <p className="planner-plan-destination">
          <span className="planner-plan-destination-label">{ctx.destHeaderLabel}</span> {ctx.destLine}
        </p>
      ) : null}
      {ctx.mapLine ? (
        <p className="planner-plan-destination">
          <span className="planner-plan-destination-label">{ctx.mapHeaderLabel}</span> {ctx.mapLine}
        </p>
      ) : null}
      {goalSummary ? <p className="planner-plan-goal">{goalSummary}</p> : null}
      <ol className="planner-plan-steps">
        {steps.map((step, idx) => {
          const toolId = step.tool_id || '';
          const isSchema = toolId.includes('schema_gate') || step.step_type === 'deploy_schema';
          return (
            <li key={step.id || step.step_type || idx} className={isSchema ? 'planner-plan-step--schema' : ''}>
              {step.label || step.step_type || `Step ${idx + 1}`}
              {isSchema ? <span className="planner-plan-step-badge">Schema</span> : null}
            </li>
          );
        })}
      </ol>
      <div className="planner-plan-actions">
        <button
          type="button"
          className="planner-plan-secondary"
          disabled={disabled}
          onClick={() => onDismiss?.()}
        >
          Dismiss plan
        </button>
        <button
          type="button"
          className="planner-plan-run"
          disabled={disabled}
          onClick={() => onApprove?.()}
        >
          Run plan
        </button>
      </div>
    </div>
  );
}
