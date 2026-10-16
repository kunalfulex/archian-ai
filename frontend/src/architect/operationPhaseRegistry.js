/**
 * Operation preview — steps come from backend `agent_plan_ready.pipeline` (dynamic per turn).
 * Classification prelude ids are fixed; everything after is plan-driven.
 */

import { planHasCreateStep } from './plannerPlanDisplay';

export const PHASE_STATUS = {
  PENDING: 'pending',
  RUNNING: 'running',
  DONE: 'done',
  ERROR: 'error',
};

/** Execution prelude — prompt parsing lives in the planning loader, not here. */
export const EXECUTION_PRELUDE_PHASE_IDS = new Set(['identify_route']);

/** Full prelude while classifying extract / map / migrate turns. */
export const CLASSIFICATION_PHASES = [
  { id: 'identify_route', label: 'Identifying source and destination' },
];

/** Data-ops on an in-session dataset — no classification prelude in execution. */
export const DATA_OPS_CLASSIFICATION_PHASES = [];

/** Extract-only when no destination was named — omit destination routing step. */
export const EXTRACT_ONLY_CLASSIFICATION_PHASES = [
  { id: 'identify_route', label: 'Identifying source' },
];

function destinationIsKnown(classification) {
  const contractDest = classification?.plan_contract?.destination;
  if (contractDest?.display_name?.trim()) {
    return true;
  }
  const dest = (classification?.destination || '').toLowerCase();
  const destName = (classification?.destination_name || '').trim().toLowerCase();
  return Boolean(dest && dest !== 'unknown' && destName && destName !== 'unknown');
}

function classificationPlanSteps(classification) {
  const fromExecution = classification?.execution_plan?.steps;
  if (Array.isArray(fromExecution) && fromExecution.length) {
    return fromExecution;
  }
  const summary = classification?.plan_steps_summary;
  return Array.isArray(summary) ? summary : [];
}

function classificationMaterializesCreate(classification) {
  const steps = classificationPlanSteps(classification);
  if (steps.length) {
    return planHasCreateStep(steps);
  }
  const ops = Array.isArray(classification?.operations)
    ? classification.operations.map((o) => String(o).toLowerCase())
    : [];
  return ops.includes('create');
}

function needsDestinationRouting(classification) {
  const intent = (classification?.intent || '').toLowerCase();
  const mode = (classification?.mode || '').toLowerCase();
  const ops = Array.isArray(classification?.operations)
    ? classification.operations.map((o) => String(o).toLowerCase())
    : [];
  if (ops.some((o) => ['map', 'migrate', 'upsert'].includes(o))) return true;
  if (ops.includes('create') && classificationMaterializesCreate(classification)) return true;
  return (
    intent === 'map_data' ||
    mode === 'map' ||
    intent === 'migrate_data' ||
    intent === 'migrate' ||
    mode === 'migrate' ||
    ((intent === 'create' || mode === 'create') && classificationMaterializesCreate(classification)) ||
    intent === 'upsert'
  );
}

function isStandaloneUniversalOp(classification) {
  const intent = (classification?.intent || '').toLowerCase();
  const ops = Array.isArray(classification?.operations)
    ? classification.operations.map((o) => String(o).toLowerCase())
    : [];
  const standalone = new Set(['create', 'join', 'deploy', 'deploy_schema', 'upsert', 'update', 'search']);
  if (standalone.has(intent)) return true;
  return ops.length === 1 && standalone.has(ops[0]);
}

function normalizeDataOpsList(classification) {
  const raw = classification?.data_operations;
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  return raw
    .map(normalizeDataOpId)
    .filter((id) => {
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
}

function isCompoundExtractWithDataOps(classification) {
  const mode = (classification?.mode || '').toLowerCase();
  const dataOps = normalizeDataOpsList(classification);
  if (!dataOps.length || mode !== 'extract') return false;
  return destinationIsKnown(classification);
}

function isStandaloneDataOpsClassification(classification) {
  if (isCompoundExtractWithDataOps(classification)) return false;
  const mode = (classification?.mode || '').toLowerCase();
  const intent = (classification?.intent || '').toLowerCase();
  const rawOps = Array.isArray(classification?.operations) ? classification.operations : [];
  if (mode === 'data_ops' || rawOps.length > 0) return true;
  return ['transform', 'filter', 'dedupe', 'validate', 'multi', 'cleanup'].includes(intent);
}

/** Prelude steps for this turn (1 step for data-ops, 2 for extract/map/migrate). */
export function classificationPreludePhases(classification) {
  if (isStandaloneDataOpsClassification(classification)) {
    return DATA_OPS_CLASSIFICATION_PHASES;
  }
  if (isStandaloneUniversalOp(classification)) {
    return [];
  }
  if (!destinationIsKnown(classification) && !needsDestinationRouting(classification)) {
    return EXTRACT_ONLY_CLASSIFICATION_PHASES;
  }
  return CLASSIFICATION_PHASES;
}

/** Legacy extract fallback when no agent pipeline SSE (direct handler route). */
export const EXTRACT_PIPELINE_PHASES = [
  ...CLASSIFICATION_PHASES,
  { id: 'connect_source', label: 'Connecting to source' },
  { id: 'inspect_catalog', label: 'Reviewing fields and structure' },
  { id: 'build_extraction_plan', label: 'Building extraction plan' },
  { id: 'extract_records', label: 'Extracting records' },
  { id: 'prepare_preview', label: 'Preparing data preview' },
];

const STATIC_PHASE_LABELS = {
  understand_request: 'Understanding your request',
  identify_route: 'Identifying source and destination',
  connect_source: 'Connecting to source',
  inspect_catalog: 'Reviewing fields and structure',
  build_extraction_plan: 'Building extraction plan',
  extract_records: 'Extracting records',
  prepare_preview: 'Preparing data preview',
  map_fields: 'Mapping fields',
  migrate_records: 'Migrating records',
  start_migration: 'Migrating records',
  create_spreadsheet: 'Create spreadsheet',
  deploy_schema: 'Deploy Salesforce metadata',
  gsheets_execute_operation: 'Run Sheets operation',
  update_spreadsheet_metadata: 'Update spreadsheet metadata',
  load_flow: 'Load saved flow',
  save_flow: 'Save as flow',
};

function normalizeDataOpId(op) {
  const k = (op || '').toLowerCase();
  if (k === 'transformation') return 'transform';
  if (k === 'deduplication') return 'dedupe';
  if (k === 'validation') return 'validate';
  return k;
}

export function dataOpsPhaseId(operationId) {
  const canon = normalizeDataOpId(operationId);
  return canon ? `data_ops_${canon}` : 'data_ops_operation';
}

/** Human label from phase id when SSE did not include a label. */
export function humanizePhaseId(id) {
  if (!id) return 'Processing';
  if (STATIC_PHASE_LABELS[id]) return STATIC_PHASE_LABELS[id];
  if (id.startsWith('data_ops_')) {
    const op = id.slice('data_ops_'.length).replace(/_/g, ' ');
    return op ? op.charAt(0).toUpperCase() + op.slice(1) : 'Processing data';
  }
  return id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function sourceDisplayName(source) {
  const s = (source || '').toLowerCase();
  if (s === 'google_sheets' || s === 'gsheets') return 'Google Sheets';
  if (s === 'salesforce') return 'Salesforce';
  if (s === 'sql') return 'SQL database';
  if (s === 'file') return 'File';
  return source || 'Source';
}

export function connectSourceLabel(context) {
  const sn = context?.source_name?.trim();
  const src = sourceDisplayName(context?.source);
  return sn ? `Connecting to ${src} (${sn})` : `Connecting to ${src}`;
}

export function formatIdentifyRouteDetail(context) {
  if (!context) return null;
  const sn = context.source_name?.trim();
  const contractDest = context.plan_contract?.destination?.display_name?.trim();
  const dn = contractDest || context.destination_name?.trim();
  if (sn || dn) {
    return `${sn || ''}${dn ? ` → ${dn}` : ''}`.trim();
  }
  return null;
}

/**
 * Brief fallback before agent_plan_ready — uses classifier `operations` only (no fixed multi/cleanup lists).
 */
export function buildPipelineFromClassification(classification) {
  const intent = (classification?.intent || '').toLowerCase();
  const mode = (classification?.mode || '').toLowerCase();
  const rawOps = Array.isArray(classification?.operations) ? classification.operations : [];

  if (isCompoundExtractWithDataOps(classification)) {
    const dataOps = normalizeDataOpsList(classification);
    return [
      ...classificationPreludePhases(classification),
      ...EXTRACT_PIPELINE_PHASES.slice(CLASSIFICATION_PHASES.length).map((p) => ({ ...p })),
      ...dataOps.map((op) => ({
        id: dataOpsPhaseId(op),
        label: humanizePhaseId(dataOpsPhaseId(op)),
      })),
      { id: 'map_fields', label: 'Mapping fields' },
      { id: 'start_migration', label: 'Migrating records' },
    ];
  }

  if (isStandaloneDataOpsClassification(classification)) {
    let opIds = rawOps.map(normalizeDataOpId).filter(Boolean);
    if (!opIds.length && intent && !['multi', 'cleanup', 'general'].includes(intent)) {
      opIds = [normalizeDataOpId(intent)];
    }
    const seen = new Set();
    opIds = opIds.filter((id) => {
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
    return [
      ...classificationPreludePhases(classification),
      ...opIds.map((op) => ({
        id: dataOpsPhaseId(op),
        label: humanizePhaseId(dataOpsPhaseId(op)),
      })),
    ];
  }

  if (intent === 'map_data' || mode === 'map') {
    return [...classificationPreludePhases(classification), { id: 'map_fields', label: 'Mapping fields' }];
  }
  if (intent === 'migrate_data' || intent === 'migrate' || mode === 'migrate') {
    return [...classificationPreludePhases(classification), { id: 'migrate_records', label: 'Migrating records' }];
  }
  if ((intent === 'create' || rawOps.includes('create')) && classificationMaterializesCreate(classification)) {
    return [...classificationPreludePhases(classification), { id: 'create_spreadsheet', label: 'Create spreadsheet' }];
  }
  if (intent === 'metadata_deploy' || rawOps.includes('deploy') || rawOps.includes('deploy_schema')) {
    return [...classificationPreludePhases(classification), { id: 'deploy_schema', label: 'Deploy Salesforce metadata' }];
  }
  if (rawOps.includes('join') || intent === 'join') {
    return [...classificationPreludePhases(classification), { id: 'gsheets_execute_operation', label: 'Combine sources' }];
  }
  if (intent === 'upsert' || rawOps.includes('upsert')) {
    return [...classificationPreludePhases(classification), { id: 'gsheets_execute_operation', label: 'Upsert rows' }];
  }
  if (intent === 'update' || rawOps.includes('update')) {
    return [...classificationPreludePhases(classification), { id: 'update_spreadsheet_metadata', label: 'Update spreadsheet metadata' }];
  }
  if (rawOps.includes('search') || intent === 'search') {
    return [...classificationPreludePhases(classification), { id: 'gsheets_execute_operation', label: 'Search spreadsheet' }];
  }
  return EXTRACT_PIPELINE_PHASES.map((p) => ({ ...p }));
}

export function leadingPreludeDoneThroughIndex(definitions) {
  const defs = Array.isArray(definitions) ? definitions : [];
  let last = -1;
  for (let i = 0; i < defs.length; i += 1) {
    const id = defs[i].id || defs[i].phase;
    if (EXECUTION_PRELUDE_PHASE_IDS.has(id)) last = i;
    else break;
  }
  return last;
}

export function phasesFromPipelineDefinitions(
  definitions,
  context,
  { throughIndex = -1, doneThroughIndex } = {},
) {
  const defs = Array.isArray(definitions) ? definitions : [];
  const markDoneThrough =
    doneThroughIndex !== undefined
      ? doneThroughIndex
      : throughIndex > 0
        ? throughIndex - 1
        : -1;
  return defs.map((def, i) => {
    const id = def.id || def.phase;
    let label = def.label || humanizePhaseId(id);
    if (id === 'connect_source' && context) {
      label = connectSourceLabel(context);
    }
    let status = PHASE_STATUS.PENDING;
    if (markDoneThrough >= 0 && i <= markDoneThrough) status = PHASE_STATUS.DONE;
    else if (doneThroughIndex === undefined && i === throughIndex) status = PHASE_STATUS.RUNNING;
    return {
      id,
      label,
      status,
      detail: id === 'identify_route' ? formatIdentifyRouteDetail(context) : null,
      progress: null,
    };
  });
}

export function buildInitialOperationPreview(classification) {
  const definitions = buildPipelineFromClassification(classification);
  return {
    phases: phasesFromPipelineDefinitions(definitions, classification, { throughIndex: -1 }),
    pipelineOrder: definitions.map((d) => d.id),
    context: classification
      ? {
          source: classification.source,
          destination: classification.destination,
          intent: classification.intent,
          mode: classification.mode,
          source_name: classification.source_name,
          destination_name:
            classification.plan_contract?.destination?.display_name ||
            classification.destination_name,
          operations: classification.operations,
          plan_contract: classification.plan_contract,
        }
      : null,
  };
}

export function labelForPhaseId(id, context) {
  if (id === 'connect_source' && context) return connectSourceLabel(context);
  return STATIC_PHASE_LABELS[id] || humanizePhaseId(id);
}

export function resolveDataOpsPhaseId(event) {
  if (event?.phase_id) return event.phase_id;
  const opId = event?.operation_id;
  if (!opId) return null;
  return opId.startsWith('data_ops_') ? opId : dataOpsPhaseId(opId);
}

/** Phases that run after extract/preview in compound extract → map/migrate plans. */
export function isPostExtractPipelinePhaseId(phaseId) {
  const id = (phaseId || '').trim();
  if (!id) return false;
  return (
    id === 'map_fields' ||
    id === 'start_migration' ||
    id === 'migrate_records' ||
    id.startsWith('data_ops_')
  );
}

/** True when architect plan still has map/migrate/data-ops work after an extract preview. */
export function hasPendingPostExtractPhases(preview) {
  const phases = preview?.phases || [];
  if (!phases.length) return false;
  const order =
    preview?.pipelineOrder?.length > 0
      ? preview.pipelineOrder
      : phases.map((p) => p.id);
  return order.some((id) => {
    if (!isPostExtractPipelinePhaseId(id)) return false;
    const p = phases.find((ph) => ph.id === id);
    return (
      !p ||
      p.status === PHASE_STATUS.PENDING ||
      p.status === PHASE_STATUS.RUNNING
    );
  });
}

/** Single-step loader for inline "Map to …" from data preview (not architect stream). */
export function createInlineMappingOperationPreview(label = 'Mapping fields') {
  const text = (label || 'Mapping fields').trim();
  return {
    phases: [
      {
        id: 'map_fields',
        label: text,
        status: PHASE_STATUS.RUNNING,
        detail: null,
        progress: null,
      },
    ],
    pipelineOrder: ['map_fields'],
    context: null,
  };
}
