import { useCallback } from 'react';
import { createEmptyOperationPreview } from '../architect/reduceOperationPreview';
import {
  isPlannerEntryResolved,
  markPlannerEntriesResolved,
  resolvePlannerEntriesAfterPipeline,
  schedulePersistPlannerResolved,
  withPlannerEntryResolved,
} from '../architect/plannerState';

/**
 * User-initiated planner resume actions (Run plan, clarification panel, schema deploy).
 */
export function usePlannerResume({
  activeConversationId,
  chatHistoryRef,
  plannerPendingRef,
  cancelledRef,
  abortControllerRef,
  setChatHistory,
  setIsLoading,
  setAgentTurnSteps,
  setOperationPreview,
  startLoadingMessages,
  stopLoadingMessages,
  runArchitectStream,
  addMessage,
  planAutoRunRef,
  apiClient,
  updateMessageMetadata,
  persistMessage,
}) {
  const finalizePlannerStream = useCallback(() => {
    plannerPendingRef.current = null;
    setChatHistory((prev) => {
      const next = resolvePlannerEntriesAfterPipeline(prev);
      schedulePersistPlannerResolved(activeConversationId, next, updateMessageMetadata);
      return next;
    });
    stopLoadingMessages();
    setIsLoading(false);
    setAgentTurnSteps([]);
    setOperationPreview?.(createEmptyOperationPreview());
  }, [
    activeConversationId,
    plannerPendingRef,
    setChatHistory,
    stopLoadingMessages,
    setIsLoading,
    setAgentTurnSteps,
    setOperationPreview,
    updateMessageMetadata,
  ]);

  const runPlannerResumeStream = useCallback(
    async ({ conversationId, signal, plannerResume, loadingLabel, resumePrompt = '' }) => {
      startLoadingMessages(loadingLabel);
      setIsLoading(true);
      const effectivePrompt =
        (resumePrompt || plannerResume?.planner_pending?.original_prompt || '').trim();
      try {
        return await runArchitectStream(effectivePrompt, conversationId, signal, {
          plannerResume,
          planAutoRun: planAutoRunRef?.current ?? false,
        });
      } catch (err) {
        if (err.name !== 'AbortError') {
          throw err;
        }
        return null;
      }
    },
    [runArchitectStream, setIsLoading, startLoadingMessages, planAutoRunRef],
  );

  const beginPlannerAbortableRequest = useCallback(() => {
    cancelledRef.current = false;
    abortControllerRef.current = new AbortController();
    return abortControllerRef.current.signal;
  }, [cancelledRef, abortControllerRef]);

  const handlePlannerPlanApprove = useCallback(
    async (pendingOverride = null) => {
      const pending = pendingOverride || plannerPendingRef.current;
      let conversationId = activeConversationId;
      if (!conversationId && pending?.conversation_id) {
        conversationId = pending.conversation_id;
      }
      if (!pending || !conversationId) {
        addMessage(
          'Could not run the plan — refresh the chat and try **Run plan** again.',
          { type: 'system', timestamp: Date.now() },
        );
        return;
      }
      plannerPendingRef.current = pending;

      setChatHistory((prev) => {
        const next = markPlannerEntriesResolved(prev, 'planner_plan_ready');
        schedulePersistPlannerResolved(conversationId, next, updateMessageMetadata);
        return next;
      });

      const signal = beginPlannerAbortableRequest();
      const resumePrompt = (pending.original_prompt || '').trim();
      try {
        const result = await runPlannerResumeStream({
          conversationId,
          signal,
          loadingLabel: 'Running your plan',
          resumePrompt,
          plannerResume: {
            planner_pending: pending,
            approve_plan: true,
          },
        });
        const assistantText = (result || '').trim();
        const sawSpreadsheetCreated = chatHistoryRef.current?.some?.(
          (e) =>
            e.metadata?.event_type === 'spreadsheet_creation_completed' ||
            e.metadata?.event_type === 'spreadsheet_created',
        );
        if (
          assistantText &&
          !assistantText.includes('❌ Streaming failed') &&
          !sawSpreadsheetCreated
        ) {
          addMessage(assistantText, { type: 'system', timestamp: Date.now() });
          if (persistMessage) {
            await persistMessage(conversationId, 'assistant', assistantText);
          }
        }
      } catch (err) {
        console.error('Planner plan approve stream failed:', err);
        addMessage(
          '❌ **Error:** Failed to start the plan. Please try **Run plan** again.',
          { type: 'system', timestamp: Date.now() },
        );
      } finally {
        finalizePlannerStream();
      }
    },
    [
      activeConversationId,
      addMessage,
      beginPlannerAbortableRequest,
      finalizePlannerStream,
      persistMessage,
      chatHistoryRef,
      plannerPendingRef,
      runPlannerResumeStream,
      setChatHistory,
      updateMessageMetadata,
    ],
  );

  const handlePlannerSchemaDeployApprove = useCallback(
    async (skipDeploy = false) => {
      const pending = plannerPendingRef.current;
      const conversationId = activeConversationId;
      if (!pending || !conversationId) return;

      setChatHistory((prev) =>
        markPlannerEntriesResolved(prev, 'planner_schema_deploy_required'),
      );

      const signal = beginPlannerAbortableRequest();
      try {
        await runPlannerResumeStream({
          conversationId,
          signal,
          loadingLabel: skipDeploy ? 'Continuing plan' : 'Deploying schema',
          plannerResume: {
            planner_pending: pending,
            approve_schema_deploy: !skipDeploy,
            skip_schema_deploy: skipDeploy,
          },
        });
      } catch (err) {
        if (err.name !== 'AbortError') {
          console.error('Schema deploy resume failed:', err);
        }
      } finally {
        plannerPendingRef.current = null;
        stopLoadingMessages();
        setIsLoading(false);
      }
    },
    [
      activeConversationId,
      beginPlannerAbortableRequest,
      plannerPendingRef,
      runPlannerResumeStream,
      setChatHistory,
      setIsLoading,
      stopLoadingMessages,
    ],
  );

  const handlePlannerRecoveryDismiss = useCallback(async () => {
    const pending = plannerPendingRef.current;
    const conversationId = activeConversationId;
    if (!conversationId) return;

    setChatHistory((prev) =>
      prev.map((e) =>
        e.metadata?.event_type === 'planner_recovery_required' &&
        !e.plannerResolved
          ? {
              ...e,
              plannerResolved: true,
              metadata: { ...e.metadata, recovery_dismissed: true },
            }
          : e,
      ),
    );
    plannerPendingRef.current = null;

    try {
      await apiClient.post(`/chat/conversations/${conversationId}/recovery-dismiss`, {
        recovery_batch_id: pending?.recovery_batch_id,
        recovery_job_id: pending?.recovery_job_id,
      });
    } catch (err) {
      console.warn('Recovery dismiss persist failed:', err);
    }
  }, [activeConversationId, apiClient, plannerPendingRef, setChatHistory]);

  const handlePlannerRecoveryApprove = useCallback(
    async (skipRecovery = false) => {
      if (skipRecovery) {
        await handlePlannerRecoveryDismiss();
        return;
      }
      const pending = plannerPendingRef.current;
      const conversationId = activeConversationId;
      if (!pending || !conversationId) return;

      setChatHistory((prev) =>
        markPlannerEntriesResolved(prev, 'planner_recovery_required'),
      );

      const signal = beginPlannerAbortableRequest();
      try {
        await runPlannerResumeStream({
          conversationId,
          signal,
          loadingLabel: 'Applying migration fix',
          plannerResume: {
            planner_pending: pending,
            approve_recovery: true,
            skip_recovery: false,
          },
        });
      } catch (err) {
        if (err.name !== 'AbortError') {
          console.error('Recovery resume failed:', err);
        }
      } finally {
        plannerPendingRef.current = null;
        stopLoadingMessages();
        setIsLoading(false);
        setAgentTurnSteps([]);
        setOperationPreview?.(createEmptyOperationPreview());
      }
    },
    [
      activeConversationId,
      beginPlannerAbortableRequest,
      handlePlannerRecoveryDismiss,
      plannerPendingRef,
      runPlannerResumeStream,
      setAgentTurnSteps,
      setChatHistory,
      setIsLoading,
      setOperationPreview,
      stopLoadingMessages,
    ],
  );

  const handlePlannerClarificationSubmit = useCallback(
    async (selections) => {
      const pending = plannerPendingRef.current;
      const conversationId = activeConversationId;
      const planStatus = pending?.classification?.plan_status;
      const openClarifications =
        pending?.clarifications?.length ||
        pending?.classification?.clarifications?.length ||
        0;
      if (planStatus === 'awaiting_approval' && !openClarifications) return;

      const hasSelections = selections?.length > 0;
      if (!pending || !conversationId || !hasSelections) return;

      setChatHistory((prev) =>
        prev.map((e) =>
          (e.metadata?.event_type === 'planner_clarification_required' ||
            e.metadata?.event_type === 'planner_plan_execution_clarification') &&
          !isPlannerEntryResolved(e)
            ? withPlannerEntryResolved(e)
            : e,
        ),
      );

      const signal = beginPlannerAbortableRequest();
      try {
        const result = await runPlannerResumeStream({
          conversationId,
          signal,
          loadingLabel: 'Continuing with your selection',
          plannerResume: {
            selections: selections || [],
            planner_pending: pending,
            resume_plan_execution: Boolean(pending?.extract_step_clarification),
          },
        });
        const assistantText = (result || '').trim();
        if (
          assistantText &&
          !assistantText.includes('❌ Streaming failed') &&
          !plannerPendingRef.current
        ) {
          addMessage(assistantText, { type: 'system', timestamp: Date.now() });
          if (persistMessage) {
            await persistMessage(conversationId, 'assistant', assistantText);
          }
        }
      } catch (err) {
        if (err.name !== 'AbortError') {
          console.error('Planner resume stream failed:', err);
        }
      } finally {
        stopLoadingMessages();
        setIsLoading(false);
      }
    },
    [
      activeConversationId,
      addMessage,
      beginPlannerAbortableRequest,
      persistMessage,
      plannerPendingRef,
      runPlannerResumeStream,
      setChatHistory,
      setIsLoading,
      stopLoadingMessages,
    ],
  );

  const reportPlanRejected = useCallback(
    async (pending, editedPrompt = null) => {
      if (!apiClient || !pending) return;
      try {
        await apiClient.post('/api/learning/plan-rejected', {
          conversation_id: activeConversationId || pending.conversation_id || null,
          original_prompt: pending.original_prompt || null,
          edited_prompt: editedPrompt,
          planner_pending: pending,
        });
      } catch (_) {
        /* learning is best-effort */
      }
    },
    [activeConversationId, apiClient],
  );

  const handlePlannerPlanDismiss = useCallback(
    async (dismissContext = null) => {
      const ctx =
        dismissContext && typeof dismissContext === 'object' && dismissContext.pending
          ? dismissContext
          : { pending: dismissContext || plannerPendingRef.current, messageId: null };
      const pending = ctx.pending || plannerPendingRef.current;
      const messageId = ctx.messageId || null;
      const conversationId = activeConversationId;

      plannerPendingRef.current = null;
      setChatHistory((prev) =>
        prev.map((e) =>
          e.metadata?.event_type === 'planner_plan_ready' && !isPlannerEntryResolved(e)
            ? {
                ...withPlannerEntryResolved(e),
                metadata: {
                  ...(e.metadata || {}),
                  plan_dismissed: true,
                  planner_resolved: true,
                },
              }
            : e,
        ),
      );

      if (pending && conversationId && apiClient) {
        try {
          const cls = pending.classification || {};
          await apiClient.post(`/chat/conversations/${conversationId}/plan-dismiss`, {
            plan_id: pending.plan_id || cls.plan_id || null,
            message_id: messageId,
            planner_pending: pending,
          });
        } catch (err) {
          console.warn('Plan dismiss persist failed:', err);
          reportPlanRejected(pending);
        }
      } else if (pending) {
        reportPlanRejected(pending);
      }

      addMessage('Plan dismissed. Send a new message when you are ready to continue.', {
        type: 'system',
        timestamp: Date.now(),
      });
    },
    [activeConversationId, addMessage, apiClient, plannerPendingRef, reportPlanRejected, setChatHistory],
  );

  return {
    handlePlannerPlanApprove,
    handlePlannerPlanDismiss,
    handlePlannerSchemaDeployApprove,
    handlePlannerRecoveryApprove,
    handlePlannerRecoveryDismiss,
    handlePlannerClarificationSubmit,
    finalizePlannerStream,
  };
}
