import { useCallback, useRef } from 'react';
import { buildAuthHeaders } from '../utils/authStorage';
import {
  parseSSEDataObjects,
  filterSseChunkForDisplay,
  chunkHasInternalSseOnly,
  isSseJsonOnlyTranscript,
  isFatalStreamError,
  shouldAbortArchitectStreamOnHandlerResult,
} from '../architect/architectSse';
import { dispatchArchitectSseEvents } from '../architect/dispatchArchitectSseEvents';
import { consumeArchitectSseReader } from '../architect/consumeArchitectSseReader';
import { createEmptyOperationPreview } from '../architect/reduceOperationPreview';
import { createEmptyPlanningActivity } from '../architect/reducePlanningActivity';
import { createEmptyRecoveryActivity } from '../architect/reduceRecoveryActivity';
import { clearArchitectProcessingUi } from '../architect/clearArchitectProcessingUi';

function parseDataJsonFromChunk(chunk, predicate) {
  const lines = chunk.split('\n');
  for (const line of lines) {
    const jsonMatch = line.match(/data:\s*(\{.*\})/);
    if (!jsonMatch) continue;
    try {
      const data = JSON.parse(jsonMatch[1]);
      if (predicate(data)) return data;
    } catch (_) {
      /* skip */
    }
  }
  return null;
}

/**
 * Architect POST /architect/stream — SSE read loop + event dispatch.
 * ChatUploader supplies state setters and side-effect callbacks only.
 */
export function useArchitectStream(deps) {
  const depsRef = useRef(deps);
  depsRef.current = deps;
  const assistantMessageRef = useRef('');

  const runArchitectStream = useCallback(async (prompt, conversationId, signal, streamOptions = {}) => {
    const d = depsRef.current;

    let streamState = {
      pendingHeldExtraction: null,
      extractionCompletedViaHeldStream: false,
      sawExtractionJobFinished: false,
    };

    try {
      const attachedFile = streamOptions.attachedFile || null;
      let response;
      if (attachedFile) {
        const formData = new FormData();
        formData.append('file', attachedFile);
        formData.append('prompt', prompt || '');
        if (conversationId) {
          formData.append('conversation_id', conversationId);
        }
        formData.append('plan_auto_run', streamOptions.planAutoRun ? 'true' : 'false');
        if (streamOptions.plannerResume) {
          formData.append('planner_resume', JSON.stringify(streamOptions.plannerResume));
        }
        response = await fetch(`${process.env.REACT_APP_API_URL}/architect/stream`, {
          method: 'POST',
          headers: buildAuthHeaders({}, 'POST'),
          credentials: 'include',
          body: formData,
          signal,
        });
      } else {
        response = await fetch(`${process.env.REACT_APP_API_URL}/architect/stream`, {
          method: 'POST',
          headers: buildAuthHeaders({ 'Content-Type': 'application/json' }, 'POST'),
          credentials: 'include',
          body: JSON.stringify({
            prompt,
            conversation_id: conversationId,
            planner_resume: streamOptions.plannerResume || null,
            plan_auto_run: Boolean(streamOptions.planAutoRun),
          }),
          signal,
        });
      }

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullResponse = '';

      streamState = {
        pendingHeldExtraction: null,
        extractionCompletedViaHeldStream: false,
        sawExtractionJobFinished: false,
        executionHandoff: false,
      };

      d.setStreamingText('');
      d.setAgentTurnSteps([]);
      d.setOperationPreview?.(createEmptyOperationPreview());
      d.setPlanningActivity?.(createEmptyPlanningActivity());
      d.setRecoveryActivity?.(createEmptyRecoveryActivity());
      d.appliedExtractionJobFinishIdsRef.current.clear();
      assistantMessageRef.current = '';

      let isFirstChunk = true;
      const isCancelled = () => d.cancelledRef.current;

      const handlers = {
        setLoadingMessage: d.setLoadingMessage,
        setAgentTurnSteps: d.setAgentTurnSteps,
        setOperationPreview: d.setOperationPreview,
        setPlanningActivity: d.setPlanningActivity,
        setRecoveryActivity: d.setRecoveryActivity,
        clearOperationPreview: () => d.setOperationPreview?.(createEmptyOperationPreview()),
        trackActiveJob: (jobId) => d.activeJobIdsRef.current.add(jobId),
        hasAppliedJobFinish: (jid) => d.appliedExtractionJobFinishIdsRef.current.has(jid),
        markAppliedJobFinish: (jid) => d.appliedExtractionJobFinishIdsRef.current.add(jid),
        clearExtractionMessageRotation: () => {
          if (d.loadingMessageIntervalRef.current) {
            clearInterval(d.loadingMessageIntervalRef.current);
            d.loadingMessageIntervalRef.current = null;
          }
        },
        onMapFieldsReady: d.onMapFieldsReady,
        onMigrateDataPrompt: d.onMigrateDataPrompt,
        onSpreadsheetCreated: d.onSpreadsheetCreated,
        onPlannerClarificationRequired: d.onPlannerClarificationRequired,
        onPlannerPlanReady: d.onPlannerPlanReady,
        onSchemaGateProposal: d.onSchemaGateProposal,
        onPlannerSchemaDeployRequired: d.onPlannerSchemaDeployRequired,
        onPlannerRecoveryRequired: d.onPlannerRecoveryRequired,
        onFreshSupersedesPending: d.onFreshSupersedesPending,
        conversationId,
        onExtractionJobFinished: (jobStatus, previewSrc) =>
          d.applyTaskJobStatusToUi(jobStatus, previewSrc, conversationId),
        recoverCompletedExtractionsFromActiveJobs: (streamState) =>
          d.recoverCompletedExtractionsFromActiveJobs?.(conversationId, streamState),
        pollJobForInlinePreview: (jobId, previewSrc) =>
          d.pollJobForInlinePreview?.(jobId, conversationId, previewSrc),
        stopLoadingMessages: d.stopLoadingMessages,
        setIsLoading: d.setIsLoading,
        onMigrationStarted: d.onMigrationStarted,
        onMigrationProgress: d.onMigrationProgress,
        onMigrationJobFinished: d.onMigrationJobFinished,
        onMigrationCompletedChat: d.onMigrationCompletedChat,
        onMigrationRecoveryChat: d.onMigrationRecoveryChat,
        onExtractionProcessingStarted: ({ jobId, previewSource, sourceType }) => {
          const sourceTypeName =
            previewSource === 'gsheets'
              ? 'Google Sheets'
              : previewSource === 'salesforce'
                ? 'Salesforce'
                : previewSource === 'file'
                  ? 'File'
                  : 'SQL';
          const extractionMessages = [
            `Connecting to ${sourceTypeName}`,
            'Reviewing fields and structure',
            'Building extraction plan',
            'Extracting records',
            'Preparing data preview',
          ];
          let currentExtractionIndex = 0;
          d.setLoadingMessage(extractionMessages[0]);
          if (d.loadingMessageIntervalRef.current) {
            clearInterval(d.loadingMessageIntervalRef.current);
          }
          d.loadingMessageIntervalRef.current = setInterval(() => {
            currentExtractionIndex = (currentExtractionIndex + 1) % extractionMessages.length;
            d.setLoadingMessage(extractionMessages[currentExtractionIndex]);
          }, 2500);
          d.setIsLoading(true);
        },
      };

      for await (const { chunk } of consumeArchitectSseReader(reader, decoder, isCancelled)) {
        if (isCancelled()) break;

        const hasDataPrefix = chunk.includes('data:');
        const sseDataObjects = hasDataPrefix ? parseSSEDataObjects(chunk) : [];

        if (
          sseDataObjects.some(
            (ev) =>
              ev &&
              typeof ev === 'object' &&
              ((ev.event_type && String(ev.event_type).startsWith('agent_')) ||
                ev.event_type === 'extraction_progress' ||
                ev.event_type === 'classification_started' ||
                ev.event_type === 'classification_completed' ||
                ev.event_type === 'operation_phase' ||
                ev.event_type === 'planner_clarification_required' ||
                ev.event_type === 'planner_plan_ready' ||
                ev.event_type === 'planner_schema_deploy_required' ||
                ev.event_type === 'recovery_analysis_started' ||
                ev.event_type === 'recovery_activity' ||
                ev.event_type === 'planner_recovery_required'),
          )
        ) {
          handlers.clearExtractionMessageRotation();
        }

        for (const ev of sseDataObjects) {
          if (
            ev?.event_type === 'classification_completed' &&
            (ev._fresh_supersedes_pending || ev.planner_pending_superseded)
          ) {
            await d.onFreshSupersedesPending?.(ev, conversationId);
          }
          if (
            ev?.event_type === 'assistant_message' &&
            typeof ev.text === 'string' &&
            ev.text.trim()
          ) {
            assistantMessageRef.current = ev.text;
            fullResponse = ev.text;
            d.stopLoadingMessages();
            d.setIsLoading(false);
            d.setStreamingText(ev.text);
          }
        }

        if (!assistantMessageRef.current) {
          fullResponse += filterSseChunkForDisplay(chunk);
        }

        const isInternalOnlyChunk = hasDataPrefix && chunkHasInternalSseOnly(chunk, sseDataObjects);
        if (isFirstChunk) {
          if (!isInternalOnlyChunk) {
            d.stopLoadingMessages();
            d.setIsLoading(false);
          }
          isFirstChunk = false;
        }

        if (hasDataPrefix && chunk.includes('"extraction_job_created"')) {
          for (const data of sseDataObjects) {
            if (data.event_type === 'extraction_job_created' && data.job_id) {
              d.activeJobIdsRef.current.add(data.job_id);
              break;
            }
          }
        }

        if (hasDataPrefix && chunk.includes('"migration_started"')) {
          const mig = parseDataJsonFromChunk(
            chunk,
            (data) => data.event_type === 'migration_started' && data.job_id && data.batch_id,
          );
          if (mig) {
            clearArchitectProcessingUi(handlers);
            d.onMigrationStarted?.(mig, conversationId);
          }
        }

        if (hasDataPrefix && chunk.includes('"spreadsheet_created"')) {
          const created = parseDataJsonFromChunk(
            chunk,
            (data) => data.event_type === 'spreadsheet_created' && data.spreadsheet_id,
          );
          if (created && d.onSpreadsheetCreated) {
            await d.onSpreadsheetCreated(created, conversationId);
          }
        }

        const inlineSql = parseDataJsonFromChunk(
          chunk,
          (data) => data.status === 'success' && Array.isArray(data.data),
        );
        if (inlineSql) {
          const early = await d.onInlineSqlExtractionSuccess(inlineSql, conversationId);
          // Terminal extract preview — stop even when handler returns ''.
          if (early != null) return early;
        }

        const planLimit = parseDataJsonFromChunk(chunk, (data) => data.status === 'plan_limit_reached');
        if (planLimit) {
          const early = await d.onPlanLimitReached(planLimit, conversationId);
          if (early != null) return early;
        }

        // Prefer the real clarification event (includes full planner_pending / _pending_file_ref).
        // Do NOT synthesize from classification_completed — that payload omits staged file refs
        // and returning early used to drop the follow-up planner_clarification_required SSE.
        const plannerClarification =
          sseDataObjects.find(
            (data) =>
              data?.event_type === 'planner_clarification_required' ||
              data?.event_type === 'planner_plan_execution_clarification',
          ) ||
          parseDataJsonFromChunk(
            chunk,
            (data) =>
              data.event_type === 'planner_clarification_required' ||
              data.event_type === 'planner_plan_execution_clarification',
          );
        if (plannerClarification) {
          const early = await d.onPlannerClarificationRequired?.(
            plannerClarification,
            conversationId,
          );
          if (shouldAbortArchitectStreamOnHandlerResult(early)) return early;
        }

        const plannerPlan = parseDataJsonFromChunk(
          chunk,
          (data) =>
            data.event_type === 'planner_plan_ready' &&
            (data.plan_status === 'awaiting_approval' ||
              (Array.isArray(data.steps) && data.steps.length > 0)),
        );
        if (plannerPlan) {
          const early = await d.onPlannerPlanReady?.(plannerPlan, conversationId);
          if (shouldAbortArchitectStreamOnHandlerResult(early)) return early;
        }

        const schemaDeploy = parseDataJsonFromChunk(
          chunk,
          (data) => data.event_type === 'planner_schema_deploy_required',
        );
        if (schemaDeploy) {
          const early = await d.onPlannerSchemaDeployRequired?.(schemaDeploy, conversationId);
          if (shouldAbortArchitectStreamOnHandlerResult(early)) return early;
        }

        const streamClarification = parseDataJsonFromChunk(
          chunk,
          (data) =>
            data.status === 'clarification' &&
            data.event_type !== 'planner_clarification_required' &&
            data.event_type !== 'planner_plan_execution_clarification',
        );
        if (streamClarification) {
          await d.onStreamClarificationPayload?.(
            streamClarification,
            conversationId,
          );
          // Do not return early — planner_plan_execution_clarification may follow.
        }

        const streamErr = parseDataJsonFromChunk(chunk, isFatalStreamError);
        if (streamErr) {
          const early = await d.onStreamErrorPayload(streamErr, conversationId);
          if (early != null) return early;
        }

        await dispatchArchitectSseEvents(sseDataObjects, { streamState, handlers });
        if (!assistantMessageRef.current) {
          d.setStreamingText(fullResponse);
        }
      }

      if (streamState.pendingHeldExtraction && !streamState.extractionCompletedViaHeldStream) {
        const { jobId, previewSource } = streamState.pendingHeldExtraction;
        d.pollJobForInlinePreview(jobId, conversationId, previewSource);
      }

      const finalEarly = await d.finalizeArchitectStream(fullResponse, conversationId, streamState);
      if (finalEarly != null) return finalEarly;

      d.setOperationPreview?.(createEmptyOperationPreview());
      d.setAgentTurnSteps([]);
      d.setPlanningActivity?.(createEmptyPlanningActivity());
      d.setStreamingText('');
      const assistantText = (assistantMessageRef.current || fullResponse || '').trim();
      if (assistantText && !isSseJsonOnlyTranscript(assistantText)) {
        return assistantText;
      }
      if (streamState.sawExtractionJobFinished || isSseJsonOnlyTranscript(fullResponse)) {
        return '';
      }
      return fullResponse.trim() ? fullResponse : '';
    } catch (err) {
      if (err.name === 'AbortError') {
        d.setAgentTurnSteps([]);
        d.setOperationPreview?.(createEmptyOperationPreview());
        d.setPlanningActivity?.(createEmptyPlanningActivity());
        return null;
      }
      console.error('❌ Archian AI Architect streaming call failed:', err);
      d.setAgentTurnSteps([]);
      d.setOperationPreview?.(createEmptyOperationPreview());
      d.setPlanningActivity?.(createEmptyPlanningActivity());
      d.stopLoadingMessages();
      try {
        const recovered = await d.recoverCompletedExtractionsFromActiveJobs?.({
          pendingHeldExtraction: streamState.pendingHeldExtraction,
        });
        if (recovered) {
          d.setIsLoading(false);
          return '';
        }
      } catch (recoverErr) {
        console.warn('Post-stream extraction recovery failed:', recoverErr);
      }
      d.setIsLoading(false);
      return '❌ Streaming failed. Please try again.';
    } finally {
      d.setIsLoading?.(false);
      d.stopLoadingMessages?.();
    }
  }, []);

  return { runArchitectStream };
}
