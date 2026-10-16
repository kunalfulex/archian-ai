import React from 'react';
import AgentTurnSteps from './AgentTurnSteps';
import OperationPreviewLoader from './OperationPreviewLoader';
import PlanningActivityLoader from './PlanningActivityLoader';
import RecoveryActivityLoader from './RecoveryActivityLoader';
import { stripTrailingEllipsis } from '../utils/loadingMessageDisplay';
import { AGENT_STREAM_SECTION_TITLE } from '../utils/dataOperationsDisplay';

export default function ChatStreamingStatus({
  isLoading,
  loadingMessage,
  streamingText,
  operationPreview,
  agentTurnSteps,
  planningActivity,
  recoveryActivity,
  migrationStatuses,
}) {
  return (
    <>
      {(() => {
        const hasRunningPhase = (operationPreview?.phases || []).some(
          (p) => p.status === 'running',
        );
        const hasRunningStep = agentTurnSteps.some((s) => s.status === 'running');
        const hasActiveMigration = Object.values(migrationStatuses).some(
          (s) => s && (s.status === 'processing' || s.status === 'in_progress'),
        );
        const showPostStreamLoader =
          !isLoading &&
          !hasActiveMigration &&
          (hasRunningPhase || hasRunningStep);
        if (!showPostStreamLoader) return null;
        return operationPreview?.phases?.length > 0 ? (
          <OperationPreviewLoader preview={operationPreview} title={AGENT_STREAM_SECTION_TITLE} />
        ) : (
          <AgentTurnSteps steps={agentTurnSteps} title={AGENT_STREAM_SECTION_TITLE} />
        );
      })()}

      {/* Streaming text — hide during agent plan runs (operation preview already shows step progress) */}
      {(streamingText || '').trim() &&
        !(
          isLoading &&
          ((operationPreview?.phases?.length ?? 0) > 0 ||
            agentTurnSteps.length > 0 ||
            (planningActivity?.active && (planningActivity?.items?.length ?? 0) > 0) ||
            (recoveryActivity?.active && (recoveryActivity?.items?.length ?? 0) > 0))
        ) && (
        <div className="streaming-display">
          {streamingText}
          <span className="blinking-cursor">|</span>
        </div>
      )}

      {isLoading && (
        <div className="loading-display">
          {(() => {
            const showPlanningActivity =
              planningActivity?.active && (planningActivity?.items?.length ?? 0) > 0;
            const showRecoveryActivity =
              recoveryActivity?.active && (recoveryActivity?.items?.length ?? 0) > 0;
            const showPipeline =
              !showPlanningActivity && !showRecoveryActivity && (operationPreview?.phases?.length ?? 0) > 0;
            const showLegacySteps =
              !showPlanningActivity && !showRecoveryActivity && !showPipeline && agentTurnSteps.length > 0;
            const statusText =
              loadingMessage && loadingMessage !== 'thinking' ? loadingMessage : null;
            const showStepsPanel =
              showPlanningActivity || showRecoveryActivity || showPipeline || showLegacySteps;
            const statusDisplayText = statusText ? stripTrailingEllipsis(statusText) : null;

            const statusRow = statusDisplayText ? (
              <div className="loading-display-row loading-display-row-above-steps">
                <img src="/icon.svg" className="icon-load chat-load" alt="archian ai" />
                <span className="loading-text-shine">{statusDisplayText}</span>
                <span className="loading-text-shine">
                  <span className="loading-dot">.</span>
                  <span className="loading-dot">.</span>
                  <span className="loading-dot">.</span>
                </span>
              </div>
            ) : null;

            return (
              <>
                {showStepsPanel && statusRow}
                {showPlanningActivity ? (
                  <PlanningActivityLoader preview={planningActivity} />
                ) : showRecoveryActivity ? (
                  <RecoveryActivityLoader preview={recoveryActivity} />
                ) : showPipeline ? (
                  <OperationPreviewLoader
                    preview={operationPreview}
                    title={AGENT_STREAM_SECTION_TITLE}
                  />
                ) : showLegacySteps ? (
                  <div className="agent-live-activity-preview">
                    <AgentTurnSteps steps={agentTurnSteps} title={AGENT_STREAM_SECTION_TITLE} />
                  </div>
                ) : (
                  statusRow
                )}
              </>
            );
          })()}
        </div>
      )}
    </>
  );
}
