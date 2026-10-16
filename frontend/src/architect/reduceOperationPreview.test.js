import {
  createEmptyOperationPreview,
  reduceOperationPreview,
  planReadyUserMessage,
  spinnerMessageFromOperationPreview,
  primaryPipelineSpinnerLabel,
} from './reduceOperationPreview';
import { PHASE_STATUS } from './operationPhaseRegistry';

describe('reduceOperationPreview', () => {
  test('classification_started does not seed execution pipeline during planning', () => {
    const prev = createEmptyOperationPreview();
    const next = reduceOperationPreview(prev, {
      event_type: 'classification_started',
    });
    expect(next.phases).toEqual([]);
  });

  test('classification_completed stores context without seeding execution pipeline', () => {
    const state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'classification_completed',
      intent: 'map_data',
      mode: 'map',
      source: 'salesforce',
      destination: 'salesforce',
      source_name: 'Production 1',
    });
    expect(state.phases).toEqual([]);
    expect(state.context?.source_name).toBe('Production 1');
  });

  test('classification_completed map intent execution pipeline comes from agent_plan_ready', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'classification_completed',
      intent: 'map_data',
      mode: 'map',
      source: 'salesforce',
      destination: 'salesforce',
      source_name: 'Production 1',
    });
    state = reduceOperationPreview(state, {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'map_fields', label: 'Mapping fields' },
      ],
    });
    expect(state.phases.map((p) => p.id)).toEqual(['identify_route', 'map_fields']);
    expect(state.phases.find((p) => p.id === 'connect_source')).toBeUndefined();
    expect(state.phases.find((p) => p.id === 'map_fields')?.status).toBe(PHASE_STATUS.PENDING);
    expect(state.phases.find((p) => p.id === 'identify_route')?.status).toBe(PHASE_STATUS.DONE);
  });

  test('agent_plan_ready replaces pipeline from backend', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'classification_completed',
      intent: 'extract',
      source: 'salesforce',
    });
    state = reduceOperationPreview(state, {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'map_fields', label: 'Field mapping' },
      ],
    });
    expect(state.phases.map((p) => p.id)).toEqual(['identify_route', 'map_fields']);
  });

  test('agent_step_started map_fields marks prior steps done', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'map_fields', label: 'Field mapping' },
      ],
    });
    state = reduceOperationPreview(state, {
      event_type: 'agent_step_started',
      tool_id: 'map_fields',
      label: 'Field mapping',
    });
    expect(state.phases.find((p) => p.id === 'identify_route')?.status).toBe(PHASE_STATUS.DONE);
    expect(state.phases.find((p) => p.id === 'map_fields')?.status).toBe(PHASE_STATUS.RUNNING);
    expect(state.phases.find((p) => p.id === 'connect_source')).toBeUndefined();
  });

  test('agent_plan_ready multi-source uses one phase per extract tool', () => {
    const state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'extract_salesforce', label: 'Extract from SF 1' },
        { id: 'extract_google_sheets', label: 'Extract data list workbook' },
        { id: 'gsheets_execute_operation', label: 'Combine sources' },
        { id: 'map_fields', label: 'Map fields' },
        { id: 'start_migration', label: 'Export to contact list' },
      ],
    });
    expect(state.phases.map((p) => p.id)).toEqual([
      'identify_route',
      'extract_salesforce',
      'extract_google_sheets',
      'gsheets_execute_operation',
      'map_fields',
      'start_migration',
    ]);
    expect(state.phases.find((p) => p.id === 'connect_source')).toBeUndefined();
  });

  test('extraction_progress updates active extract tool in multi-source pipeline', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'extract_salesforce', label: 'Extract from SF 1' },
        { id: 'extract_google_sheets', label: 'Extract workbook' },
      ],
    });
    state = reduceOperationPreview(state, {
      event_type: 'agent_step_started',
      tool_id: 'extract_salesforce',
      label: 'Extract from SF 1',
    });
    state = reduceOperationPreview(state, {
      event_type: 'extraction_progress',
      phase: 'resolve_org',
      message: 'Finding Salesforce org',
    });
    const sf = state.phases.find((p) => p.id === 'extract_salesforce');
    expect(sf?.status).toBe(PHASE_STATUS.RUNNING);
    expect(sf?.detail).toContain('Salesforce');
  });

  test('extraction_progress maps setup phase to connect_source', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'connect_source', label: 'Connecting to Salesforce' },
        { id: 'inspect_catalog', label: 'Reviewing fields and structure' },
        { id: 'extract_records', label: 'Extracting records' },
      ],
    });
    state = reduceOperationPreview(state, {
      event_type: 'extraction_progress',
      phase: 'resolve_org',
      message: 'Connecting to Salesforce org',
      progress: 10,
    });
    const connect = state.phases.find((p) => p.id === 'connect_source');
    expect(connect?.status).toBe(PHASE_STATUS.RUNNING);
    expect(connect?.detail).toContain('Salesforce');
  });

  test('advancing setup phase marks prior running step done', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'connect_source', label: 'Connecting to Salesforce' },
        { id: 'inspect_catalog', label: 'Reviewing fields and structure' },
      ],
    });
    state = reduceOperationPreview(state, {
      event_type: 'extraction_progress',
      phase: 'resolve_org',
      message: 'Finding your Salesforce org',
    });
    state = reduceOperationPreview(state, {
      event_type: 'extraction_progress',
      phase: 'resolve_object',
      message: 'Choosing object and fields',
    });
    const connect = state.phases.find((p) => p.id === 'connect_source');
    const inspect = state.phases.find((p) => p.id === 'inspect_catalog');
    expect(connect?.status).toBe(PHASE_STATUS.DONE);
    expect(inspect?.status).toBe(PHASE_STATUS.RUNNING);
  });

  test('agent_plan_ready filter intent uses data_ops pipeline', () => {
    const state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_plan_ready',
      pipeline: [{ id: 'data_ops_filter', label: 'Filter rows' }],
    });
    expect(state.phases.map((p) => p.id)).toEqual(['data_ops_filter']);
    expect(state.phases.find((p) => p.id === 'connect_source')).toBeUndefined();
  });

  test('agent_plan_ready multi-op pipeline from backend', () => {
    const state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'data_ops_transform', label: 'Transform columns' },
        { id: 'data_ops_filter', label: 'Filter rows' },
      ],
    });
    expect(state.pipelineOrder).toEqual([
      'identify_route',
      'data_ops_transform',
      'data_ops_filter',
    ]);
  });

  test('agent_operation_started appends unknown op dynamically', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'classification_completed',
      intent: 'filter',
      operations: ['filter'],
    });
    state = reduceOperationPreview(state, {
      event_type: 'agent_operation_started',
      operation_id: 'validate',
      phase_id: 'data_ops_validate',
      label: 'Validate & clean values',
    });
    expect(state.phases.some((p) => p.id === 'data_ops_validate')).toBe(true);
    expect(state.phases.find((p) => p.id === 'data_ops_validate')?.status).toBe(
      PHASE_STATUS.RUNNING,
    );
  });

  test('spinnerMessageFromOperationPreview uses running phase label', () => {
    const preview = {
      pipelineOrder: ['data_ops_transform'],
      phases: [
        { id: 'data_ops_transform', label: 'Transform columns', status: 'running' },
      ],
    };
    expect(spinnerMessageFromOperationPreview(preview)).toBe('Transform columns');
  });

  test('planReadyUserMessage uses pipeline label not data_ops summary', () => {
    expect(
      planReadyUserMessage({
        summary: 'data_ops: transform',
        pipeline: [{ id: 'data_ops_transform', label: 'Transform columns' }],
      }),
    ).toBe('Transform columns');
  });

  test('primaryPipelineSpinnerLabel skips prelude steps', () => {
    expect(
      primaryPipelineSpinnerLabel([
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'connect_source', label: 'Connecting to Salesforce (Production 1)' },
      ]),
    ).toBe('Connecting to Salesforce (Production 1)');
  });

  test('planReadyUserMessage prefers user_label over architect summary', () => {
    expect(
      planReadyUserMessage({
        summary: 'architect:extract_salesforce',
        user_label: 'Extract contacts from Salesforce',
      }),
    ).toBe('Extract contacts from Salesforce');
    expect(planReadyUserMessage({ summary: 'architect:extract_salesforce' })).toBeNull();
  });

  test('classification_completed compound extract stores context only until agent_plan_ready', () => {
    const state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'classification_completed',
      intent: 'extract',
      mode: 'extract',
      source: 'google_sheets',
      destination: 'google_sheets',
      source_name: 'contact list',
      destination_name: 'contact list',
      data_operations: ['dedupe', 'filter'],
    });
    expect(state.phases).toEqual([]);
    expect(state.context?.source_name).toBe('contact list');
  });

  test('agent_plan_ready compound pipeline includes data ops sub-phases', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'classification_completed',
      intent: 'extract',
      mode: 'extract',
      source: 'google_sheets',
      destination: 'google_sheets',
      source_name: 'contact list',
      destination_name: 'contact list',
      data_operations: ['dedupe', 'filter'],
    });
    state = reduceOperationPreview(state, {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'connect_source', label: 'Connecting to Google Sheets (contact list)' },
        { id: 'inspect_catalog', label: 'Reviewing fields and structure' },
        { id: 'build_extraction_plan', label: 'Building extraction plan' },
        { id: 'extract_records', label: 'Extracting records' },
        { id: 'prepare_preview', label: 'Preparing data preview' },
        { id: 'data_ops_dedupe', label: 'Remove duplicates' },
        { id: 'data_ops_filter', label: 'Filter rows' },
        { id: 'map_fields', label: 'Map fields to contact list' },
        { id: 'start_migration', label: 'Migrate to contact list' },
      ],
    });
    expect(state.phases.map((p) => p.id)).toEqual([
      'identify_route',
      'connect_source',
      'inspect_catalog',
      'build_extraction_plan',
      'extract_records',
      'prepare_preview',
      'data_ops_dedupe',
      'data_ops_filter',
      'map_fields',
      'start_migration',
    ]);
  });

  test('agent_step_started map_fields rebuilds phase when preview was cleared', () => {
    const state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_step_started',
      tool_id: 'map_fields',
      label: 'Map fields to Customer Contact Details',
    });
    expect(state.phases).toHaveLength(1);
    expect(state.phases[0].id).toBe('map_fields');
    expect(state.phases[0].status).toBe(PHASE_STATUS.RUNNING);
    expect(state.phases[0].label).toBe('Map fields to Customer Contact Details');
  });

  test('extraction_job_finished leaves map and migrate phases pending in compound flow', () => {
    let state = reduceOperationPreview(createEmptyOperationPreview(), {
      event_type: 'agent_plan_ready',
      pipeline: [
        { id: 'identify_route', label: 'Identifying source and destination' },
        { id: 'connect_source', label: 'Connecting to source' },
        { id: 'prepare_preview', label: 'Preparing data preview' },
        { id: 'map_fields', label: 'Mapping fields' },
        { id: 'start_migration', label: 'Migrating records' },
      ],
    });
    state = reduceOperationPreview(state, { event_type: 'extraction_job_finished', job_id: 'j1' });
    expect(state.phases.find((p) => p.id === 'prepare_preview')?.status).toBe(PHASE_STATUS.DONE);
    expect(state.phases.find((p) => p.id === 'map_fields')?.status).toBe(PHASE_STATUS.PENDING);
    expect(state.phases.find((p) => p.id === 'start_migration')?.status).toBe(PHASE_STATUS.PENDING);
  });
});
