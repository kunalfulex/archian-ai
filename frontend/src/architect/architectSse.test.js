import {
  isAgentUiOnlyPayload,
  isFatalStreamError,
  isHiddenArchitectDataPayload,
  isSseJsonOnlyTranscript,
  filterSseChunkForDisplay,
  shouldAbortArchitectStreamOnHandlerResult,
} from './architectSse';

describe('architectSse agent map/migrate payloads', () => {
  test('isAgentUiOnlyPayload detects map_fields_ready', () => {
    expect(
      isAgentUiOnlyPayload({
        status: 'success',
        job_id: 'j1',
        result: { action: 'map_fields_ready', mapping_config: { Name: {} } },
      }),
    ).toBe(true);
  });

  test('isHiddenArchitectDataPayload hides terminal map envelope', () => {
    const d = {
      status: 'success',
      job_id: 'j1',
      action: 'map_fields_ready',
    };
    expect(isHiddenArchitectDataPayload(d)).toBe(true);
  });

  test('filterSseChunkForDisplay strips map terminal line', () => {
    const chunk =
      'data: {"status":"success","job_id":"j1","action":"map_fields_ready"}\n\n';
    expect(filterSseChunkForDisplay(chunk).trim()).toBe('');
  });

  test('isSseJsonOnlyTranscript', () => {
    expect(isSseJsonOnlyTranscript('data: {"status":"success"}\n\n')).toBe(true);
    expect(isSseJsonOnlyTranscript('Here is your mapping.\n')).toBe(false);
  });

  test('filterSseChunkForDisplay strips migration_progress', () => {
    const chunk =
      'data: {"event_type":"migration_progress","job_id":"j1","progress":42}\n\n';
    expect(filterSseChunkForDisplay(chunk).trim()).toBe('');
  });

  test('filterSseChunkForDisplay strips operation_phase', () => {
    const chunk =
      'data: {"event_type":"operation_phase","phase":"connect_source","status":"running"}\n\n';
    expect(filterSseChunkForDisplay(chunk).trim()).toBe('');
  });

  test('filterSseChunkForDisplay strips spreadsheet_created and plan_execution_started', () => {
    const chunk =
      'data: {"status":"success","event_type":"spreadsheet_created","spreadsheet_id":"abc"}\n\n' +
      'data: {"event_type":"plan_execution_started","plan_id":"p1"}\n\n';
    expect(filterSseChunkForDisplay(chunk).trim()).toBe('');
  });

  test('filterSseChunkForDisplay strips legacy terminal result envelope', () => {
    const chunk =
      'data: {"status":"success","job_id":"ba749d24","result":{"event_type":"extraction_job_finished","job_id":"ba749d24"}}\n\n';
    expect(filterSseChunkForDisplay(chunk).trim()).toBe('');
  });

  test('isHiddenArchitectDataPayload hides nested extraction_job_finished result', () => {
    expect(
      isHiddenArchitectDataPayload({
        status: 'success',
        job_id: 'job-1',
        result: { event_type: 'extraction_job_finished', job_id: 'job-1' },
      }),
    ).toBe(true);
  });

  test('isFatalStreamError ignores planning_activity step status', () => {
    expect(
      isFatalStreamError({
        event_type: 'planning_activity',
        activity_id: 'plan_failed',
        status: 'error',
      }),
    ).toBe(false);
    expect(
      isFatalStreamError({
        event_type: 'operation_phase',
        phase: 'extract_records',
        status: 'error',
      }),
    ).toBe(false);
    expect(isFatalStreamError({ event_type: 'planner_error', status: 'error' })).toBe(true);
    expect(isFatalStreamError({ status: 'error', message: 'boom' })).toBe(true);
  });
});

describe('shouldAbortArchitectStreamOnHandlerResult', () => {
  test('empty string continues stream so follow-up planner_pending is not dropped', () => {
    expect(shouldAbortArchitectStreamOnHandlerResult(null)).toBe(false);
    expect(shouldAbortArchitectStreamOnHandlerResult(undefined)).toBe(false);
    expect(shouldAbortArchitectStreamOnHandlerResult('')).toBe(false);
  });

  test('non-empty string aborts with assistant text', () => {
    expect(shouldAbortArchitectStreamOnHandlerResult('Done.')).toBe(true);
  });
});
