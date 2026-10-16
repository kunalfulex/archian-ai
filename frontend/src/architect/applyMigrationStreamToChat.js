/**
 * Live migration UI from SSE (action API + architect stream).
 * Backend persists migration_started / migration_completed / recovery; React state only.
 */
import { createEmptyOperationPreview } from './reduceOperationPreview';
import { clearRecoveryLoaderUi } from './reduceRecoveryActivity';
import {
  resolvePlannerEntriesAfterPipeline,
  schedulePersistPlannerResolved,
} from './plannerState';

const TERMINAL_MERGE_DELAYS_MS = [500, 1000, 1500, 2500, 4000, 6000, 8000, 10000, 12000, 15000];
const activeTerminalMerges = new Map();
/** Jobs whose migration_completed chat row was merged from migrate SSE (skip live poll). */
const sseTerminalChatMergedJobs = new Set();
/** Batch ids whose recovery panel was merged from migrate SSE. */
const sseRecoveryMergedBatchIds = new Set();

export function resetMigrationSseTerminalMergeTracking() {
  sseTerminalChatMergedJobs.clear();
  sseRecoveryMergedBatchIds.clear();
}

function chatTimestampFromMessage(message = {}) {
  if (message.created_at) {
    return new Date(message.created_at).getTime();
  }
  return Date.now();
}

function mergeMigrationCompletedMessage({
  message,
  jobId,
  conversationId,
  setChatHistory,
  setMigrationStatuses,
  plannerPendingRef,
  updateMessageMetadata,
  stopLoadingMessages,
  setIsLoading,
  setOperationPreview,
  setAgentTurnSteps,
}) {
  if (!message?.id || !jobId) return null;

  let mergedMetadata = null;
  setChatHistory((prev) => {
    const existing = prev.find(
      (e) =>
        e.id === message.id ||
        (e.metadata?.event_type === 'migration_completed' && e.metadata?.job_id === jobId),
    );
    if (existing) {
      mergedMetadata = existing.metadata || message.metadata || null;
      return prev;
    }

    const entry = {
      id: message.id,
      type: 'system',
      text: message.content || '✅ **Migration Completed**',
      timestamp: chatTimestampFromMessage(message),
      metadata: message.metadata || { event_type: 'migration_completed', job_id: jobId },
    };
    mergedMetadata = entry.metadata;
    if (plannerPendingRef) plannerPendingRef.current = null;
    const next = resolvePlannerEntriesAfterPipeline([...prev, entry]);
    schedulePersistPlannerResolved(conversationId, next, updateMessageMetadata);
    return next;
  });

  if (setMigrationStatuses && mergedMetadata) {
    applyTerminalMigrationStatusFromMetadata(setMigrationStatuses, jobId, mergedMetadata);
  }
  stopLoadingMessages?.();
  setIsLoading?.(false);
  setAgentTurnSteps?.([]);
  setOperationPreview?.(createEmptyOperationPreview());
  sseTerminalChatMergedJobs.add(jobId);
  return mergedMetadata || message.metadata || {};
}

function mergeRecoveryMessage({
  message,
  batchId,
  jobId,
  setChatHistory,
  plannerPendingRef,
  setRecoveryActivity,
  setIsLoading,
  stopLoadingMessages,
}) {
  if (!message?.metadata?.recovery_proposal) return false;

  const batchIdMatch = batchId || jobId;
  let attached = false;
  setChatHistory((prev) => {
    const rid = recoveryBatchIdFromMetadata(message.metadata) || batchIdMatch;
    const exists = prev.some(
      (e) =>
        e.metadata?.event_type === 'planner_recovery_required' &&
        (!rid || recoveryBatchIdFromMetadata(e.metadata) === rid),
    );
    if (exists) {
      attached = true;
      return prev;
    }
    attached = true;
    plannerPendingRef.current = message.metadata.planner_pending || null;
    return [
      ...prev,
      {
        id: message.id || `recovery-${Date.now()}`,
        type: 'system',
        text: message.content || 'Migration recovery',
        timestamp: chatTimestampFromMessage(message),
        metadata: message.metadata,
      },
    ];
  });
  if (attached) {
    clearRecoveryLoaderUi({ setRecoveryActivity, setIsLoading, stopLoadingMessages });
    if (batchIdMatch) {
      sseRecoveryMergedBatchIds.add(String(batchIdMatch));
    }
  }
  return attached;
}

export function isPersistedChatMessagePayload(message) {
  return (
    message &&
    typeof message === 'object' &&
    message.id &&
    typeof message.metadata === 'object'
  );
}

export function buildMigrationStartedMetadata(data) {
  const jobId = data.job_id;
  const batchId = data.batch_id;
  const destination = data.destination || 'salesforce';
  const destinationName = data.destination_name || null;
  const totalRecords = data.total_records ?? data.record_count ?? 0;

  return {
    event_type: 'migration_started',
    job_id: jobId,
    batch_id: batchId,
    source_job_id: data.source_job_id || null,
    destination,
    destination_name: destinationName,
    destination_connector_id:
      data.destination_connector_id ||
      data.spreadsheet_id ||
      data.destination_spreadsheet_id ||
      null,
    destination_spreadsheet_id:
      data.destination_spreadsheet_id || data.spreadsheet_id || null,
    spreadsheet_id: data.spreadsheet_id || data.destination_spreadsheet_id || null,
    record_count: totalRecords,
    total_records: totalRecords,
    ...(data.source_type ? { source_type: data.source_type } : {}),
    ...(data.extracted_schema_type
      ? { extracted_schema_type: data.extracted_schema_type }
      : {}),
    ...(data.sql_query ? { sql_query: data.sql_query } : {}),
    ...(data.source_connector_id
      ? {
          source_connector_id: data.source_connector_id,
          connector_id: data.source_connector_id,
        }
      : {}),
    ...(data.table_name ? { table_name: data.table_name } : {}),
  };
}

function terminalStatusFromJobStatus(jobStatus = {}) {
  const raw = String(jobStatus.status || jobStatus.result?.status || '').toLowerCase();
  if (raw === 'success' || raw === 'completed') return 'completed';
  if (raw === 'failed') return 'failed';
  if (raw === 'cancelled') return 'cancelled';
  return null;
}

function countsFromJobStatus(jobStatus = {}) {
  const result = jobStatus.result || {};
  const successCount =
    result.summary?.successful ||
    result.records_migrated ||
    result.successful_records ||
    0;
  const errorCount = result.summary?.failed || result.failed_records || 0;
  const totalRecords =
    result.total_records || jobStatus.total_records || successCount + errorCount;
  return { successCount, errorCount, totalRecords };
}

function terminalMessageFromCompletedMetadata(metadata = {}) {
  const eventStatus = metadata.status || 'success';
  const errorCount = metadata.error_count ?? 0;
  if (eventStatus === 'cancelled') {
    return 'Partial progress saved. You can rollback migrated records if needed.';
  }
  if (eventStatus === 'failed') {
    return metadata.error || 'Migration failed';
  }
  if (errorCount > 0) {
    return `Migration completed with ${errorCount} failed record${errorCount === 1 ? '' : 's'}`;
  }
  return 'Migration completed successfully';
}

function applyTerminalMigrationStatusFromMetadata(setMigrationStatuses, jobId, metadata) {
  if (!setMigrationStatuses || !jobId || !metadata) return;
  const eventStatus = metadata.status || 'success';
  const resolvedStatus =
    eventStatus === 'cancelled'
      ? 'cancelled'
      : eventStatus === 'failed'
        ? 'failed'
        : 'completed';
  const errorCount = metadata.error_count ?? 0;
  setMigrationStatuses((prev) => ({
    ...prev,
    [jobId]: {
      ...prev[jobId],
      jobId,
      batchId: metadata.batch_id || prev[jobId]?.batchId,
      status: resolvedStatus,
      progress: 100,
      successCount: metadata.success_count ?? prev[jobId]?.successCount ?? 0,
      errorCount,
      totalRecords:
        prev[jobId]?.totalRecords ||
        (metadata.success_count ?? 0) + errorCount ||
        0,
      message: terminalMessageFromCompletedMetadata(metadata),
      destination: metadata.destination || prev[jobId]?.destination,
      destinationName: metadata.destination_name || prev[jobId]?.destinationName,
    },
  }));
}

function recoveryBatchIdFromMetadata(metadata = {}) {
  return metadata?.planner_pending?.recovery_batch_id || metadata?.recovery_batch_id || null;
}

/**
 * Merge backend-persisted migration_completed into chatHistory (no client persist).
 */
export async function mergeMigrationCompletedFromServer({
  conversationId,
  jobId,
  batchId,
  setChatHistory,
  setMigrationStatuses,
  plannerPendingRef,
  updateMessageMetadata,
  stopLoadingMessages,
  setIsLoading,
  setOperationPreview,
  setAgentTurnSteps,
  maxAttempts = 1,
}) {
  if (!conversationId || !jobId) return null;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, 350 * attempt));
    }
    try {
      const { default: api } = await import('../api');
      const response = await api.get(`/chat/conversations/${conversationId}/messages`, {
        params: { limit: 60 },
      });
      const messages = response.data?.messages || [];
      const completed = messages.find(
        (m) =>
          m?.metadata?.event_type === 'migration_completed' &&
          (m.metadata?.job_id === jobId ||
            (batchId && m.metadata?.batch_id === batchId)),
      );
      if (!completed?.id) continue;

      const metadata = mergeMigrationCompletedMessage({
        message: completed,
        jobId,
        conversationId,
        setChatHistory,
        setMigrationStatuses,
        plannerPendingRef,
        updateMessageMetadata,
        stopLoadingMessages,
        setIsLoading,
        setOperationPreview,
        setAgentTurnSteps,
      });
      return metadata;
    } catch (err) {
      console.warn('mergeMigrationCompletedFromServer attempt failed:', err);
    }
  }
  return null;
}

/**
 * Merge backend-persisted planner_recovery_required into chatHistory (deduped).
 */
export async function mergeRecoveryFromServer({
  conversationId,
  batchId,
  jobId,
  setChatHistory,
  plannerPendingRef,
  setRecoveryActivity,
  setIsLoading,
  stopLoadingMessages,
  maxAttempts = 1,
}) {
  if (!conversationId) return false;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, 350 * attempt));
    }
    try {
      const { default: api } = await import('../api');
      const response = await api.get(`/chat/conversations/${conversationId}/messages`, {
        params: { limit: 60 },
      });
      const messages = response.data?.messages || [];
      const batchIdMatch = batchId || jobId;
      const recovery = [...messages]
        .reverse()
        .find((m) => {
          if (m.metadata?.event_type !== 'planner_recovery_required') return false;
          if (!m.metadata?.recovery_proposal) return false;
          const rid = recoveryBatchIdFromMetadata(m.metadata);
          if (!batchIdMatch) return true;
          return rid === batchIdMatch;
        });
      if (!recovery?.metadata?.recovery_proposal) continue;

      const attached = mergeRecoveryMessage({
        message: recovery,
        batchId: batchIdMatch,
        jobId,
        setChatHistory,
        plannerPendingRef,
        setRecoveryActivity,
        setIsLoading,
        stopLoadingMessages,
      });
      if (attached) return true;
    } catch (err) {
      console.warn('mergeRecoveryFromServer attempt failed:', err);
    }
  }
  return false;
}

/**
 * Wait for backend-persisted migration_completed + recovery (architect stream / reload).
 */
export async function syncMigrationTerminalFromServer({
  conversationId,
  jobId,
  batchId,
  setChatHistory,
  setMigrationStatuses,
  plannerPendingRef,
  updateMessageMetadata,
  clearMigrationStatusPropsCache,
  stopLoadingMessages,
  setIsLoading,
  setRecoveryActivity,
  setOperationPreview,
  setAgentTurnSteps,
  maxAttempts = TERMINAL_MERGE_DELAYS_MS.length,
}) {
  if (!conversationId || !jobId) return false;

  const mergeKey = `${conversationId}:${jobId}`;
  if (activeTerminalMerges.has(mergeKey)) {
    return activeTerminalMerges.get(mergeKey);
  }

  const work = (async () => {
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      if (attempt > 0) {
        await new Promise((resolve) =>
          setTimeout(resolve, TERMINAL_MERGE_DELAYS_MS[attempt - 1] ?? 2000),
        );
      }

      const completedMeta = await mergeMigrationCompletedFromServer({
        conversationId,
        jobId,
        batchId,
        setChatHistory,
        setMigrationStatuses,
        plannerPendingRef,
        updateMessageMetadata,
        stopLoadingMessages,
        setIsLoading,
        setOperationPreview,
        setAgentTurnSteps,
        maxAttempts: 1,
      });

      if (completedMeta) {
        clearMigrationStatusPropsCache?.();
        const errorCount = completedMeta.error_count ?? 0;
        if (errorCount > 0) {
          await mergeRecoveryFromServer({
            conversationId,
            batchId: completedMeta.batch_id || batchId,
            jobId,
            setChatHistory,
            plannerPendingRef,
            setRecoveryActivity,
            setIsLoading,
            stopLoadingMessages,
            maxAttempts: 3,
          });
        }
        return true;
      }
    }
    return false;
  })();

  activeTerminalMerges.set(mergeKey, work);
  try {
    return await work;
  } finally {
    activeTerminalMerges.delete(mergeKey);
  }
}

export async function syncPendingMigrationJobsAfterArchitectStream(deps) {
  const conversationId = deps.conversationId || deps.activeConversationId;
  if (!conversationId) return;

  const statuses =
    typeof deps.getMigrationStatuses === 'function'
      ? deps.getMigrationStatuses()
      : deps.migrationStatuses || {};

  const pending = [];
  for (const [jobId, st] of Object.entries(statuses)) {
    if (!jobId || !st) continue;
    if (st.status === 'processing' || st.status === 'in_progress') {
      pending.push({ jobId, batchId: st.batchId });
    }
  }

  for (const { jobId, batchId } of pending) {
    if (sseTerminalChatMergedJobs.has(jobId)) continue;
    await syncMigrationTerminalFromServer({
      conversationId,
      jobId,
      batchId,
      setChatHistory: deps.setChatHistory,
      setMigrationStatuses: deps.setMigrationStatuses,
      plannerPendingRef: deps.plannerPendingRef,
      updateMessageMetadata: deps.updateMessageMetadata,
      clearMigrationStatusPropsCache: deps.clearMigrationStatusPropsCache,
      maxAttempts: 3,
    });
  }
}

export async function applyMigrationStarted(data, deps) {
  const {
    setChatHistory,
    setMigrationStatuses,
    stopLoadingMessages,
    setIsLoading,
    setAgentTurnSteps,
    setOperationPreview,
    activeJobIdsRef,
    clearMigrationStatusPropsCache,
  } = deps;

  stopLoadingMessages?.();
  setIsLoading?.(false);
  setAgentTurnSteps?.([]);
  setOperationPreview?.(createEmptyOperationPreview());

  const jobId = data?.job_id;
  const batchId = data?.batch_id;
  if (!jobId || !batchId) return;

  activeJobIdsRef?.current?.add(jobId);

  const migrationMetadata = buildMigrationStartedMetadata(data);
  const destination = migrationMetadata.destination;
  const destinationName = migrationMetadata.destination_name;
  const totalRecords = migrationMetadata.total_records;

  const migrationEntry = {
    id: `migration-${jobId}`,
    type: 'migration_status',
    text: '',
    timestamp: Date.now(),
    metadata: migrationMetadata,
    jobId,
    batchId,
  };

  setChatHistory((prev) => {
    const exists = prev.some(
      (e) => (e.jobId === jobId || e.batchId === batchId) && e.type === 'migration_status',
    );
    if (exists) return prev;
    return [...prev, migrationEntry];
  });

  setMigrationStatuses((prev) => {
    const existing = prev[jobId];
    if (
      existing &&
      (existing.status === 'completed' ||
        existing.status === 'failed' ||
        existing.status === 'cancelled')
    ) {
      return prev;
    }
    return {
      ...prev,
      [jobId]: {
        jobId,
        batchId,
        status: 'processing',
        progress: existing?.progress ?? 0,
        destination,
        destinationName,
        totalRecords,
        successCount: existing?.successCount,
        errorCount: existing?.errorCount,
        message:
          existing?.message ||
          `Migrating 0 of ${Number(totalRecords).toLocaleString()} records…`,
      },
    };
  });

  clearMigrationStatusPropsCache?.();
}

export function applyMigrationCompletedFromStream(evt, deps) {
  const jobId = evt?.job_id;
  const message = evt?.message;
  const conversationId = deps.conversationId || deps.activeConversationId;
  if (!jobId || !isPersistedChatMessagePayload(message)) return null;

  deps.clearMigrationStatusPropsCache?.();
  return mergeMigrationCompletedMessage({
    message,
    jobId,
    conversationId,
    setChatHistory: deps.setChatHistory,
    setMigrationStatuses: deps.setMigrationStatuses,
    plannerPendingRef: deps.plannerPendingRef,
    updateMessageMetadata: deps.updateMessageMetadata,
    stopLoadingMessages: deps.stopLoadingMessages,
    setIsLoading: deps.setIsLoading,
    setOperationPreview: deps.setOperationPreview,
    setAgentTurnSteps: deps.setAgentTurnSteps,
  });
}

export function applyMigrationRecoveryFromStream(evt, deps) {
  const message = evt?.message;
  if (!isPersistedChatMessagePayload(message)) return false;

  const attached = mergeRecoveryMessage({
    message,
    batchId: evt?.batch_id,
    jobId: evt?.job_id,
    setChatHistory: deps.setChatHistory,
    plannerPendingRef: deps.plannerPendingRef,
    setRecoveryActivity: deps.setRecoveryActivity,
    setIsLoading: deps.setIsLoading,
    stopLoadingMessages: deps.stopLoadingMessages,
  });
  if (attached) {
    deps.clearMigrationStatusPropsCache?.();
  }
  return attached;
}

export function applyMigrationProgress(data, deps) {
  const { setMigrationStatuses, clearMigrationStatusPropsCache } = deps;
  const jobId = data?.job_id;
  if (!jobId) return;

  const totalRecords = Number(data.total_records || 0);
  const processedRecords = Number(data.processed_records || 0);
  const progress =
    data.progress != null
      ? Number(data.progress)
      : totalRecords > 0
        ? Math.round((processedRecords / totalRecords) * 100)
        : 0;

  setMigrationStatuses((prev) => ({
    ...prev,
    [jobId]: {
      ...prev[jobId],
      jobId,
      batchId: data.batch_id || prev[jobId]?.batchId,
      status: 'processing',
      progress,
      totalRecords: totalRecords || prev[jobId]?.totalRecords,
      successCount: data.successful_records ?? prev[jobId]?.successCount,
      errorCount: data.failed_records ?? prev[jobId]?.errorCount,
      message:
        data.message ||
        (totalRecords
          ? `Migrating ${processedRecords.toLocaleString()} of ${totalRecords.toLocaleString()} records…`
          : 'Migration in progress…'),
      destination: data.destination || prev[jobId]?.destination,
    },
  }));

  clearMigrationStatusPropsCache?.();
}

export async function applyMigrationFinished(evt, deps) {
  const jobId = evt?.job_id;
  const conversationId = deps.conversationId || deps.activeConversationId;
  if (!jobId) return;

  const js = evt.job_status || {};
  const terminal = terminalStatusFromJobStatus(js);
  const batchId = js.batch_id || js.result?.batch_id || evt.batch_id;

  if (terminal) {
    const { successCount, errorCount, totalRecords } = countsFromJobStatus(js);
    const { setMigrationStatuses, clearMigrationStatusPropsCache } = deps;

    deps.stopLoadingMessages?.();
    deps.setIsLoading?.(false);
    deps.setAgentTurnSteps?.([]);
    deps.setOperationPreview?.(createEmptyOperationPreview());

    setMigrationStatuses((prev) => ({
      ...prev,
      [jobId]: {
        ...prev[jobId],
        jobId,
        batchId: batchId || prev[jobId]?.batchId,
        status: terminal,
        progress: terminal === 'completed' ? 100 : prev[jobId]?.progress ?? 0,
        successCount,
        errorCount,
        totalRecords: totalRecords || prev[jobId]?.totalRecords,
        message:
          terminal === 'failed'
            ? js.error || js.result?.message || 'Migration failed'
            : terminal === 'cancelled'
              ? 'Migration cancelled'
              : errorCount > 0
                ? `Migration completed with ${errorCount} failed record${errorCount === 1 ? '' : 's'}`
                : js.result?.message || 'Migration completed successfully',
        destination: js.destination || prev[jobId]?.destination,
        destinationName: js.destination_name || prev[jobId]?.destinationName,
      },
    }));
    clearMigrationStatusPropsCache?.();
  }

  if (!conversationId) return;

  const resolvedBatchId = batchId || js.batch_id || js.result?.batch_id || evt.batch_id;
  const errorCount = countsFromJobStatus(js).errorCount;

  if (!sseTerminalChatMergedJobs.has(jobId)) {
    await syncMigrationTerminalFromServer({
      conversationId,
      jobId,
      batchId: resolvedBatchId,
      setChatHistory: deps.setChatHistory,
      setMigrationStatuses: deps.setMigrationStatuses,
      plannerPendingRef: deps.plannerPendingRef,
      updateMessageMetadata: deps.updateMessageMetadata,
      clearMigrationStatusPropsCache: deps.clearMigrationStatusPropsCache,
      maxAttempts: 3,
    });
  } else if (
    errorCount > 0 &&
    resolvedBatchId &&
    !sseRecoveryMergedBatchIds.has(String(resolvedBatchId))
  ) {
    await mergeRecoveryFromServer({
      conversationId,
      batchId: resolvedBatchId,
      jobId,
      setChatHistory: deps.setChatHistory,
      plannerPendingRef: deps.plannerPendingRef,
      setRecoveryActivity: deps.setRecoveryActivity,
      setIsLoading: deps.setIsLoading,
      stopLoadingMessages: deps.stopLoadingMessages,
      maxAttempts: 2,
    });
    deps.clearMigrationStatusPropsCache?.();
  }
}

/**
 * Shared SSE handlers for architect stream + migrate action API.
 */
export function createMigrationStreamHandlers(deps) {
  return {
    onMigrationStarted: async (data, conversationId) => {
      await applyMigrationStarted(data, {
        ...deps,
        conversationId: conversationId || deps.activeConversationId,
      });
    },
    onMigrationProgress: (data) => {
      applyMigrationProgress(data, deps);
    },
    onMigrationJobFinished: async (evt, conversationId) => {
      await applyMigrationFinished(evt, {
        ...deps,
        conversationId: conversationId || deps.activeConversationId,
      });
    },
    onMigrationCompletedChat: (evt, conversationId) => {
      applyMigrationCompletedFromStream(evt, {
        ...deps,
        conversationId: conversationId || deps.activeConversationId,
      });
    },
    onMigrationRecoveryChat: (evt, conversationId) => {
      applyMigrationRecoveryFromStream(evt, {
        ...deps,
        conversationId: conversationId || deps.activeConversationId,
      });
    },
  };
}
