import {
  createEmptyPlanningActivity,
  reducePlanningActivity,
  completePlanningActivity,
  latestPlanningSpinnerLabel,
  enforceSingleRunningSpinner,
  PLANNING_ACTIVITY_STATUS,
} from './reducePlanningActivity';

describe('reducePlanningActivity', () => {
  test('appends new planning activities', () => {
    const state = reducePlanningActivity(createEmptyPlanningActivity(), {
      event_type: 'planning_activity',
      activity_id: 'parse_prompt',
      label: 'Understanding your request',
      status: 'running',
      segment: 'scope',
    });
    expect(state.active).toBe(true);
    expect(state.items).toHaveLength(1);
    expect(state.items[0].label).toBe('Understanding your request');
  });

  test('updates existing activity by id', () => {
    let state = reducePlanningActivity(createEmptyPlanningActivity(), {
      event_type: 'planning_activity',
      activity_id: 'parse_prompt',
      label: 'Understanding your request',
      status: 'running',
    });
    state = reducePlanningActivity(state, {
      event_type: 'planning_activity',
      activity_id: 'parse_prompt',
      label: 'Understanding your request',
      status: 'done',
    });
    expect(state.items).toHaveLength(1);
    expect(state.items[0].status).toBe(PLANNING_ACTIVITY_STATUS.DONE);
  });

  test('completePlanningActivity marks running items done and deactivates', () => {
    const state = completePlanningActivity({
      active: true,
      items: [
        { activityId: 'a', label: 'Step A', status: PLANNING_ACTIVITY_STATUS.RUNNING },
        { activityId: 'b', label: 'Step B', status: PLANNING_ACTIVITY_STATUS.DONE },
      ],
    });
    expect(state.active).toBe(false);
    expect(state.items[0].status).toBe(PLANNING_ACTIVITY_STATUS.DONE);
    expect(state.items[1].status).toBe(PLANNING_ACTIVITY_STATUS.DONE);
  });

  test('latestPlanningSpinnerLabel prefers running item', () => {
    const label = latestPlanningSpinnerLabel({
      items: [
        { activityId: 'a', label: 'Done step', status: PLANNING_ACTIVITY_STATUS.DONE },
        { activityId: 'b', label: 'Running step', status: PLANNING_ACTIVITY_STATUS.RUNNING },
      ],
    });
    expect(label).toBe('Running step');
  });

  test('enforceSingleRunningSpinner keeps only the latest running step active', () => {
    const normalized = enforceSingleRunningSpinner([
      { activityId: 'a', label: 'Understanding your request', status: PLANNING_ACTIVITY_STATUS.RUNNING },
      { activityId: 'b', label: 'Classifying intent', status: PLANNING_ACTIVITY_STATUS.RUNNING },
      { activityId: 'c', label: 'Identified source', status: PLANNING_ACTIVITY_STATUS.DONE },
      { activityId: 'd', label: 'Assembling execution plan', status: PLANNING_ACTIVITY_STATUS.RUNNING },
    ]);
    expect(normalized[0].status).toBe(PLANNING_ACTIVITY_STATUS.DONE);
    expect(normalized[1].status).toBe(PLANNING_ACTIVITY_STATUS.DONE);
    expect(normalized[2].status).toBe(PLANNING_ACTIVITY_STATUS.DONE);
    expect(normalized[3].status).toBe(PLANNING_ACTIVITY_STATUS.RUNNING);
  });

  test('new activity auto-completes prior running steps', () => {
    let state = reducePlanningActivity(createEmptyPlanningActivity(), {
      event_type: 'planning_activity',
      activity_id: 'parse_prompt',
      label: 'Understanding your request',
      status: 'running',
    });
    state = reducePlanningActivity(state, {
      event_type: 'planning_activity',
      activity_id: 'classify_intent',
      label: 'Classifying intent',
      status: 'running',
    });
    expect(state.items[0].status).toBe(PLANNING_ACTIVITY_STATUS.DONE);
    expect(state.items[1].status).toBe(PLANNING_ACTIVITY_STATUS.RUNNING);
  });
});
