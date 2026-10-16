/**
 * Labels for post-extract / data-ops steps shown beside extraction previews.
 * Keep roughly aligned with backend agent/tools/data_ops.py _OP_LABELS.
 */
export const DATA_OPERATIONS_SECTION_TITLE = 'Data operations';

/** Live SSE agent steps (extract/migrate/info tools), not only post-extract ops */
export const AGENT_STREAM_SECTION_TITLE = '';

const OPERATION_STEP_LABELS = {
  transform: 'Transform columns',
  transformation: 'Transform columns',
  filter: 'Filter rows',
  dedupe: 'Remove duplicates',
  deduplication: 'Remove duplicates',
  validate: 'Validate & clean values',
  validation: 'Validate & clean values',
  cleanup_values: 'Clean values',
};

const GENERIC_DETAIL_RE = /^updated extraction rules for this step\.?$/i;

/** Architect-only tools (GSheets execute, extract handlers) — not user-facing data ops. */
export const INTERNAL_ARCHITECT_TOOL_IDS = new Set([
  'gsheets_execute_operation',
  'extract_google_sheets',
  'extract_sql',
  'extract_salesforce',
  'extract_file',
  'file_reextract',
]);

export function normalizePipelineStepsForPersist(steps) {
  if (!Array.isArray(steps) || steps.length === 0) return steps;
  return steps.map((s) => ({
    ...s,
    status: s.status === 'error' ? 'error' : 'done',
  }));
}

/** Drop internal architect steps so extract previews do not show stuck loaders. */
export function filterInternalArchitectPipelineSteps(steps) {
  if (!Array.isArray(steps) || steps.length === 0) return null;
  const filtered = steps.filter((s) => s?.tool_id && !INTERNAL_ARCHITECT_TOOL_IDS.has(s.tool_id));
  return filtered.length ? filtered : null;
}

function formatOperationId(opId) {
  if (!opId || typeof opId !== 'string') return '';
  return opId
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Build fallback step rows when SSE persisted only generic summaries.
 */
export function stepsFromOperationsApplied(operationsApplied) {
  if (!Array.isArray(operationsApplied) || operationsApplied.length === 0) return null;
  return operationsApplied.map((opId) => ({
    tool_id: opId,
    label: OPERATION_STEP_LABELS[opId] || formatOperationId(opId),
    detail: null,
    status: 'done',
  }));
}

function isGenericStepDetail(detail) {
  return !detail || GENERIC_DETAIL_RE.test(String(detail).trim());
}

/**
 * Prefer concrete pipeline_steps from the server; if every step is generic, use operations_applied.
 */
export function enhanceStepsWithOperationsApplied(steps, operationsApplied) {
  const fallback = stepsFromOperationsApplied(operationsApplied);
  if (!fallback) return steps || null;
  if (!steps || steps.length === 0) return fallback;
  if (steps.every((s) => isGenericStepDetail(s.detail))) return fallback;
  return steps;
}

/** Steps to show under extraction previews (post-extract ops only, not GSheets micro-plan). */
export function pipelineStepsForPreviewDisplay(steps, operationsApplied) {
  const withoutInternal = filterInternalArchitectPipelineSteps(
    normalizePipelineStepsForPersist(steps),
  );
  return enhanceStepsWithOperationsApplied(withoutInternal, operationsApplied);
}
