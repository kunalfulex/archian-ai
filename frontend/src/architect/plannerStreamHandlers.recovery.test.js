import { createPlannerStreamHandlers } from './plannerStreamHandlers';

describe('createPlannerStreamHandlers onPlannerRecoveryRequired', () => {
  it('appends recovery panel once per batch id', async () => {
    const plannerPendingRef = { current: null };
    let history = [];
    const setChatHistory = jest.fn((fn) => {
      history = fn(history);
      return history;
    });

    const handlers = createPlannerStreamHandlers({
      plannerPendingRef,
      setChatHistory,
      setIsLoading: jest.fn(),
      stopLoadingMessages: jest.fn(),
      setAgentTurnSteps: jest.fn(),
      setOperationPreview: jest.fn(),
      persistMessage: jest.fn(),
      updateMessageMetadata: jest.fn(),
    });

    const payload = {
      message: 'Review migration errors',
      recovery_proposal: { requires_write: true, steps: [{ id: 'fix-1' }] },
      planner_pending: { recovery_batch_id: 'batch-42' },
      show_recovery_approval: true,
    };

    await handlers.onPlannerRecoveryRequired(payload, 'conv-1');
    await handlers.onPlannerRecoveryRequired(payload, 'conv-1');

    expect(history).toHaveLength(1);
    expect(history[0].metadata.event_type).toBe('planner_recovery_required');
    expect(history[0].metadata.recovery_proposal.steps).toHaveLength(1);
    expect(plannerPendingRef.current?.recovery_batch_id).toBe('batch-42');
  });

  it('does not call persistMessage for recovery (backend owns persistence)', async () => {
    const persistMessage = jest.fn();
    const setChatHistory = jest.fn((fn) => fn([]));

    const handlers = createPlannerStreamHandlers({
      plannerPendingRef: { current: null },
      setChatHistory,
      setIsLoading: jest.fn(),
      stopLoadingMessages: jest.fn(),
      setAgentTurnSteps: jest.fn(),
      setOperationPreview: jest.fn(),
      persistMessage,
      updateMessageMetadata: jest.fn(),
    });

    await handlers.onPlannerRecoveryRequired(
      {
        recovery_proposal: { requires_write: false },
        planner_pending: { recovery_batch_id: 'b1' },
      },
      'conv-1',
    );

    expect(persistMessage).not.toHaveBeenCalled();
  });
});
