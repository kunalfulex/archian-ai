/**
 * Pure planner v2 state helpers — chat history display, pending ref for UI actions.
 */

export const HIDDEN_CHAT_DATA_EVENT_TYPES = new Set([
  'plan_execution_started',
  'plan_step_checkpoint',
  'spreadsheet_creation_started',
]);

const PLANNER_INTERACTIVE_EVENTS = new Set([
  'planner_plan_ready',
  'planner_clarification_required',
  'planner_plan_execution_clarification',
]);

/** Pipeline progress after a planner message means that thread is finished (mirrors backend plan_context). */
const PLANNER_PROGRESS_AFTER_PENDING_EVENTS = new Set([
  'migration_completed',
  'migration_started',
  'extraction_completed',
  'mapping_completed',
  'mapping_started',
  'spreadsheet_creation_completed',
]);

const PLAN_EXECUTED_PROGRESS_EVENTS = new Set([
  'migration_completed',
  'migration_started',
  'extraction_completed',
  'spreadsheet_creation_completed',
]);

function isSyntheticPipelinePreviewEntry(entry) {
  return (
    entry?.type === 'data_preview' ||
    entry?.type === 'transformation_preview' ||
    entry?.type === 'migration_status'
  );
}

/** Progress that means an approved planner plan already ran (extract/migrate started or finished). */
export function isPlanExecutedProgressEntry(entry) {
  if (!entry) return false;
  const eventType = entry.metadata?.event_type;
  if (PLAN_EXECUTED_PROGRESS_EVENTS.has(eventType)) return true;
  if (isSyntheticPipelinePreviewEntry(entry) && entry.jobId) return true;
  return false;
}

/** Progress that means a planner clarify/plan card should no longer be interactive. */
export function isPlannerPipelineProgressEntry(entry) {
  if (!entry) return false;
  const eventType = entry.metadata?.event_type;
  if (PLANNER_PROGRESS_AFTER_PENDING_EVENTS.has(eventType)) return true;
  return isPlanExecutedProgressEntry(entry);
}

const PERSISTED_MESSAGE_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isPersistedChatMessageId(id) {
  return PERSISTED_MESSAGE_ID.test(String(id || ''));
}

export function isPlannerEntryResolved(entry) {
  if (!entry) return false;
  if (entry.plannerResolved) return true;
  const meta = entry.metadata || {};
  return Boolean(meta.planner_resolved || meta.plannerResolved);
}

export function plannerEntrySupersededByProgress(entries, entryIndex) {
  if (entryIndex < 0) return false;
  for (let i = entryIndex + 1; i < entries.length; i++) {
    if (isPlannerPipelineProgressEntry(entries[i])) {
      return true;
    }
  }
  return false;
}

function plannerEntrySupersededByLaterPlanner(entries, entryIndex) {
  if (entryIndex < 0) return false;
  for (let i = entryIndex + 1; i < entries.length; i++) {
    const eventType = entries[i]?.metadata?.event_type;
    if (
      eventType === 'planner_plan_ready' ||
      eventType === 'planner_clarification_required' ||
      eventType === 'planner_plan_execution_clarification'
    ) {
      return true;
    }
  }
  return false;
}

/** Mark planner clarify/plan cards resolved when the pipeline moved on or a later planner step exists. */
export function markSupersededPlannerEntriesResolved(entries) {
  return entries.map((e, idx) => {
    if (isPlannerEntryResolved(e)) return e;
    const eventType = e.metadata?.event_type;
    if (!PLANNER_INTERACTIVE_EVENTS.has(eventType)) return e;
    if (
      plannerEntrySupersededByProgress(entries, idx) ||
      plannerEntrySupersededByLaterPlanner(entries, idx)
    ) {
      return withPlannerEntryResolved(e);
    }
    return e;
  });
}

export function withPlannerEntryResolved(entry) {
  return {
    ...entry,
    plannerResolved: true,
    metadata: { ...(entry.metadata || {}), planner_resolved: true },
  };
}

export function schedulePersistPlannerResolved(conversationId, entries, updateMessageMetadata) {
  if (!conversationId || typeof updateMessageMetadata !== 'function') return;
  const persistedPlanById = new Map();
  for (const entry of entries || []) {
    if (entry.metadata?.event_type !== 'planner_plan_ready') continue;
    const planId = entry.metadata?.plan_id;
    if (!planId || !isPersistedChatMessageId(entry.id)) continue;
    persistedPlanById.set(planId, entry);
  }
  for (const entry of entries || []) {
    if (!PLANNER_INTERACTIVE_EVENTS.has(entry.metadata?.event_type)) continue;
    if (!isPlannerEntryResolved(entry)) continue;
    const metadata = { ...(entry.metadata || {}), planner_resolved: true };
    if (isPersistedChatMessageId(entry.id)) {
      updateMessageMetadata(conversationId, entry.id, metadata).catch(() => {});
      continue;
    }
    const planId = entry.metadata?.plan_id;
    const persisted = planId ? persistedPlanById.get(planId) : null;
    if (persisted?.id) {
      updateMessageMetadata(conversationId, persisted.id, {
        ...(persisted.metadata || {}),
        planner_resolved: true,
      }).catch(() => {});
    }
  }
}

export function resolveFreshSupersededPlannerEntries(entries) {
  return (entries || []).map((e) => {
    const et = e.metadata?.event_type;
    if (
      PLANNER_INTERACTIVE_EVENTS.has(et) &&
      !isPlannerEntryResolved(e)
    ) {
      return withPlannerEntryResolved(e);
    }
    return e;
  });
}

/** Only the latest unresolved planner clarify/plan message should show interactive UI. */
export function resolveStalePlannerChatEntries(entries) {
  let latestId = null;
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (isPlannerEntryResolved(e)) continue;
    const et = e.metadata?.event_type;
    if (
      et === 'planner_plan_ready' &&
      (e.metadata?.plan_steps_summary?.length || e.metadata?.planner_pending)
    ) {
      latestId = e.id;
      break;
    }
    if (
      et === 'planner_clarification_required' &&
      (e.metadata?.clarifications?.length || e.metadata?.planner_pending || (e.text || '').trim())
    ) {
      latestId = e.id;
      break;
    }
    if (et === 'planner_plan_execution_clarification' && e.metadata?.planner_pending) {
      latestId = e.id;
      break;
    }
  }
  if (!latestId) return entries;
  return entries.map((e) => {
    const et = e.metadata?.event_type;
    if (
      e.id !== latestId &&
      (et === 'planner_plan_ready' || et === 'planner_clarification_required' || et === 'planner_plan_execution_clarification') &&
      !isPlannerEntryResolved(e)
    ) {
      return withPlannerEntryResolved(e);
    }
    return e;
  });
}

/** Drop duplicate persisted planner plan rows (backend + frontend both used to save). */
export function dedupePlannerPlanMessages(entries) {
  const lastIndexByPlanId = new Map();
  entries.forEach((e, i) => {
    if (e.metadata?.event_type !== 'planner_plan_ready') return;
    const planId = e.metadata?.plan_id;
    if (planId) lastIndexByPlanId.set(planId, i);
  });
  if (lastIndexByPlanId.size === 0) return entries;
  return entries.filter((e, i) => {
    if (e.metadata?.event_type !== 'planner_plan_ready') return true;
    const planId = e.metadata?.plan_id;
    if (!planId) return true;
    return lastIndexByPlanId.get(planId) === i;
  });
}

export function inferPlannerPlanExecuted(entries, planEntry) {
  if (!planEntry) return false;
  const planTs = planEntry.timestamp || 0;
  const planIdx = entries.indexOf(planEntry);
  const afterPlan =
    planIdx >= 0
      ? entries.slice(planIdx + 1)
      : entries.filter((m) => (m.timestamp || 0) > planTs);
  return afterPlan.some((m) => isPlanExecutedProgressEntry(m));
}

export function markExecutedPlannerPlansResolved(entries) {
  return entries.map((e) => {
    if (isPlannerEntryResolved(e) || e.metadata?.event_type !== 'planner_plan_ready') return e;
    if (inferPlannerPlanExecuted(entries, e)) {
      return withPlannerEntryResolved(e);
    }
    return e;
  });
}

export function plannerPlanStatus(pending) {
  return (
    pending?.classification?.plan_status ||
    pending?.plan_status ||
    pending?.classification?.pipeline_state ||
    null
  );
}

export function hasActivePlannerPlanPanel(history) {
  return (history || []).some(
    (e) => e.metadata?.event_type === 'planner_plan_ready' && !isPlannerEntryResolved(e),
  );
}

export function hasActivePlannerClarificationPanel(history) {
  return (history || []).some(
    (e) =>
      (e.metadata?.event_type === 'planner_clarification_required' ||
        e.metadata?.event_type === 'planner_plan_execution_clarification') &&
      !isPlannerEntryResolved(e),
  );
}

export function chatHasCompletedPipeline(history) {
  return (history || []).some((m) => m.metadata?.event_type === 'migration_completed');
}

export function resolvePlannerEntriesAfterPipeline(entries) {
  return markExecutedPlannerPlansResolved(
    (entries || []).map((e) => {
      if (PLANNER_INTERACTIVE_EVENTS.has(e.metadata?.event_type) && !isPlannerEntryResolved(e)) {
        return withPlannerEntryResolved(e);
      }
      return e;
    }),
  );
}

export function latestActivePlannerEntryId(entries) {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (isPlannerEntryResolved(e)) continue;
    const et = e.metadata?.event_type;
    if (
      et === 'planner_plan_ready' &&
      (e.metadata?.plan_steps_summary?.length || e.metadata?.planner_pending)
    ) {
      return e.id;
    }
    if (
      et === 'planner_clarification_required' &&
      (e.metadata?.clarifications?.length || e.metadata?.planner_pending || (e.text || '').trim())
    ) {
      return e.id;
    }
    if (et === 'planner_plan_execution_clarification' && e.metadata?.planner_pending) {
      return e.id;
    }
  }
  return null;
}

export function preparePlannerChatEntries(entries) {
  return resolveStalePlannerChatEntries(
    markExecutedPlannerPlansResolved(
      markSupersededPlannerEntriesResolved(dedupePlannerPlanMessages(entries)),
    ),
  );
}

export function resolvePlannerPendingFromLoadedMessages(mappedMessages) {
  const pendingPlannerMsg = [...mappedMessages]
    .reverse()
    .find(
      (m) =>
        (m.metadata?.event_type === 'planner_clarification_required' ||
          m.metadata?.event_type === 'planner_plan_ready' ||
          m.metadata?.event_type === 'planner_plan_execution_clarification') &&
        m.metadata?.planner_pending &&
        !isPlannerEntryResolved(m),
    );
  if (!pendingPlannerMsg) return null;

  const pendingIdx = mappedMessages.indexOf(pendingPlannerMsg);
  if (plannerEntrySupersededByProgress(mappedMessages, pendingIdx)) {
    return null;
  }

  let pending = pendingPlannerMsg.metadata?.planner_pending || null;
  const pendingStatus =
    pendingPlannerMsg.metadata?.plan_status ||
    plannerPlanStatus(pending);

  if (
    pendingStatus !== 'awaiting_approval' &&
    pendingStatus !== 'awaiting_clarification' &&
    !pending?.extract_step_clarification
  ) {
    return null;
  }

  if (pending && pendingPlannerMsg.metadata?.pipeline_state) {
    pending = {
      ...pending,
      pipeline_state: pendingPlannerMsg.metadata.pipeline_state,
    };
  }
  return pending;
}

export function syncPlannerPendingRefFromEntry(plannerPendingRef, chatHistory, activePlannerEntryId) {
  if (!activePlannerEntryId) {
    plannerPendingRef.current = null;
    return;
  }
  const entry = chatHistory.find((e) => e.id === activePlannerEntryId);
  if (!entry?.metadata?.planner_pending) return;
  if (inferPlannerPlanExecuted(chatHistory, entry) || isPlannerEntryResolved(entry)) {
    plannerPendingRef.current = null;
    return;
  }
  plannerPendingRef.current = entry.metadata.planner_pending;
}

export function markPlannerEntriesResolved(prev, eventType) {
  return prev.map((e) =>
    e.metadata?.event_type === eventType && !isPlannerEntryResolved(e)
      ? withPlannerEntryResolved(e)
      : e,
  );
}

export function freezeOlderPlannerEntries(prev) {
  return prev.map((e) => {
    const et = e.metadata?.event_type;
    if (
      (et === 'planner_clarification_required' || et === 'planner_plan_ready') &&
      !isPlannerEntryResolved(e)
    ) {
      return withPlannerEntryResolved(e);
    }
    return e;
  });
}

export function buildConversationHistorySlice(chatHistory, limit = 24) {
  return (chatHistory || []).slice(-limit).map((msg) => {
    const baseMessage = { type: msg.type, text: msg.text };
    if (msg.metadata) baseMessage.metadata = { ...msg.metadata };
    if (msg.plannerResolved) baseMessage.plannerResolved = true;
    return baseMessage;
  });
}

function plannerClarificationHasOptions(entry) {
  const clars = entry.metadata?.clarifications || [];
  const chatOnlyTypes = new Set([
    'create_workbook_name',
    'pick_create_headers',
    'pick_extract_fields',
  ]);
  if (
    clars.some(
      (c) =>
        c?.required !== false &&
        !c?.selected_option_id &&
        c?.clarification_type === 'pick_source_entity' &&
        !(c?.options || []).length,
    )
  ) {
    return false;
  }
  if (
    clars.some(
      (c) =>
        c?.required !== false &&
        !c?.selected_option_id &&
        chatOnlyTypes.has(c?.clarification_type) &&
        !(c?.options || []).length,
    )
  ) {
    return false;
  }
  return clars.some(
    (c) => c?.required !== false && !c?.selected_option_id && (c?.options || []).length > 0,
  );
}

export function getPlannerEntryRenderState(entry, activePlannerEntryId) {
  const isActivePlannerEntry = activePlannerEntryId && entry.id === activePlannerEntryId;
  const resolved = isPlannerEntryResolved(entry);
  const showPlannerClarPanel = Boolean(
    isActivePlannerEntry &&
      (entry.metadata?.event_type === 'planner_clarification_required' ||
        entry.metadata?.event_type === 'planner_plan_execution_clarification') &&
      !resolved &&
      (plannerClarificationHasOptions(entry) ||
        entry.metadata?.planner_pending?.extract_step_clarification),
  );
  const showPlannerPlanPanel =
    isActivePlannerEntry &&
    entry.metadata?.event_type === 'planner_plan_ready' &&
    !resolved &&
    (entry.metadata?.plan_steps_summary?.length > 0 || entry.metadata?.planner_pending);
  const hidePlannerMarkdown = Boolean(
    (entry.metadata?.event_type === 'planner_plan_ready' ||
      entry.metadata?.event_type === 'planner_clarification_required' ||
      entry.metadata?.event_type === 'planner_plan_execution_clarification') &&
      (resolved || !isActivePlannerEntry || showPlannerClarPanel),
  );

  return { isActivePlannerEntry, showPlannerClarPanel, showPlannerPlanPanel, hidePlannerMarkdown };
}

export function shouldHideChatEntry(entry) {
  if (entry.metadata?.event_type === 'extraction_started' && !entry.text) {
    return true;
  }
  if (HIDDEN_CHAT_DATA_EVENT_TYPES.has(entry.metadata?.event_type)) {
    return true;
  }
  if (entry.metadata?.event_type === 'spreadsheet_created' && !entry.text?.trim()) {
    return true;
  }
  if (
    entry.metadata?.event_type === 'planner_plan_ready' &&
    isPlannerEntryResolved(entry)
  ) {
    return true;
  }
  return false;
}
