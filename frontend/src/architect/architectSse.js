/**
 * Architect / extraction SSE parsing — shared so ChatUploader stays lean.
 */
import { extractionJobIdFromPayload } from './extractionJobId';

export const INTERNAL_SSE_EVENT_TYPES = new Set([
  'migration_started',
  'migration_progress',
  'migration_job_finished',
  'migration_completed',
  'extraction_job_created',
  'extraction_progress',
  'extraction_job_finished',
  'classification_started',
  'classification_completed',
  'operation_phase',
  'planning_activity',
  'recovery_analysis_started',
  'recovery_activity',
  'agent_planning',
  'agent_plan_ready',
  'agent_step_started',
  'agent_step_completed',
  'agent_turn_completed',
  'agent_operation_started',
  'agent_operation_completed',
  'planner_clarification_required',
  'planner_plan_execution_clarification',
  'planner_plan_ready',
  'planner_error',
  'planner_schema_deploy_required',
  'planner_recovery_required',
  'execution_observation',
  'general_chat_completed',
  'assistant_message',
]);

/** Structured data events / SSE payloads — never show raw JSON in chat transcript. */
export const HIDDEN_CHAT_DATA_EVENT_TYPES = new Set([
  'plan_execution_started',
  'plan_step_checkpoint',
  'spreadsheet_creation_started',
  'spreadsheet_created',
]);

/** Agent tool results consumed by UI handlers — never show in chat transcript. */
const AGENT_UI_ONLY_ACTIONS = new Set([
  'map_fields_ready',
  'migration_started',
]);

export function isAgentUiOnlyPayload(d) {
  if (!d || typeof d !== 'object') return false;
  if (d.action && AGENT_UI_ONLY_ACTIONS.has(d.action)) return true;
  const r = d.result;
  if (r && typeof r === 'object') {
    if (r.action && AGENT_UI_ONLY_ACTIONS.has(r.action)) return true;
    if (r.mapping_config && typeof r.mapping_config === 'object') return true;
    if (r.mapping && typeof r.mapping === 'object') return true;
  }
  if (d.mapping_config && typeof d.mapping_config === 'object' && d.job_id) return true;
  return false;
}

/** True when the streamed assistant text is only raw `data: {...}` lines (no user-facing prose). */
export function isSseJsonOnlyTranscript(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return true;
  const lines = trimmed.split('\n').map((l) => l.trim()).filter(Boolean);
  if (lines.length === 0) return true;
  return lines.every((line) => /^data:\s*\{/.test(line));
}

export function parseSSEDataObjects(text) {
  const objs = [];
  if (!text || !text.includes('data:')) return objs;
  for (const line of text.split('\n')) {
    const m = line.match(/^data:\s*(\{.+)\s*$/);
    if (!m) continue;
    try {
      objs.push(JSON.parse(m[1].trim()));
    } catch (_) {
      /* skip malformed */
    }
  }
  return objs;
}

/** True when SSE payload is a turn-level failure (not a planning/execution step status). */
export function isFatalStreamError(data) {
  if (!data || typeof data !== 'object') return false;
  const eventType = String(data.event_type || '').trim();
  if (eventType === 'planner_error') return true;
  if (eventType === 'planning_activity' || eventType === 'operation_phase' || eventType === 'recovery_activity') {
    return false;
  }
  return data.status === 'error';
}

/**
 * Architect stream handlers often return '' after updating UI (clarification / plan ready).
 * That must NOT abort the SSE loop — classification_completed can arrive before
 * planner_clarification_required (which carries _pending_file_ref for Run plan).
 * Only a non-empty string (assistant text / hard stop) should end the consumer early.
 */
export function shouldAbortArchitectStreamOnHandlerResult(early) {
  if (early == null) return false;
  if (typeof early === 'string') return early.length > 0;
  return true;
}

function nestedResultEventType(d) {
  const r = d?.result;
  if (!r || typeof r !== 'object') return '';
  return String(r.event_type || '').trim();
}

function isInternalNestedResultPayload(d) {
  const nested = nestedResultEventType(d);
  if (!nested) return false;
  return INTERNAL_SSE_EVENT_TYPES.has(nested) || HIDDEN_CHAT_DATA_EVENT_TYPES.has(nested);
}

export function isHiddenArchitectDataPayload(d) {
  if (!d || typeof d !== 'object' || d.event_type) return false;
  // Inline table preview (SQL extract, Sheets join) — rendered as data_preview card, not markdown.
  if (d.status === 'success' && Array.isArray(d.data) && d.source_type) return true;
  if (isAgentUiOnlyPayload(d)) return true;
  if (d.status === 'processing' && typeof d.message === 'string' && d.message.includes('post-extract')) {
    return true;
  }
  if (d.status === 'success' && d.job_id) {
    if (isInternalNestedResultPayload(d)) return true;
    if (d.extraction_version != null && Array.isArray(d.operations_applied)) return true;
    if (d.result && typeof d.result === 'object') {
      const r = d.result;
      const rows = r.data ?? r.extracted_data;
      if (Array.isArray(rows) && rows.length > 0) return false;
      if (Array.isArray(r.operations_applied) || r.extraction_version != null) return true;
      if (r.event_type && INTERNAL_SSE_EVENT_TYPES.has(r.event_type)) return true;
      const keys = Object.keys(r).filter((k) => r[k] != null && r[k] !== '');
      const thinOnly = keys.every((k) => ['job_id', 'active_job_id'].includes(k));
      if (keys.length === 0 || thinOnly) return true;
    } else {
      return true;
    }
  }
  return false;
}

/** Whether a `data: {...}` line should be omitted from streaming markdown transcript. */
export function shouldHideSseDataLine(d) {
  if (!d || typeof d !== 'object') return false;
  if (d.event_type && HIDDEN_CHAT_DATA_EVENT_TYPES.has(d.event_type)) return true;
  if (d.event_type && INTERNAL_SSE_EVENT_TYPES.has(d.event_type)) return true;
  if (isHiddenArchitectDataPayload(d)) return true;
  if (isInternalNestedResultPayload(d)) return true;
  if (d.status === 'processing' && extractionJobIdFromPayload(d)) return true;
  return false;
}

export function filterSseChunkForDisplay(chunk) {
  const lines = chunk.split('\n');
  return lines
    .filter((line) => {
      const m = line.match(/data:\s*(\{.*\})/);
      if (!m) return true;
      try {
        return !shouldHideSseDataLine(JSON.parse(m[1]));
      } catch (_) {
        return true;
      }
    })
    .join('\n');
}

export function chunkHasInternalSseOnly(chunk, sseDataObjects) {
  const hasDataPrefix = chunk.includes('data:');
  if (!hasDataPrefix) return false;

  const hasExtractionJobCreated =
    chunk.includes('"extraction_job_created"') || chunk.includes('"event_type":"extraction_job_created"');
  const hasMigrationStarted =
    chunk.includes('"migration_started"') || chunk.includes('"event_type":"migration_started"');
  const hasMigrationProgress =
    chunk.includes('"migration_progress"') || chunk.includes('"event_type":"migration_progress"');
  const hasMigrationJobFinished =
    chunk.includes('"migration_job_finished"') ||
    chunk.includes('"event_type":"migration_job_finished"');
  const hasRecoveryActivity =
    chunk.includes('"recovery_activity"') ||
    chunk.includes('"recovery_analysis_started"');
  const hasExtractionProgress = chunk.includes('"extraction_progress"');
  const hasExtractionJobFinished = chunk.includes('"extraction_job_finished"');
  const hasOperationPreviewSse =
    chunk.includes('"classification_started"') ||
    chunk.includes('"classification_completed"') ||
    chunk.includes('"operation_phase"');
  const hasAgentSse =
    chunk.includes('"agent_planning"') ||
    chunk.includes('"agent_plan_ready"') ||
    chunk.includes('"agent_step_started"') ||
    chunk.includes('"agent_step_completed"') ||
    chunk.includes('"agent_turn_completed"') ||
    chunk.includes('"agent_operation_started"') ||
    chunk.includes('"agent_operation_completed"');
  const hasStatusProcessing = chunk.includes('"status": "processing"') || chunk.includes('"status":"processing"');
  const hasJobId = chunk.includes('"job_id"') || chunk.includes('"jobId"');

  const hasInternalDataOpsTerminal = sseDataObjects.some((d) => {
    if (!d || typeof d !== 'object' || d.event_type) return false;
    if (isAgentUiOnlyPayload(d)) return true;
    if (d.status === 'processing' && typeof d.message === 'string' && d.message.includes('post-extract')) {
      return true;
    }
    if (d.status !== 'success' || !d.job_id) return false;
    if (isInternalNestedResultPayload(d)) return true;
    if (d.extraction_version != null && Array.isArray(d.operations_applied)) return true;
    if (d.result && typeof d.result === 'object') {
      const r = d.result;
      const rows = r.data ?? r.extracted_data;
      if (Array.isArray(rows) && rows.length > 0) return false;
      if (Array.isArray(r.operations_applied) || r.extraction_version != null) return true;
      const keys = Object.keys(r).filter((k) => r[k] != null && r[k] !== '');
      const thinOnly = keys.every((k) => ['job_id', 'active_job_id'].includes(k));
      if (keys.length === 0 || thinOnly) return true;
    } else {
      return true;
    }
    return false;
  });

  return (
    hasExtractionJobCreated ||
    hasMigrationStarted ||
    hasMigrationProgress ||
    hasMigrationJobFinished ||
    hasRecoveryActivity ||
    hasExtractionProgress ||
    hasExtractionJobFinished ||
    hasOperationPreviewSse ||
    hasAgentSse ||
    hasInternalDataOpsTerminal ||
    (hasStatusProcessing && hasJobId)
  );
}
