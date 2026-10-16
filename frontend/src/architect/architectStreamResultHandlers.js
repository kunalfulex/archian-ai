import { applyInlineDataPreviewFromStreamPayload } from './applyInlineDataPreviewFromStreamPayload';
import { syncPendingMigrationJobsAfterArchitectStream } from './applyMigrationStreamToChat';
import { formatArchitectStreamErrorMessage } from './architectErrorMessages';
import { isFatalStreamError, parseSSEDataObjects } from './architectSse';
import { migrationStatusPropsCache } from './computeMigrationStatusProps';
import { resolvePlannerEntriesAfterPipeline, schedulePersistPlannerResolved } from './plannerState';
import { createEmptyOperationPreview } from './reduceOperationPreview';

export function createArchitectStreamResultHandlers(deps) {
  const {
    addMessage,
    migrationStatusesRef,
    persistMessage,
    plannerPendingRef,
    setAgentTurnSteps,
    setChatHistory,
    setInlineDataPreviews,
    setIsLoading,
    setMigrationStatuses,
    setOperationPreview,
    stopLoadingMessages,
    updateMessageMetadata,
  } = deps;

  return {
    onInlineSqlExtractionSuccess: async (data, conversationId) => {
      if (
        applyInlineDataPreviewFromStreamPayload(data, {
          setInlineDataPreviews,
          addMessage,
          persistMessage,
          setIsLoading,
          setAgentTurnSteps,
          clearOperationPreview: () => setOperationPreview(createEmptyOperationPreview()),
          conversationId,
        })
      ) {
        return '';
      }
      return null;
    },
    onPlanLimitReached: async (data, conversationId) => {
      const balance = data.plan_info.credit_balance ?? data.plan_info.remaining_credits ?? 0;
      const canExtract = data.plan_info.can_extract ?? data.plan_info.remaining_records ?? 0;
      const planLimitText =
        `📊 **Credit Limit Reached**\n\n` +
        `Your current plan (${data.plan_info.plan_type}) is limited by your credit balance.\n\n` +
        `📈 **Summary:**\n` +
        `• Credit balance: **${Number(balance).toLocaleString()}**\n` +
        `• Records you can extract: **${Number(canExtract).toLocaleString()}**\n\n` +
        `💡 **Solution:** Add credits or upgrade your plan to extract more.`;
      addMessage(planLimitText, {
        type: 'system',
        timestamp: Date.now(),
        showUpgradeButton: true,
        metadata: { planInfo: data.plan_info },
      });
      await persistMessage(conversationId, 'assistant', planLimitText, { planInfo: data.plan_info });
      setIsLoading(false);
      setAgentTurnSteps([]);
      return '';
    },
    onStreamClarificationPayload: async (data, conversationId) => {
      if (
        data.event_type === 'planner_clarification_required' ||
        data.event_type === 'planner_plan_execution_clarification' ||
        (data.clarifications?.length && data.planner_pending)
      ) {
        return '';
      }
      const text =
        (data.message || '').trim() ||
        "Please be more specific about where in the destination you'd like this data.";
      addMessage(text, { type: 'system', timestamp: Date.now() });
      await persistMessage(conversationId, 'assistant', text);
      setIsLoading(false);
      stopLoadingMessages();
      setAgentTurnSteps([]);
      setOperationPreview(createEmptyOperationPreview());
      return '';
    },
    onStreamErrorPayload: async (data, conversationId) => {
      const errorMessage = formatArchitectStreamErrorMessage(data);
      addMessage(errorMessage, { type: 'system', timestamp: Date.now() });
      await persistMessage(conversationId, 'assistant', errorMessage);
      setIsLoading(false);
      stopLoadingMessages();
      setAgentTurnSteps([]);
      setOperationPreview(createEmptyOperationPreview());
      return '';
    },
    finalizeArchitectStream: async (fullResponse, conversationId, streamState) => {
      if (
        fullResponse.includes('planner_clarification_required') ||
        fullResponse.includes('planner_plan_execution_clarification') ||
        fullResponse.includes('planner_error') ||
        (fullResponse.includes('planner_plan_ready') &&
          fullResponse.includes('awaiting_approval'))
      ) {
        return '';
      }
      if (
        fullResponse.includes('"status": "clarification"') ||
        fullResponse.includes('"status":"clarification"')
      ) {
        try {
          const jsonMatch = fullResponse.match(/data: (\{.*\})/s);
          if (jsonMatch) {
            const data = JSON.parse(jsonMatch[1]);
            if (
              data.status === 'clarification' &&
              data.event_type !== 'planner_clarification_required' &&
              data.event_type !== 'planner_plan_execution_clarification'
            ) {
              const text =
                (data.message || '').trim() ||
                "Please be more specific about where in the destination you'd like this data.";
              addMessage(text, { type: 'system', timestamp: Date.now() });
              await persistMessage(conversationId, 'assistant', text);
              setIsLoading(false);
              stopLoadingMessages();
              setAgentTurnSteps([]);
              setOperationPreview(createEmptyOperationPreview());
              return text;
            }
          }
        } catch (e) {
          console.error('Error parsing clarification response:', e);
        }
      }
      if (fullResponse.includes('"status": "error"') || fullResponse.includes('"status":"error"')) {
        const fatal = parseSSEDataObjects(fullResponse).find(isFatalStreamError);
        if (fatal) {
          const errorMessage = formatArchitectStreamErrorMessage(fatal);
          addMessage(errorMessage, { type: 'system', timestamp: Date.now() });
          await persistMessage(conversationId, 'assistant', errorMessage);
          setIsLoading(false);
          stopLoadingMessages();
          setAgentTurnSteps([]);
          setOperationPreview(createEmptyOperationPreview());
          return errorMessage;
        }
      }
      const hasSuccess =
        (fullResponse.includes('"status": "success"') || fullResponse.includes('"status":"success"')) &&
        (fullResponse.includes('"data": [') || fullResponse.includes('"data":['));
      if (hasSuccess) {
        try {
          const jsonMatch = fullResponse.match(/data: (\{.*\})/s);
          if (jsonMatch) {
            const data = JSON.parse(jsonMatch[1]);
            if (data.status === 'success' && data.data && Array.isArray(data.data)) {
              if (
                applyInlineDataPreviewFromStreamPayload(data, {
                  setInlineDataPreviews,
                  addMessage,
                  persistMessage,
                  setIsLoading,
                  setAgentTurnSteps,
                  clearOperationPreview: () => setOperationPreview(createEmptyOperationPreview()),
                  conversationId,
                })
              ) {
                return '';
              }
            }
          }
        } catch (_) {
          /* legacy inline SQL path */
        }
      }
      const planExecuted =
        fullResponse.includes('spreadsheet_created') ||
        fullResponse.includes('spreadsheet_creation_completed') ||
        fullResponse.includes('migration_completed') ||
        fullResponse.includes('migration_started');
      if (planExecuted && conversationId) {
        plannerPendingRef.current = null;
        setChatHistory((prev) => {
          const next = resolvePlannerEntriesAfterPipeline(prev);
          schedulePersistPlannerResolved(conversationId, next, updateMessageMetadata);
          return next;
        });
      }
      if (
        conversationId &&
        fullResponse.includes('migration_started') &&
        !fullResponse.includes('migration_job_finished')
      ) {
        void syncPendingMigrationJobsAfterArchitectStream({
          conversationId,
          getMigrationStatuses: () => migrationStatusesRef.current,
          setChatHistory,
          setMigrationStatuses,
          plannerPendingRef,
          updateMessageMetadata,
          clearMigrationStatusPropsCache: () => migrationStatusPropsCache.clear(),
        });
      }
      return null;
    },
  };
}
