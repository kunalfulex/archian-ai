/**
 * Dispatch a batch of parsed SSE JSON objects (agent + extraction lifecycle).
 * Returns 'continue' or a terminal sentinel for the stream loop.
 */
import { extractionJobIdFromPayload } from './extractionJobId';
import {
  reduceAgentTurnSteps,
  extractionProgressLoadingMessage,
  planReadyLoadingMessage,
} from './reduceAgentTurnSteps';
import {
  createEmptyOperationPreview,
  reduceOperationPreview,
  planReadyUserMessage,
  spinnerMessageFromOperationPreview,
} from './reduceOperationPreview';
import {
  completePlanningActivity,
  createEmptyPlanningActivity,
  latestPlanningSpinnerLabel,
  reducePlanningActivity,
} from './reducePlanningActivity';
import {
  clearRecoveryLoaderUi,
  completeRecoveryActivity,
  createEmptyRecoveryActivity,
  hasRunningRecoveryStep,
  latestRecoverySpinnerLabel,
  reduceRecoveryActivity,
} from './reduceRecoveryActivity';
import { mapJobSourceToPreviewSource } from './mapJobSourceToPreviewSource';
import { clearArchitectProcessingUi } from './clearArchitectProcessingUi';
import { isPostExtractPipelinePhaseId, PHASE_STATUS } from './operationPhaseRegistry';
import { isMigrationShapedExtractionFinish } from './extractionJobFinish';
import { isPersistedChatMessagePayload } from './applyMigrationStreamToChat';

const OPERATION_PREVIEW_EVENT_TYPES = new Set([
  'classification_started',
  'classification_completed',
  'agent_plan_ready',
  'operation_phase',
  'extraction_progress',
  'extraction_job_finished',
  'agent_step_started',
  'agent_step_completed',
  'agent_operation_started',
  'agent_operation_completed',
]);

/** Planning-scope phases during pre-execution; ignored once execution handoff starts. */
const PLANNING_OPERATION_PHASES = new Set(['understand_request', 'identify_route']);

const EXECUTING_PLAN_STATUSES = new Set(['approved', 'running', 'completed']);

function markExecutionHandoff(streamState) {
  if (streamState) {
    streamState.executionHandoff = true;
  }
}

function clearPlanningLoader(handlers, streamState) {
  markExecutionHandoff(streamState);
  deactivatePlanningActivity(handlers);
}

function isExecutingPlanStatus(status) {
  return EXECUTING_PLAN_STATUSES.has(String(status || '').toLowerCase());
}

const SOURCE_TYPE_MAP = {
  google_sheets: 'gsheets',
  sql: 'sql',
  salesforce: 'salesforce',
  file: 'file',
};

/**
 * @param {object[]} events
 * @param {object} ctx
 * @param {object} ctx.streamState - mutable: pendingHeldExtraction, extractionCompletedViaHeldStream, sawExtractionJobFinished, executionHandoff
 * @param {object} ctx.handlers
 */
function applyOperationPreviewEvent(handlers, event) {
  if (!handlers.setOperationPreview) return;
  handlers.setOperationPreview((prev) => {
    const next = reduceOperationPreview(prev, event);
    const spinner = spinnerMessageFromOperationPreview(next);
    if (spinner) {
      handlers.clearExtractionMessageRotation?.();
      handlers.setLoadingMessage(spinner);
    }
    if (
      event.event_type === 'agent_step_started' &&
      (event.tool_id === 'map_fields' || event.tool_id === 'start_migration')
    ) {
      handlers.setIsLoading?.(true);
    }
    return next;
  });
}

function applyPlanningActivityEvent(handlers, event) {
  if (!handlers.setPlanningActivity) return;
  handlers.setPlanningActivity((prev) => {
    const next = reducePlanningActivity(prev, event);
    const spinner = latestPlanningSpinnerLabel(next);
    if (spinner) {
      handlers.clearExtractionMessageRotation?.();
      handlers.setLoadingMessage(spinner);
    } else if (event.label) {
      handlers.clearExtractionMessageRotation?.();
      handlers.setLoadingMessage(event.label);
    }
    return next;
  });
}

function deactivatePlanningActivity(handlers) {
  handlers.setPlanningActivity?.((prev) => completePlanningActivity(prev));
}

function applyRecoveryActivityEvent(handlers, event) {
  if (!handlers.setRecoveryActivity) return;
  let nextPreview = null;
  handlers.setRecoveryActivity((prev) => {
    nextPreview = reduceRecoveryActivity(prev, event);
    return nextPreview;
  });
  if (!nextPreview) return;
  const spinner = latestRecoverySpinnerLabel(nextPreview);
  if (spinner) {
    handlers.clearExtractionMessageRotation?.();
    handlers.setLoadingMessage(spinner);
    handlers.setIsLoading?.(true);
    return;
  }
  handlers.setIsLoading?.(false);
  if (!hasRunningRecoveryStep(nextPreview)) {
    handlers.setRecoveryActivity?.((prev) => completeRecoveryActivity(prev));
  }
}

function deactivateRecoveryActivity(handlers) {
  clearRecoveryLoaderUi({
    setRecoveryActivity: handlers.setRecoveryActivity,
    setIsLoading: handlers.setIsLoading,
    stopLoadingMessages: handlers.stopLoadingMessages,
  });
}

export async function dispatchArchitectSseEvents(events, ctx) {
  const { streamState, handlers } = ctx;

  for (const d of events) {
    if (!d || typeof d !== 'object') continue;

    if (d.event_type === 'planning_activity') {
      if (!streamState.executionHandoff) {
        applyPlanningActivityEvent(handlers, d);
      }
      continue;
    }

    if (d.event_type === 'recovery_analysis_started') {
      handlers.setRecoveryActivity?.(
        createEmptyRecoveryActivity({
          active: true,
          jobId: d.job_id || null,
          batchId: d.batch_id || null,
          items: [],
        }),
      );
      handlers.clearExtractionMessageRotation?.();
      handlers.setLoadingMessage('Analyzing migration failures…');
      handlers.setIsLoading?.(true);
      continue;
    }

    if (d.event_type === 'recovery_activity') {
      applyRecoveryActivityEvent(handlers, d);
      continue;
    }

    if (d.event_type === 'classification_started') {
      streamState.executionHandoff = false;
      handlers.setPlanningActivity?.(createEmptyPlanningActivity({ active: true, items: [] }));
      applyOperationPreviewEvent(handlers, d);
      continue;
    }

    if (d.event_type === 'classification_completed') {
      const planStatus =
        d.plan_status || d.execution_plan?.status || d.execution_plan?.plan_status;
      if (isExecutingPlanStatus(planStatus)) {
        clearPlanningLoader(handlers, streamState);
      }
    }

    if (
      d.event_type === 'planner_plan_ready' ||
      d.event_type === 'planner_clarification_required'
    ) {
      if (d.event_type === 'planner_plan_ready' && isExecutingPlanStatus(d.plan_status)) {
        clearPlanningLoader(handlers, streamState);
      } else {
        deactivatePlanningActivity(handlers);
      }
    }

    if (
      d.event_type === 'agent_plan_ready' ||
      d.event_type === 'agent_step_started' ||
      d.event_type === 'extraction_progress'
    ) {
      if (d.event_type === 'agent_plan_ready') {
        clearPlanningLoader(handlers, streamState);
      } else {
        deactivatePlanningActivity(handlers);
      }
    }

    if (d.event_type === 'operation_phase') {
      const phase = d.phase || d.data?.phase;
      if (streamState.executionHandoff || !PLANNING_OPERATION_PHASES.has(phase)) {
        deactivatePlanningActivity(handlers);
      }
    }

    if (d.event_type === 'agent_planning') {
      handlers.setLoadingMessage('Planning next steps…');
      continue;
    }

    if (d.event_type === 'agent_plan_ready') {
      if (handlers.setOperationPreview) {
        applyOperationPreviewEvent(handlers, d);
      } else {
        const label = planReadyUserMessage(d);
        handlers.setLoadingMessage(label || planReadyLoadingMessage(d.summary));
      }
      continue;
    }

    if (OPERATION_PREVIEW_EVENT_TYPES.has(d.event_type)) {
      applyOperationPreviewEvent(handlers, d);
    }

    if (d.event_type === 'agent_step_completed' && d.tool_id === 'map_fields' && d.action === 'map_fields_ready') {
      if (handlers.onMapFieldsReady) {
        await handlers.onMapFieldsReady(d);
      }
      handlers.setAgentTurnSteps((prev) => reduceAgentTurnSteps(prev, d));
      continue;
    }

    if (
      d.event_type === 'agent_step_completed' &&
      d.tool_id === 'start_migration' &&
      d.action === 'migration_needs_mapping'
    ) {
      if (handlers.onMigrateDataPrompt) {
        await handlers.onMigrateDataPrompt(d);
      } else if (d.message && handlers.addMessage) {
        handlers.addMessage(d.message, { type: 'system', timestamp: Date.now() });
      }
      handlers.setAgentTurnSteps((prev) => reduceAgentTurnSteps(prev, d));
      continue;
    }

    if (
      d.event_type === 'agent_step_completed' &&
      d.tool_id === 'map_fields' &&
      d.action === 'needs_destination_pick'
    ) {
      handlers.setAgentTurnSteps((prev) => reduceAgentTurnSteps(prev, d));
      continue;
    }

    if (
      d.event_type === 'agent_step_completed' &&
      d.tool_id === 'start_migration' &&
      d.action === 'migration_started'
    ) {
      clearArchitectProcessingUi(handlers);
      if (handlers.onMigrationStarted) {
        await handlers.onMigrationStarted(d, handlers.conversationId);
      }
      handlers.setAgentTurnSteps((prev) => reduceAgentTurnSteps(prev, d));
      continue;
    }

    if (d.event_type === 'migration_started') {
      clearArchitectProcessingUi(handlers);
      if (handlers.onMigrationStarted) {
        await handlers.onMigrationStarted(d, handlers.conversationId);
      }
      continue;
    }

    if (d.event_type === 'migration_progress') {
      handlers.onMigrationProgress?.(d);
      continue;
    }

    if (d.event_type === 'migration_completed' && isPersistedChatMessagePayload(d.message)) {
      handlers.onMigrationCompletedChat?.(d, handlers.conversationId);
      continue;
    }

    if (
      d.event_type === 'planner_recovery_required' &&
      isPersistedChatMessagePayload(d.message)
    ) {
      deactivateRecoveryActivity(handlers);
      handlers.onMigrationRecoveryChat?.(d, handlers.conversationId);
      continue;
    }

    if (
      d.event_type === 'agent_step_completed' &&
      (d.action === 'schema_gate_proposal' || d.tool_id === 'schema_gate_propose')
    ) {
      handlers.onSchemaGateProposal?.(d);
      handlers.setAgentTurnSteps((prev) => reduceAgentTurnSteps(prev, d));
      continue;
    }

    if (
      d.event_type === 'agent_step_started' ||
      d.event_type === 'agent_step_completed' ||
      d.event_type === 'agent_operation_started' ||
      d.event_type === 'agent_operation_completed' ||
      d.event_type === 'extraction_progress'
    ) {
      const extractToolIds = new Set(['extract_salesforce', 'extract_sql', 'extract_google_sheets']);
      const gsheetsMicroToolIds = new Set(['gsheets_execute_operation']);
      const skipAgentStepsForPreview =
        handlers.setOperationPreview &&
        (d.event_type === 'extraction_progress' ||
          (d.event_type === 'agent_step_started' &&
            (extractToolIds.has(d.tool_id) || gsheetsMicroToolIds.has(d.tool_id))) ||
          (d.event_type === 'agent_step_completed' &&
            (extractToolIds.has(d.tool_id) || gsheetsMicroToolIds.has(d.tool_id))));
      if (d.event_type === 'extraction_progress' && !handlers.setOperationPreview) {
        handlers.setLoadingMessage(extractionProgressLoadingMessage(d));
      }
      if (!skipAgentStepsForPreview) {
        handlers.setAgentTurnSteps((prev) => reduceAgentTurnSteps(prev, d));
      }
      continue;
    }

    if (d.event_type === 'migration_job_finished' && d.job_id) {
      clearArchitectProcessingUi(handlers);
      if (handlers.onMigrationJobFinished) {
        await handlers.onMigrationJobFinished(d, handlers.conversationId);
      }
      continue;
    }

    if (d.event_type === 'extraction_job_finished' && d.job_status && d.job_id) {
      if (isMigrationShapedExtractionFinish(d.job_status)) continue;
      streamState.sawExtractionJobFinished = true;
      const jid = d.job_id;
      if (handlers.hasAppliedJobFinish(jid)) continue;
      handlers.markAppliedJobFinish(jid);

      if (streamState.pendingHeldExtraction?.jobId === jid) {
        streamState.extractionCompletedViaHeldStream = true;
      }

      handlers.clearExtractionMessageRotation();
      const previewSrc = mapJobSourceToPreviewSource(d.job_status.source_type);
      try {
        await handlers.onExtractionJobFinished(d.job_status, previewSrc);
      } catch (finishErr) {
        console.error('extraction_job_finished UI apply failed:', finishErr);
        if (handlers.recoverCompletedExtractionsFromActiveJobs) {
          await handlers.recoverCompletedExtractionsFromActiveJobs(streamState);
        } else if (handlers.pollJobForInlinePreview) {
          handlers.pollJobForInlinePreview(jid, handlers.conversationId, previewSrc);
        }
      }
      if (handlers.setOperationPreview) {
        handlers.setOperationPreview((prev) => {
          const next = reduceOperationPreview(prev, d);
          const hasPostExtractWork = (next.phases || []).some(
            (p) =>
              isPostExtractPipelinePhaseId(p.id) &&
              (p.status === PHASE_STATUS.PENDING || p.status === PHASE_STATUS.RUNNING),
          );
          return hasPostExtractWork ? next : createEmptyOperationPreview();
        });
      } else {
        handlers.clearOperationPreview?.();
      }
      handlers.setAgentTurnSteps?.((prev) => (prev.length ? [] : prev));
      continue;
    }

    if (d.status === 'processing' && extractionJobIdFromPayload(d)) {
      const jobId = extractionJobIdFromPayload(d);
      handlers.trackActiveJob(jobId);
      const previewSource = SOURCE_TYPE_MAP[d.source_type] || 'sql';
      handlers.onExtractionProcessingStarted({ jobId, previewSource, sourceType: d.source_type });
      streamState.pendingHeldExtraction = { jobId, previewSource };
    }
  }

  return 'continue';
}
