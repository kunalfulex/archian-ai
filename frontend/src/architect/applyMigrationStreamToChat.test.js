import {
  buildMigrationStartedMetadata,
  applyMigrationProgress,
  applyMigrationStarted,
  applyMigrationCompletedFromStream,
  applyMigrationFinished,
  mergeMigrationCompletedFromServer,
  mergeRecoveryFromServer,
  syncMigrationTerminalFromServer,
  resetMigrationSseTerminalMergeTracking,
} from './applyMigrationStreamToChat';

jest.mock('../api', () => ({ __esModule: true, default: { get: jest.fn() } }));
jest.mock('./plannerState', () => ({
  resolvePlannerEntriesAfterPipeline: (entries) => entries,
  schedulePersistPlannerResolved: jest.fn(),
}));

import api from '../api';

describe('buildMigrationStartedMetadata', () => {
  it('maps SSE migration_started fields', () => {
    const meta = buildMigrationStartedMetadata({
      job_id: 'm1',
      batch_id: 'b1',
      destination: 'salesforce',
      destination_name: 'SF 1',
      total_records: 24,
      source_job_id: 'ext-1',
    });
    expect(meta.event_type).toBe('migration_started');
    expect(meta.job_id).toBe('m1');
    expect(meta.batch_id).toBe('b1');
    expect(meta.total_records).toBe(24);
    expect(meta.source_job_id).toBe('ext-1');
  });
});

describe('applyMigrationCompletedFromStream', () => {
  beforeEach(() => {
    resetMigrationSseTerminalMergeTracking();
  });

  it('merges migration_completed from SSE without messages API', () => {
    const setChatHistory = jest.fn((fn) => fn([]));
    const setMigrationStatuses = jest.fn((fn) => fn({}));

    const meta = applyMigrationCompletedFromStream(
      {
        job_id: 'm-sse',
        batch_id: 'b-sse',
        message: {
          id: 'msg-sse-1',
          content: 'Done',
          created_at: '2026-06-01T00:00:00Z',
          metadata: {
            event_type: 'migration_completed',
            job_id: 'm-sse',
            batch_id: 'b-sse',
            success_count: 5,
            error_count: 0,
            status: 'success',
          },
        },
      },
      {
        conversationId: 'conv-1',
        setChatHistory,
        setMigrationStatuses,
        plannerPendingRef: { current: null },
      },
    );

    expect(meta?.event_type).toBe('migration_completed');
    expect(setChatHistory).toHaveBeenCalled();
    expect(api.get).not.toHaveBeenCalled();
  });
});

describe('applyMigrationFinished', () => {
  beforeEach(() => {
    resetMigrationSseTerminalMergeTracking();
    jest.clearAllMocks();
  });

  it('skips messages polling when SSE already merged terminal chat', async () => {
    applyMigrationCompletedFromStream(
      {
        job_id: 'm-skip',
        message: {
          id: 'msg-skip',
          content: 'Done',
          metadata: {
            event_type: 'migration_completed',
            job_id: 'm-skip',
            batch_id: 'b-skip',
            success_count: 1,
            error_count: 0,
          },
        },
      },
      {
        conversationId: 'conv-skip',
        setChatHistory: jest.fn((fn) => fn([])),
        setMigrationStatuses: jest.fn((fn) => fn({})),
        plannerPendingRef: { current: null },
      },
    );

    await applyMigrationFinished(
      {
        job_id: 'm-skip',
        job_status: {
          status: 'completed',
          batch_id: 'b-skip',
          result: { successful_records: 1, failed_records: 0 },
        },
      },
      {
        conversationId: 'conv-skip',
        setChatHistory: jest.fn(),
        setMigrationStatuses: jest.fn((fn) => fn({})),
        plannerPendingRef: { current: null },
      },
    );

    expect(api.get).not.toHaveBeenCalled();
  });
});

describe('applyMigrationStarted', () => {
  beforeEach(() => {
    resetMigrationSseTerminalMergeTracking();
  });

  it('does not reset migration status when job already completed', async () => {
    const setMigrationStatuses = jest.fn((fn) =>
      fn({
        m1: {
          jobId: 'm1',
          batchId: 'b1',
          status: 'completed',
          progress: 100,
          totalRecords: 10,
        },
      }),
    );

    await applyMigrationStarted(
      {
        job_id: 'm1',
        batch_id: 'b1',
        total_records: 10,
        destination: 'sql',
      },
      {
        setChatHistory: jest.fn((fn) => fn([])),
        setMigrationStatuses,
        activeJobIdsRef: { current: new Set() },
      },
    );

    const next = setMigrationStatuses.mock.calls[0][0]({
      m1: { jobId: 'm1', status: 'completed', progress: 100 },
    });
    expect(next.m1.status).toBe('completed');
    expect(next.m1.progress).toBe(100);
  });
});

describe('applyMigrationProgress', () => {
  it('updates migrationStatuses from SSE progress', () => {
    const setMigrationStatuses = jest.fn((fn) =>
      fn({ m1: { jobId: 'm1', status: 'processing', progress: 0 } }),
    );
    applyMigrationProgress(
      {
        job_id: 'm1',
        progress: 50,
        processed_records: 12,
        total_records: 24,
        message: 'Halfway',
      },
      { setMigrationStatuses, clearMigrationStatusPropsCache: jest.fn() },
    );
    expect(setMigrationStatuses).toHaveBeenCalled();
    const next = setMigrationStatuses.mock.calls[0][0]({
      m1: { jobId: 'm1', status: 'processing', progress: 0 },
    });
    expect(next.m1.progress).toBe(50);
    expect(next.m1.message).toBe('Halfway');
  });
});

describe('mergeMigrationCompletedFromServer', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('merges persisted migration_completed into chatHistory once', async () => {
    api.get.mockResolvedValue({
      data: {
        messages: [
          {
            id: 'msg-complete-1',
            content: '✅ **Migration Completed**',
            created_at: '2026-06-01T00:00:00Z',
            metadata: {
              event_type: 'migration_completed',
              job_id: 'm1',
              batch_id: 'b1',
              success_count: 10,
              error_count: 0,
              status: 'success',
            },
          },
        ],
      },
    });

    const setChatHistory = jest.fn((fn) => fn([]));
    const setMigrationStatuses = jest.fn((fn) => fn({}));

    const meta = await mergeMigrationCompletedFromServer({
      conversationId: 'conv-1',
      jobId: 'm1',
      batchId: 'b1',
      setChatHistory,
      setMigrationStatuses,
      plannerPendingRef: { current: { foo: 1 } },
    });

    expect(meta?.event_type).toBe('migration_completed');
    expect(setChatHistory).toHaveBeenCalled();
    const next = setChatHistory.mock.calls[0][0]([]);
    expect(next).toHaveLength(1);
    expect(next[0].id).toBe('msg-complete-1');
    expect(setMigrationStatuses).toHaveBeenCalled();
  });

  it('does not duplicate migration_completed when already in chatHistory', async () => {
    api.get.mockResolvedValue({
      data: {
        messages: [
          {
            id: 'msg-complete-1',
            metadata: { event_type: 'migration_completed', job_id: 'm1' },
          },
        ],
      },
    });

    const setChatHistory = jest.fn((fn) =>
      fn([
        {
          id: 'msg-complete-1',
          metadata: { event_type: 'migration_completed', job_id: 'm1' },
        },
      ]),
    );

    await mergeMigrationCompletedFromServer({
      conversationId: 'conv-1',
      jobId: 'm1',
      setChatHistory,
      setMigrationStatuses: jest.fn(),
    });

    const next = setChatHistory.mock.calls[0][0]([
      {
        id: 'msg-complete-1',
        metadata: { event_type: 'migration_completed', job_id: 'm1' },
      },
    ]);
    expect(next).toHaveLength(1);
  });
});

describe('mergeRecoveryFromServer', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('merges planner_recovery_required from messages and dedupes by batch id', async () => {
    api.get.mockResolvedValue({
      data: {
        messages: [
          {
            id: 'recovery-msg-1',
            content: 'Fix migration errors',
            created_at: '2026-06-01T00:00:00Z',
            metadata: {
              event_type: 'planner_recovery_required',
              recovery_batch_id: 'batch-9',
              recovery_proposal: { requires_write: true, steps: [] },
              planner_pending: { recovery_batch_id: 'batch-9' },
            },
          },
        ],
      },
    });

    const plannerPendingRef = { current: null };
    const setChatHistory = jest.fn((fn) => fn([]));

    const attached = await mergeRecoveryFromServer({
      conversationId: 'conv-1',
      batchId: 'batch-9',
      jobId: 'm1',
      setChatHistory,
      plannerPendingRef,
    });

    expect(attached).toBe(true);
    expect(plannerPendingRef.current?.recovery_batch_id).toBe('batch-9');
    const next = setChatHistory.mock.calls[0][0]([]);
    expect(next).toHaveLength(1);
    expect(next[0].metadata.event_type).toBe('planner_recovery_required');
  });

  it('skips duplicate recovery panel for the same batch id', async () => {
    api.get.mockResolvedValue({
      data: {
        messages: [
          {
            id: 'recovery-msg-1',
            metadata: {
              event_type: 'planner_recovery_required',
              recovery_proposal: { requires_write: true },
              planner_pending: { recovery_batch_id: 'batch-9' },
            },
          },
        ],
      },
    });

    const setChatHistory = jest.fn((fn) =>
      fn([
        {
          id: 'existing-recovery',
          metadata: {
            event_type: 'planner_recovery_required',
            planner_pending: { recovery_batch_id: 'batch-9' },
          },
        },
      ]),
    );

    const attached = await mergeRecoveryFromServer({
      conversationId: 'conv-1',
      batchId: 'batch-9',
      setChatHistory,
      plannerPendingRef: { current: null },
    });

    expect(attached).toBe(true);
    const next = setChatHistory.mock.calls[0][0]([
      {
        id: 'existing-recovery',
        metadata: {
          event_type: 'planner_recovery_required',
          planner_pending: { recovery_batch_id: 'batch-9' },
        },
      },
    ]);
    expect(next).toHaveLength(1);
  });
});

describe('syncMigrationTerminalFromServer', () => {
  beforeEach(() => {
    resetMigrationSseTerminalMergeTracking();
    jest.clearAllMocks();
  });

  it('merges migration_completed and recovery when errors exist', async () => {
    api.get.mockImplementation((url) => {
      if (url.includes('/messages')) {
        return Promise.resolve({
          data: {
            messages: [
              {
                id: 'msg-complete-1',
                content: 'Done with errors',
                metadata: {
                  event_type: 'migration_completed',
                  job_id: 'm1',
                  batch_id: 'b1',
                  success_count: 8,
                  error_count: 2,
                  status: 'success',
                },
              },
              {
                id: 'recovery-msg-1',
                content: 'Review errors',
                metadata: {
                  event_type: 'planner_recovery_required',
                  recovery_proposal: { requires_write: true },
                  planner_pending: { recovery_batch_id: 'b1' },
                },
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const setChatHistory = jest.fn((fn) => fn([]));
    const setMigrationStatuses = jest.fn((fn) => fn({}));
    const clearMigrationStatusPropsCache = jest.fn();

    const ok = await syncMigrationTerminalFromServer({
      conversationId: 'conv-1',
      jobId: 'm1',
      batchId: 'b1',
      setChatHistory,
      setMigrationStatuses,
      plannerPendingRef: { current: null },
      clearMigrationStatusPropsCache,
      maxAttempts: 1,
    });

    expect(ok).toBe(true);
    expect(clearMigrationStatusPropsCache).toHaveBeenCalled();
    expect(api.get).toHaveBeenCalled();
  });

  it('dedupes concurrent terminal merge for the same job', async () => {
    api.get.mockResolvedValue({
      data: {
        messages: [
          {
            id: 'msg-complete-1',
            metadata: {
              event_type: 'migration_completed',
              job_id: 'm-dedup',
              batch_id: 'b-dedup',
              error_count: 0,
              status: 'success',
            },
          },
        ],
      },
    });

    const setChatHistory = jest.fn((fn) => fn([]));
    const setMigrationStatuses = jest.fn((fn) => fn({}));

    const opts = {
      conversationId: 'conv-dedup',
      jobId: 'm-dedup',
      batchId: 'b-dedup',
      setChatHistory,
      setMigrationStatuses,
      plannerPendingRef: { current: null },
      maxAttempts: 1,
    };

    const [a, b] = await Promise.all([
      syncMigrationTerminalFromServer(opts),
      syncMigrationTerminalFromServer(opts),
    ]);

    expect(a).toBe(true);
    expect(b).toBe(true);
    expect(api.get).toHaveBeenCalledTimes(1);
  });
});
