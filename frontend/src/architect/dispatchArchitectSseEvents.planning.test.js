import { dispatchArchitectSseEvents } from './dispatchArchitectSseEvents';
import {
  createEmptyPlanningActivity,
  PLANNING_ACTIVITY_STATUS,
} from './reducePlanningActivity';

function runDispatch(events, initialPlanning, streamStateOverrides = {}) {
  let planning = initialPlanning;
  const handlers = {
    setPlanningActivity: (updater) => {
      planning = typeof updater === 'function' ? updater(planning) : updater;
    },
    setOperationPreview: jest.fn(),
    setLoadingMessage: jest.fn(),
    setIsLoading: jest.fn(),
  };
  const streamState = {
    pendingHeldExtraction: null,
    extractionCompletedViaHeldStream: false,
    sawExtractionJobFinished: false,
    executionHandoff: false,
    ...streamStateOverrides,
  };
  dispatchArchitectSseEvents(events, { streamState, handlers });
  return { planning, streamState };
}

describe('dispatchArchitectSseEvents planning loader', () => {
  test('planning stays active through resume planning_activity milestones', () => {
    const activePlanning = createEmptyPlanningActivity({
      active: true,
      items: [
        {
          activityId: 'apply_selection',
          label: 'Applying your selection',
          status: PLANNING_ACTIVITY_STATUS.RUNNING,
        },
      ],
    });
    const { planning: after } = runDispatch(
      [
        {
          event_type: 'planning_activity',
          activity_id: 'merge_clarifications',
          label: 'Merging your answers',
          status: 'done',
          segment: 'scope',
        },
      ],
      activePlanning,
    );
    expect(after.active).toBe(true);
    expect(after.items.some((i) => i.activityId === 'merge_clarifications')).toBe(true);
  });

  test('operation_phase understand_request does not deactivate planning', () => {
    const activePlanning = createEmptyPlanningActivity({
      active: true,
      items: [
        {
          activityId: 'apply_selection',
          label: 'Applying your selection',
          status: PLANNING_ACTIVITY_STATUS.RUNNING,
        },
      ],
    });
    const { planning: after } = runDispatch(
      [
        {
          event_type: 'operation_phase',
          phase: 'understand_request',
          status: 'done',
          label: 'Applying your selection',
        },
      ],
      activePlanning,
    );
    expect(after.active).toBe(true);
  });

  test('operation_phase connect_source deactivates planning', () => {
    const activePlanning = createEmptyPlanningActivity({
      active: true,
      items: [
        {
          activityId: 'apply_selection',
          label: 'Applying your selection',
          status: PLANNING_ACTIVITY_STATUS.RUNNING,
        },
      ],
    });
    const { planning: after } = runDispatch(
      [
        {
          event_type: 'operation_phase',
          phase: 'connect_source',
          status: 'running',
          label: 'Connecting source',
        },
      ],
      activePlanning,
    );
    expect(after.active).toBe(false);
  });

  test('planner_clarification_required deactivates planning', () => {
    const activePlanning = createEmptyPlanningActivity({
      active: true,
      items: [
        {
          activityId: 'clarification_needed',
          label: 'Need your input to continue',
          status: PLANNING_ACTIVITY_STATUS.DONE,
        },
      ],
    });
    const { planning: after } = runDispatch(
      [{ event_type: 'planner_clarification_required', clarifications: [] }],
      activePlanning,
    );
    expect(after.active).toBe(false);
  });

  test('classification_completed approved clears planning and blocks later planning_activity', () => {
    const activePlanning = createEmptyPlanningActivity({
      active: true,
      items: [
        {
          activityId: 'apply_selection',
          label: 'Starting approved plan',
          status: PLANNING_ACTIVITY_STATUS.RUNNING,
        },
      ],
    });
    const { planning: after, streamState } = runDispatch(
      [
        { event_type: 'classification_completed', plan_status: 'approved' },
        {
          event_type: 'planning_activity',
          activity_id: 'apply_selection',
          label: 'Starting approved plan',
          status: 'done',
          segment: 'scope',
        },
      ],
      activePlanning,
    );
    expect(after.active).toBe(false);
    expect(streamState.executionHandoff).toBe(true);
  });

  test('planner_plan_ready approved clears planning', () => {
    const activePlanning = createEmptyPlanningActivity({
      active: true,
      items: [
        {
          activityId: 'plan_ready',
          label: 'Plan ready (2 steps)',
          status: PLANNING_ACTIVITY_STATUS.DONE,
        },
      ],
    });
    const { planning: after } = runDispatch(
      [{ event_type: 'planner_plan_ready', plan_status: 'approved', steps: [{ label: 'Map' }] }],
      activePlanning,
    );
    expect(after.active).toBe(false);
  });

  test('agent_plan_ready clears planning and sets execution handoff', () => {
    const activePlanning = createEmptyPlanningActivity({
      active: true,
      items: [
        {
          activityId: 'apply_selection',
          label: 'Starting approved plan',
          status: PLANNING_ACTIVITY_STATUS.RUNNING,
        },
      ],
    });
    const { planning: after, streamState } = runDispatch(
      [
        {
          event_type: 'agent_plan_ready',
          pipeline: [{ id: 'map_fields', label: 'Map fields' }],
        },
        {
          event_type: 'planning_activity',
          activity_id: 'assemble_plan',
          label: 'Assembling execution plan',
          status: 'running',
          segment: 'plan_build',
        },
      ],
      activePlanning,
    );
    expect(after.active).toBe(false);
    expect(streamState.executionHandoff).toBe(true);
  });

  test('operation_phase identify_route clears planning after execution handoff', () => {
    const activePlanning = createEmptyPlanningActivity({
      active: true,
      items: [
        {
          activityId: 'apply_selection',
          label: 'Starting approved plan',
          status: PLANNING_ACTIVITY_STATUS.RUNNING,
        },
      ],
    });
    const { planning: after } = runDispatch(
      [
        {
          event_type: 'operation_phase',
          phase: 'identify_route',
          status: 'done',
          label: 'Identifying source and destination',
        },
      ],
      activePlanning,
      { executionHandoff: true },
    );
    expect(after.active).toBe(false);
  });
});
