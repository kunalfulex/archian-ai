import React from 'react';
import AgentTurnSteps from './AgentTurnSteps';
import {
  DATA_OPERATIONS_SECTION_TITLE,
  pipelineStepsForPreviewDisplay,
} from '../utils/dataOperationsDisplay';

/** Resolve narrative + steps from chat entry (top-level or metadata). */
function getPipelineNarrativeAndStepsFromEntry(entry) {
  const nar =
    (typeof entry.pipelineNarrative === 'string' && entry.pipelineNarrative.trim()) ||
    (typeof entry.metadata?.pipeline_narrative === 'string' && entry.metadata.pipeline_narrative.trim()) ||
    '';
  const steps =
    (Array.isArray(entry.pipelineSteps) && entry.pipelineSteps.length > 0 && entry.pipelineSteps) ||
    (Array.isArray(entry.metadata?.pipeline_steps) && entry.metadata.pipeline_steps.length > 0
      ? entry.metadata.pipeline_steps
      : null);
  return { nar, steps };
}

/** Green narrative + purple pipeline steps — shared by data_preview and transformation_preview (SSE + reload). */
export default function PipelineSummaryBlocks({ entry }) {
  const { nar, steps } = getPipelineNarrativeAndStepsFromEntry(entry);
  const opsApplied = entry.metadata?.operations_applied ?? entry.operations_applied;
  const displaySteps = pipelineStepsForPreviewDisplay(steps, opsApplied);
  if (!nar && !(displaySteps && displaySteps.length)) return null;
  return (
    <div className="chat-extraction-pipeline-inner">
      {nar ? (
        <div
          className="pipeline-narrative-summary"
          style={{
            marginBottom: 10,
            padding: '10px 12px',
            borderRadius: 8,
            backgroundColor: 'rgba(15, 118, 110, 0.06)',
            border: '1px solid rgba(15, 118, 110, 0.22)',
            fontSize: 14,
            lineHeight: 1.45,
            color: '#134e4a',
            textAlign: 'left',
          }}
        >
          {nar}
        </div>
      ) : null}
      {displaySteps && displaySteps.length ? (
        <AgentTurnSteps steps={displaySteps} title={DATA_OPERATIONS_SECTION_TITLE} />
      ) : null}
    </div>
  );
}

export function hasPipelineSummaryForEntry(entry) {
  const { nar, steps } = getPipelineNarrativeAndStepsFromEntry(entry);
  const opsApplied = entry.metadata?.operations_applied ?? entry.operations_applied;
  const displaySteps = pipelineStepsForPreviewDisplay(steps, opsApplied);
  return !!(nar || (displaySteps && displaySteps.length));
}
