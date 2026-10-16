import {
  RECOVERY_ACTIVITY_STATUS,
  createEmptyRecoveryActivity,
  enforceSingleRunningSpinner,
  reduceRecoveryActivity,
  completeRecoveryActivity,
  latestRecoverySpinnerLabel,
} from './reduceRecoveryActivity';

describe('reduceRecoveryActivity', () => {
  test('recovery_analysis_started activates preview', () => {
    const next = reduceRecoveryActivity(createEmptyRecoveryActivity(), {
      event_type: 'recovery_analysis_started',
      job_id: 'job-1',
      batch_id: 'batch-1',
    });
    expect(next.active).toBe(true);
    expect(next.jobId).toBe('job-1');
    expect(next.batchId).toBe('batch-1');
  });

  test('append-only recovery milestones upsert by activity_id', () => {
    let state = createEmptyRecoveryActivity({ active: true });
    state = reduceRecoveryActivity(state, {
      event_type: 'recovery_activity',
      activity_id: 'review_results',
      label: 'Reviewing migration results',
      status: RECOVERY_ACTIVITY_STATUS.RUNNING,
    });
    state = reduceRecoveryActivity(state, {
      event_type: 'recovery_activity',
      activity_id: 'review_results',
      label: 'Reviewing migration results',
      status: RECOVERY_ACTIVITY_STATUS.DONE,
      detail: '2 failed records',
    });
    expect(state.items).toHaveLength(1);
    expect(state.items[0].status).toBe(RECOVERY_ACTIVITY_STATUS.DONE);
    expect(state.items[0].detail).toBe('2 failed records');
  });

  test('enforceSingleRunningSpinner keeps one running spinner', () => {
    const items = enforceSingleRunningSpinner([
      { activityId: 'a', label: 'A', status: RECOVERY_ACTIVITY_STATUS.RUNNING },
      { activityId: 'b', label: 'B', status: RECOVERY_ACTIVITY_STATUS.RUNNING },
    ]);
    expect(items[0].status).toBe(RECOVERY_ACTIVITY_STATUS.DONE);
    expect(items[1].status).toBe(RECOVERY_ACTIVITY_STATUS.RUNNING);
  });

  test('latestRecoverySpinnerLabel returns null when all steps are done', () => {
    const preview = {
      items: [
        { activityId: 'a', label: 'Reviewing migration results', status: RECOVERY_ACTIVITY_STATUS.DONE },
        { activityId: 'b', label: 'Recovery plan ready', status: RECOVERY_ACTIVITY_STATUS.DONE },
      ],
    };
    expect(latestRecoverySpinnerLabel(preview)).toBeNull();
  });

  test('latestRecoverySpinnerLabel returns running step label only', () => {
    const preview = {
      items: [
        { activityId: 'a', label: 'Reviewing migration results', status: RECOVERY_ACTIVITY_STATUS.DONE },
        { activityId: 'b', label: 'Building recovery plan', status: RECOVERY_ACTIVITY_STATUS.RUNNING },
      ],
    };
    expect(latestRecoverySpinnerLabel(preview)).toBe('Building recovery plan');
  });

  test('completeRecoveryActivity marks running steps done and deactivates', () => {
    const prev = {
      active: true,
      items: [
        { activityId: 'a', label: 'A', status: RECOVERY_ACTIVITY_STATUS.DONE },
        { activityId: 'b', label: 'B', status: RECOVERY_ACTIVITY_STATUS.RUNNING },
      ],
    };
    const next = completeRecoveryActivity(prev);
    expect(next.active).toBe(false);
    expect(next.items.every((item) => item.status === RECOVERY_ACTIVITY_STATUS.DONE)).toBe(true);
  });
});
