/**
 * Reducer for append-only recovery activity stream (post-migrate failure analysis).
 */

export const RECOVERY_ACTIVITY_STATUS = {
  RUNNING: 'running',
  DONE: 'done',
  ERROR: 'error',
  FAILED: 'failed',
};

export function createEmptyRecoveryActivity(overrides = {}) {
  return {
    active: false,
    jobId: null,
    batchId: null,
    items: [],
    ...overrides,
  };
}

function upsertRecoveryItem(items, event) {
  const activityId = (event.activity_id || '').trim();
  if (!activityId) return items;

  const nextItem = {
    activityId,
    label: (event.label || '').trim() || 'Working',
    status: (event.status || RECOVERY_ACTIVITY_STATUS.RUNNING).toLowerCase(),
    detail: event.detail ? String(event.detail).trim() : null,
  };

  const idx = items.findIndex((item) => item.activityId === activityId);
  let next;
  if (idx < 0) {
    next = [...items, nextItem];
  } else {
    next = [...items];
    next[idx] = { ...next[idx], ...nextItem };
  }
  return enforceSingleRunningSpinner(next);
}

/** Only the latest running step shows a spinner; earlier running steps read as done. */
export function enforceSingleRunningSpinner(items) {
  if (!items?.length) return [];
  let lastRunningIdx = -1;
  for (let i = items.length - 1; i >= 0; i -= 1) {
    if (items[i].status === RECOVERY_ACTIVITY_STATUS.RUNNING) {
      lastRunningIdx = i;
      break;
    }
  }
  if (lastRunningIdx < 0) return items;
  return items.map((item, i) => {
    if (item.status === RECOVERY_ACTIVITY_STATUS.RUNNING && i !== lastRunningIdx) {
      return { ...item, status: RECOVERY_ACTIVITY_STATUS.DONE };
    }
    return item;
  });
}

export function displayStatusForRecoveryItem(items, item) {
  const list = enforceSingleRunningSpinner(items || []);
  const match = list.find((row) => row.activityId === item.activityId);
  return match?.status || item.status;
}

export function reduceRecoveryActivity(prev, event) {
  if (!event) return prev || createEmptyRecoveryActivity();

  if (event.event_type === 'recovery_analysis_started') {
    return {
      ...(prev || createEmptyRecoveryActivity()),
      active: true,
      jobId: event.job_id || prev?.jobId || null,
      batchId: event.batch_id || prev?.batchId || null,
      items: prev?.items || [],
    };
  }

  if (event.event_type !== 'recovery_activity') {
    return prev || createEmptyRecoveryActivity();
  }

  const base = prev || createEmptyRecoveryActivity();
  return {
    ...base,
    active: true,
    jobId: event.job_id || base.jobId,
    batchId: event.batch_id || base.batchId,
    items: upsertRecoveryItem(base.items || [], event),
  };
}

export function completeRecoveryActivity(prev) {
  const base = prev || createEmptyRecoveryActivity();
  const items = (base.items || []).map((item) =>
    item.status === RECOVERY_ACTIVITY_STATUS.RUNNING
      ? { ...item, status: RECOVERY_ACTIVITY_STATUS.DONE }
      : item,
  );
  return {
    ...base,
    active: false,
    items,
  };
}

export function hasRunningRecoveryStep(preview) {
  return (preview?.items || []).some((item) => item.status === RECOVERY_ACTIVITY_STATUS.RUNNING);
}

/** Label for the current running step only — null when nothing is in progress. */
export function latestRecoverySpinnerLabel(preview) {
  const items = preview?.items || [];
  if (!items.length) return null;
  const running = [...items].reverse().find((item) => item.status === RECOVERY_ACTIVITY_STATUS.RUNNING);
  return running?.label || null;
}

/** Hide bottom spinner + deactivate recovery loader after panel is ready. */
export function clearRecoveryLoaderUi(deps = {}) {
  const { setRecoveryActivity, setIsLoading, stopLoadingMessages } = deps;
  setRecoveryActivity?.((prev) => completeRecoveryActivity(prev));
  stopLoadingMessages?.();
  setIsLoading?.(false);
}
