import {
  filterInternalArchitectPipelineSteps,
  normalizePipelineStepsForPersist,
  pipelineStepsForPreviewDisplay,
} from './dataOperationsDisplay';

describe('pipelineStepsForPreviewDisplay', () => {
  it('drops GSheets execute steps so extract previews do not show stuck loaders', () => {
    const steps = [
      { tool_id: 'gsheets_execute_operation', label: 'Run Sheets operation', status: 'running' },
    ];
    expect(pipelineStepsForPreviewDisplay(steps, null)).toBeNull();
  });

  it('keeps transform/filter steps after a data-ops run', () => {
    const steps = [{ tool_id: 'filter', label: 'Filter rows', status: 'running' }];
    const out = pipelineStepsForPreviewDisplay(steps, ['filter']);
    expect(out).toEqual([
      { tool_id: 'filter', label: 'Filter rows', detail: null, status: 'done' },
    ]);
  });

  it('normalizes running architect steps to done before filtering', () => {
    const steps = [
      { tool_id: 'gsheets_execute_operation', label: 'Run Sheets operation', status: 'running' },
    ];
    expect(normalizePipelineStepsForPersist(steps)[0].status).toBe('done');
    expect(filterInternalArchitectPipelineSteps(steps)).toBeNull();
  });

  it('safe for metadata when all steps are internal (null, not .length throw)', () => {
    const steps = [
      { tool_id: 'gsheets_execute_operation', label: 'Run', status: 'running' },
    ];
    const persisted = pipelineStepsForPreviewDisplay(steps, null);
    expect(persisted).toBeNull();
    expect(Boolean(persisted?.length)).toBe(false);
  });
});
