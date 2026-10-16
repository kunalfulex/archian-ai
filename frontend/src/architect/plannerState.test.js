import {
  chatHasCompletedPipeline,
  dedupePlannerPlanMessages,
  getPlannerEntryRenderState,
  hasActivePlannerPlanPanel,
  inferPlannerPlanExecuted,
  isPlanExecutedProgressEntry,
  latestActivePlannerEntryId,
  markExecutedPlannerPlansResolved,
  preparePlannerChatEntries,
  resolvePlannerPendingFromLoadedMessages,
  resolveFreshSupersededPlannerEntries,
  resolveStalePlannerChatEntries,
  shouldHideChatEntry,
} from './plannerState';

describe('plannerState', () => {
  test('dedupePlannerPlanMessages keeps latest per plan_id', () => {
    const entries = [
      { id: 'a', metadata: { event_type: 'planner_plan_ready', plan_id: 'p1' } },
      { id: 'b', metadata: { event_type: 'planner_plan_ready', plan_id: 'p1' } },
    ];
    expect(dedupePlannerPlanMessages(entries).map((e) => e.id)).toEqual(['b']);
  });

  test('inferPlannerPlanExecuted when migration follows plan', () => {
    const plan = {
      id: 'plan',
      timestamp: 1,
      metadata: { event_type: 'planner_plan_ready' },
    };
    const entries = [
      plan,
      { id: 'm', timestamp: 2, metadata: { event_type: 'migration_completed' } },
    ];
    expect(inferPlannerPlanExecuted(entries, plan)).toBe(true);
  });

  test('chatHasCompletedPipeline after migration', () => {
    const history = [
      { metadata: { event_type: 'planner_plan_ready' }, plannerResolved: true },
      { metadata: { event_type: 'migration_completed' } },
    ];
    expect(chatHasCompletedPipeline(history)).toBe(true);
  });

  test('resolveFreshSupersededPlannerEntries marks all interactive planner cards resolved', () => {
    const entries = [
      {
        id: '1',
        metadata: { event_type: 'planner_clarification_required', clarifications: [{}], planner_pending: {} },
      },
      {
        id: '2',
        metadata: { event_type: 'planner_plan_ready', plan_steps_summary: [{}], planner_pending: {} },
      },
    ];
    const next = resolveFreshSupersededPlannerEntries(entries);
    expect(next.every((e) => e.plannerResolved)).toBe(true);
  });

  test('preparePlannerChatEntries dedupes and resolves stale', () => {
    const entries = preparePlannerChatEntries([
      { id: '1', metadata: { event_type: 'planner_plan_ready', plan_id: 'p1', planner_pending: {} } },
      { id: '2', metadata: { event_type: 'planner_plan_ready', plan_id: 'p1', planner_pending: {} } },
      { id: '3', metadata: { event_type: 'planner_clarification_required', clarifications: [{}], planner_pending: {} } },
    ]);
    expect(entries.filter((e) => e.metadata?.event_type === 'planner_plan_ready')).toHaveLength(1);
    const planEntry = entries.find((e) => e.metadata?.event_type === 'planner_plan_ready');
    expect(planEntry?.plannerResolved).toBe(true);
    expect(entries.find((e) => e.id === '3')?.plannerResolved).toBeFalsy();
  });

  test('resolvePlannerPendingFromLoadedMessages returns null when plan precedes migration', () => {
    const mapped = [
      {
        metadata: {
          event_type: 'planner_plan_ready',
          planner_pending: { classification: { plan_status: 'awaiting_approval' } },
          plan_status: 'awaiting_approval',
        },
      },
      { metadata: { event_type: 'migration_completed' } },
    ];
    expect(resolvePlannerPendingFromLoadedMessages(mapped)).toBeNull();
  });

  test('resolvePlannerPendingFromLoadedMessages returns pending for plan after migration', () => {
    const mapped = [
      {
        metadata: {
          event_type: 'planner_plan_ready',
          planner_pending: { classification: { plan_status: 'awaiting_approval' } },
          plan_status: 'awaiting_approval',
        },
        plannerResolved: true,
      },
      { metadata: { event_type: 'migration_completed' } },
      {
        metadata: {
          event_type: 'planner_plan_ready',
          planner_pending: { classification: { plan_status: 'awaiting_approval' } },
          plan_status: 'awaiting_approval',
        },
      },
    ];
    expect(resolvePlannerPendingFromLoadedMessages(mapped)).not.toBeNull();
  });

  test('latestActivePlannerEntryId finds plan after migration_completed', () => {
    const entries = preparePlannerChatEntries([
      {
        id: 'old-plan',
        metadata: {
          event_type: 'planner_plan_ready',
          plan_steps_summary: [{ label: 'Extract' }],
          planner_pending: {},
        },
      },
      { id: 'm', metadata: { event_type: 'migration_completed' } },
      {
        id: 'new-plan',
        metadata: {
          event_type: 'planner_plan_ready',
          plan_steps_summary: [{ label: 'Extract Contact' }],
          planner_pending: { classification: { plan_status: 'awaiting_approval' } },
        },
      },
    ]);
    expect(entries.find((e) => e.id === 'old-plan')?.plannerResolved).toBe(true);
    expect(latestActivePlannerEntryId(entries)).toBe('new-plan');
    const { showPlannerPlanPanel } = getPlannerEntryRenderState(
      entries.find((e) => e.id === 'new-plan'),
      'new-plan',
    );
    expect(showPlannerPlanPanel).toBe(true);
  });

  test('chat-only planner clarification shows markdown not hidden', () => {
    const entry = {
      id: 'chat-only',
      type: 'system',
      text: 'Which table should we extract from?',
      metadata: {
        event_type: 'planner_clarification_required',
        clarifications: [
          {
            id: 'c1',
            clarification_type: 'pick_source_entity',
            prompt: 'Which table should we extract from?',
            options: [],
            required: true,
          },
        ],
        planner_pending: { original_prompt: 'deploy user data' },
      },
    };
    const { showPlannerClarPanel, hidePlannerMarkdown } = getPlannerEntryRenderState(
      entry,
      'chat-only',
    );
    expect(showPlannerClarPanel).toBe(false);
    expect(hidePlannerMarkdown).toBe(false);
  });

  test('pick_create_headers with options shows clarification panel', () => {
    const entry = {
      id: 'headers-clar',
      type: 'system',
      text: 'What column headers would you like?',
      metadata: {
        event_type: 'planner_clarification_required',
        clarifications: [
          {
            id: 'c1',
            clarification_type: 'pick_create_headers',
            prompt: 'What column headers would you like?',
            options: [
              { id: 'text:First Name', label: 'First Name' },
              { id: 'text:Last Name', label: 'Last Name' },
            ],
            required: true,
          },
        ],
        planner_pending: { original_prompt: 'create google sheet' },
      },
    };
    const { showPlannerClarPanel, hidePlannerMarkdown } = getPlannerEntryRenderState(
      entry,
      'headers-clar',
    );
    expect(showPlannerClarPanel).toBe(true);
    expect(hidePlannerMarkdown).toBe(true);
  });

  test('create_workbook_name with options shows clarification panel', () => {
    const entry = {
      id: 'title-clar',
      type: 'system',
      text: 'What would you like to name your new Google Sheet?',
      metadata: {
        event_type: 'planner_clarification_required',
        clarifications: [
          {
            id: 'c1',
            clarification_type: 'create_workbook_name',
            prompt: 'What would you like to name your new Google Sheet?',
            options: [
              { id: 'text:Client List', label: 'Client List' },
              { id: 'text:Contact Information', label: 'Contact Information' },
            ],
            required: true,
          },
        ],
        planner_pending: { original_prompt: 'create google sheet' },
      },
    };
    const { showPlannerClarPanel, hidePlannerMarkdown } = getPlannerEntryRenderState(
      entry,
      'title-clar',
    );
    expect(showPlannerClarPanel).toBe(true);
    expect(hidePlannerMarkdown).toBe(true);
  });

  test('pick_source_workbook with options shows clarification panel', () => {
    const entry = {
      id: 'src-wb-clar',
      type: 'system',
      text: 'Which Google Sheet should we read from?',
      metadata: {
        event_type: 'planner_clarification_required',
        clarifications: [
          {
            id: 'c1',
            clarification_type: 'pick_source_workbook',
            prompt: 'Which Google Sheet should we read from?',
            options: [
              { id: 'gs_wb_sheet-emp-1', label: 'Employee list (Sheet1)' },
            ],
            required: true,
          },
        ],
        planner_pending: { original_prompt: 'extract from google sheet' },
      },
    };
    const { showPlannerClarPanel, hidePlannerMarkdown } = getPlannerEntryRenderState(
      entry,
      'src-wb-clar',
    );
    expect(showPlannerClarPanel).toBe(true);
    expect(hidePlannerMarkdown).toBe(true);
  });

  test('pick_create_headers without options stays chat-only', () => {
    const entry = {
      id: 'headers-chat',
      type: 'system',
      text: 'List the column headers in chat.',
      metadata: {
        event_type: 'planner_clarification_required',
        clarifications: [
          {
            id: 'c1',
            clarification_type: 'pick_create_headers',
            prompt: 'List the column headers in chat.',
            options: [],
            required: true,
          },
        ],
        planner_pending: {},
      },
    };
    const { showPlannerClarPanel, hidePlannerMarkdown } = getPlannerEntryRenderState(
      entry,
      'headers-chat',
    );
    expect(showPlannerClarPanel).toBe(false);
    expect(hidePlannerMarkdown).toBe(false);
  });

  test('hasActivePlannerPlanPanel respects plannerResolved', () => {
    expect(
      hasActivePlannerPlanPanel([
        { metadata: { event_type: 'planner_plan_ready' }, plannerResolved: true },
      ]),
    ).toBe(false);
  });

  test('preparePlannerChatEntries resolves clarifications after migration_completed', () => {
    const clar = {
      id: 'clar-1',
      metadata: {
        event_type: 'planner_clarification_required',
        clarifications: [{ options: [{ id: 'sql' }], required: true }],
        planner_pending: { classification: { plan_status: 'awaiting_clarification' } },
      },
    };
    const entries = preparePlannerChatEntries([
      clar,
      { id: 'm', metadata: { event_type: 'migration_completed' } },
    ]);
    expect(entries[0].plannerResolved).toBe(true);
    expect(latestActivePlannerEntryId(entries)).toBeNull();
    expect(getPlannerEntryRenderState(entries[0], null).showPlannerClarPanel).toBe(false);
  });

  test('preparePlannerChatEntries keeps new plan active after migration_completed', () => {
    const entries = preparePlannerChatEntries([
      {
        id: 'old-plan',
        metadata: {
          event_type: 'planner_plan_ready',
          plan_steps_summary: [{ label: 'Extract' }],
          planner_pending: {},
        },
      },
      { id: 'm', metadata: { event_type: 'migration_completed' } },
      {
        id: 'new-plan',
        metadata: {
          event_type: 'planner_plan_ready',
          plan_steps_summary: [{ label: 'Extract Contact' }],
          planner_pending: { classification: { plan_status: 'awaiting_approval' } },
        },
      },
    ]);
    expect(entries.find((e) => e.id === 'old-plan')?.plannerResolved).toBe(true);
    expect(entries.find((e) => e.id === 'new-plan')?.plannerResolved).toBeFalsy();
    expect(latestActivePlannerEntryId(entries)).toBe('new-plan');
  });

  test('preparePlannerChatEntries resolves early clar when plan exists later', () => {
    const entries = preparePlannerChatEntries([
      {
        id: 'clar-1',
        metadata: {
          event_type: 'planner_clarification_required',
          clarifications: [{ options: [{ id: 'sql' }], required: true }],
          planner_pending: {},
        },
      },
      {
        id: 'plan-1',
        metadata: {
          event_type: 'planner_plan_ready',
          plan_steps_summary: [{ label: 'Extract' }],
          planner_pending: { classification: { plan_status: 'awaiting_approval' } },
        },
      },
    ]);
    expect(entries[0].plannerResolved).toBe(true);
    expect(entries[1].plannerResolved).toBeFalsy();
    expect(latestActivePlannerEntryId(entries)).toBe('plan-1');
  });

  test('mapServerMessageToEntry restores planner_resolved from metadata', () => {
    const map = (message) => {
      const metadata = message?.metadata || null;
      return {
        id: message?.id,
        metadata,
        plannerResolved: Boolean(metadata?.planner_resolved || metadata?.plannerResolved),
      };
    };
    const entry = map({
      id: 'uuid-0000-4000-8000-000000000001',
      metadata: { event_type: 'planner_clarification_required', planner_resolved: true },
    });
    expect(entry.plannerResolved).toBe(true);
  });

  test('isPlanExecutedProgressEntry includes spreadsheet_creation_completed', () => {
    expect(
      isPlanExecutedProgressEntry({
        metadata: { event_type: 'spreadsheet_creation_completed' },
      }),
    ).toBe(true);
  });

  test('inferPlannerPlanExecuted when spreadsheet creation follows plan', () => {
    const plan = {
      id: 'plan',
      timestamp: 1,
      metadata: { event_type: 'planner_plan_ready' },
    };
    const entries = [
      plan,
      {
        id: 'create',
        timestamp: 2,
        metadata: { event_type: 'spreadsheet_creation_completed', spreadsheet_id: 'abc' },
      },
    ];
    expect(inferPlannerPlanExecuted(entries, plan)).toBe(true);
  });

  test('shouldHideChatEntry hides resolved planner_plan_ready', () => {
    expect(
      shouldHideChatEntry({
        metadata: { event_type: 'planner_plan_ready' },
        plannerResolved: true,
      }),
    ).toBe(true);
    expect(
      shouldHideChatEntry({
        metadata: { event_type: 'planner_plan_ready', planner_resolved: true },
      }),
    ).toBe(true);
    expect(
      shouldHideChatEntry({
        metadata: { event_type: 'planner_plan_ready' },
        plannerResolved: false,
      }),
    ).toBe(false);
  });

  test('preparePlannerChatEntries resolves plan when spreadsheet_creation_completed follows', () => {
    const entries = preparePlannerChatEntries([
      {
        id: 'plan-1',
        timestamp: 1000,
        metadata: {
          event_type: 'planner_plan_ready',
          plan_steps_summary: [{ label: 'Create spreadsheet Archian Data' }],
          planner_pending: { classification: { plan_status: 'awaiting_approval' } },
        },
      },
      {
        id: 'created-1',
        timestamp: 2000,
        metadata: {
          event_type: 'spreadsheet_creation_completed',
          spreadsheet_id: 'sheet-abc',
          spreadsheet_name: 'Archian Data',
        },
      },
    ]);
    expect(entries[0].plannerResolved).toBe(true);
    expect(latestActivePlannerEntryId(entries)).toBeNull();
    expect(getPlannerEntryRenderState(entries[0], null).showPlannerPlanPanel).toBeFalsy();
    expect(shouldHideChatEntry(entries[0])).toBe(true);
    expect(resolvePlannerPendingFromLoadedMessages(entries)).toBeNull();
  });

  test('preparePlannerChatEntries resolves plan when data_preview follows on reload', () => {
    const entries = preparePlannerChatEntries([
      {
        id: 'plan-1',
        timestamp: 1000,
        metadata: {
          event_type: 'planner_plan_ready',
          plan_steps_summary: [{ label: 'Extract from SF 1' }],
          planner_pending: { classification: { plan_status: 'awaiting_approval' } },
        },
      },
      {
        id: 'preview-1',
        type: 'data_preview',
        timestamp: 2000,
        jobId: 'job-abc',
        metadata: { event_type: 'extraction_completed', job_id: 'job-abc' },
      },
    ]);
    expect(entries[0].plannerResolved).toBe(true);
    expect(latestActivePlannerEntryId(entries)).toBeNull();
    expect(getPlannerEntryRenderState(entries[0], null).showPlannerPlanPanel).toBeFalsy();
    expect(resolvePlannerPendingFromLoadedMessages(entries)).toBeNull();
  });
});
