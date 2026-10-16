/**
 * Reducer for append-only planning activity stream (plan scope / plan build).
 */

export const PLANNING_ACTIVITY_STATUS = {
  RUNNING: 'running',
  DONE: 'done',
  ERROR: 'error',
  FAILED: 'failed',
};

export function createEmptyPlanningActivity(overrides = {}) {
  return {
    active: false,
    items: [],
    ...overrides,
  };
}

function upsertPlanningItem(items, event) {
  const activityId = (event.activity_id || '').trim();
  if (!activityId) return items;

  const nextItem = {
    activityId,
    label: (event.label || '').trim() || 'Working',
    status: (event.status || PLANNING_ACTIVITY_STATUS.RUNNING).toLowerCase(),
    detail: event.detail ? String(event.detail).trim() : null,
    segment: event.segment ? String(event.segment).trim() : null,
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
    if (items[i].status === PLANNING_ACTIVITY_STATUS.RUNNING) {
      lastRunningIdx = i;
      break;
    }
  }
  if (lastRunningIdx < 0) return items;
  return items.map((item, i) => {
    if (item.status === PLANNING_ACTIVITY_STATUS.RUNNING && i !== lastRunningIdx) {
      return { ...item, status: PLANNING_ACTIVITY_STATUS.DONE };
    }
    return item;
  });
}

export function displayStatusForPlanningItem(items, item) {
  const list = enforceSingleRunningSpinner(items || []);
  const match = list.find((row) => row.activityId === item.activityId);
  return match?.status || item.status;
}

export function reducePlanningActivity(prev, event) {
  if (!event || event.event_type !== 'planning_activity') {
    return prev || createEmptyPlanningActivity();
  }

  const base = prev || createEmptyPlanningActivity();
  return {
    ...base,
    active: true,
    items: upsertPlanningItem(base.items || [], event),
  };
}

export function activatePlanningActivity(prev) {
  return {
    ...(prev || createEmptyPlanningActivity()),
    active: true,
  };
}

export function completePlanningActivity(prev) {
  const base = prev || createEmptyPlanningActivity();
  const items = (base.items || []).map((item) =>
    item.status === PLANNING_ACTIVITY_STATUS.RUNNING
      ? { ...item, status: PLANNING_ACTIVITY_STATUS.DONE }
      : item,
  );
  return {
    ...base,
    active: false,
    items,
  };
}

export function latestPlanningSpinnerLabel(preview) {
  const items = preview?.items || [];
  if (!items.length) return null;
  const running = [...items].reverse().find((item) => item.status === PLANNING_ACTIVITY_STATUS.RUNNING);
  if (running?.label) return running.label;
  return items[items.length - 1]?.label || null;
}
