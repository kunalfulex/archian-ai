import { metadataJobId, metadataMatchesJobId } from './metadataJobId';
import { findLatestMappingPanelForJob } from './mappingPanelHistory';

function entryMatchesExtractionJob(entry, anchorJobId) {
  if (!anchorJobId || !entry) return false;
  const meta = entry.metadata || {};
  if (metadataMatchesJobId(meta, anchorJobId)) return true;
  if (meta.parent_job_id === anchorJobId) return true;
  if (entry.jobId === anchorJobId) return true;
  return false;
}

function isExtractionContextEntry(entry) {
  if (!entry) return false;
  if (entry.type === 'data_preview') return true;
  return entry.metadata?.event_type === 'extraction_completed';
}

export function extractionVersionFromMeta(metadata) {
  if (!metadata || typeof metadata !== 'object') return 1;
  try {
    return parseInt(metadata.extraction_version, 10) || 1;
  } catch {
    return 1;
  }
}

/**
 * Pick the best extraction_completed chat row for save-flow / migration metadata.
 * After data ops, a child job (parent_job_id set) holds merged extraction_rules — prefer highest extraction_version.
 */
export function resolveExtractionCompletedEvent(chatHistory, anchorJobId) {
  if (!anchorJobId || !Array.isArray(chatHistory)) return null;

  const completed = (chatHistory || []).filter(isExtractionContextEntry);

  const related = completed.filter((e) => entryMatchesExtractionJob(e, anchorJobId));

  if (!related.length) {
    return (
      completed.find((e) => metadataMatchesJobId(e.metadata, anchorJobId)) || null
    );
  }

  related.sort(
    (a, b) =>
      extractionVersionFromMeta(b.metadata) - extractionVersionFromMeta(a.metadata),
  );
  return related[0];
}

/** Normalize chat/API source type strings to save-flow keys (sql, gsheets, salesforce, file). */
export function normalizeSourceTypeKey(raw) {
  if (raw == null || raw === '') return null;
  const s = String(raw).toLowerCase();
  if (s === 'google_sheets') return 'gsheets';
  if (['sql', 'gsheets', 'salesforce', 'file'].includes(s)) return s;
  return s;
}

export function resolveExtractedSchemaType({
  sourceType = null,
  destination = null,
  migrationCompletedEvent = null,
  migrationStartedEvent = null,
  extractionCompletedEvent = null,
} = {}) {
  const fromEvents =
    migrationCompletedEvent?.metadata?.extracted_schema_type ||
    migrationStartedEvent?.metadata?.extracted_schema_type ||
    extractionCompletedEvent?.metadata?.extracted_schema_type ||
    null;
  const normalized = normalizeSourceTypeKey(fromEvents || sourceType);
  if (normalized) return normalized;
  const dest = String(destination || '').toLowerCase();
  if (dest === 'sql') return 'sql';
  if (dest === 'google_sheets' || dest === 'gsheets') return 'gsheets';
  if (dest === 'salesforce') return 'salesforce';
  return 'file';
}

/**
 * Resolve source_type for migration_completed / save-flow (avoid defaulting SQL flows to file).
 */
export function resolveSourceTypeForSave({
  chatHistory = [],
  inlineDataPreviews = {},
  sourceJobId = null,
  destination = null,
  migrationCompletedEvent = null,
  migrationStartedEvent = null,
  extractionCompletedEvent = null,
  mappingStartedEvent = null,
  mappingPanelData = null,
  previewData = null,
  extractionRow = null,
} = {}) {
  const candidates = [
    migrationCompletedEvent?.metadata?.source_type,
    migrationStartedEvent?.metadata?.source_type,
    extractionCompletedEvent?.metadata?.source_type,
    mappingStartedEvent?.metadata?.source_type,
    mappingPanelData?.sourceType,
    previewData?.sourceType,
    extractionRow?.source_type,
  ];
  for (const c of candidates) {
    const n = normalizeSourceTypeKey(c);
    if (n && n !== 'file') return n;
  }
  const dest = String(destination || '').toLowerCase();
  if (dest === 'sql') return 'sql';
  if (dest === 'google_sheets' || dest === 'gsheets') return 'gsheets';
  if (dest === 'salesforce') return 'salesforce';
  if (sourceJobId && resolveSourceSqlQuery({
    chatHistory,
    inlineDataPreviews,
    sourceJobId,
    extractionCompletedEvent,
    frontendMappingStartedEvent: mappingStartedEvent,
    mappingPanelData,
    previewData,
  })) {
    return 'sql';
  }
  const n = normalizeSourceTypeKey(candidates.find((c) => c != null && c !== ''));
  return n || 'file';
}

/**
 * Bundle metadata needed when persisting migration_completed or calling save-mapping-flow.
 */
export function resolveMigrationFlowContext({
  chatHistory = [],
  inlineDataPreviews = {},
  mappingPanels = {},
  sourceJobId = null,
  recordsJobId = null,
  destination = null,
  migrationCompletedEvent = null,
  migrationStartedEvent = null,
  extractionRow = null,
} = {}) {
  const extractionCompletedEvent = sourceJobId
    ? resolveExtractionCompletedEvent(chatHistory, sourceJobId)
    : null;
  const mappingStartedEvent =
    (sourceJobId && findLatestMappingPanelForJob(chatHistory, sourceJobId)) || null;
  const mappingId =
    mappingStartedEvent?.metadata?.mapping_id || mappingStartedEvent?.mappingId;
  const mappingPanelData = mappingId ? mappingPanels[mappingId] : null;
  const previewData =
    (sourceJobId && inlineDataPreviews[sourceJobId]) ||
    (recordsJobId && inlineDataPreviews[recordsJobId]) ||
    null;

  const sourceType = resolveSourceTypeForSave({
    chatHistory,
    inlineDataPreviews,
    sourceJobId,
    destination,
    migrationCompletedEvent,
    migrationStartedEvent,
    extractionCompletedEvent,
    mappingStartedEvent,
    mappingPanelData,
    previewData,
    extractionRow,
  });
  const extractedSchemaType = resolveExtractedSchemaType({
    sourceType,
    destination,
    migrationCompletedEvent,
    migrationStartedEvent,
    extractionCompletedEvent,
  });
  const sqlQuery = resolveSourceSqlQuery({
    chatHistory,
    inlineDataPreviews,
    sourceJobId,
    recordsJobId,
    migrationCompletedEvent,
    backendMigrationStartedEvent: migrationStartedEvent,
    extractionCompletedEvent,
    frontendMappingStartedEvent: mappingStartedEvent,
    mappingPanelData,
    previewData,
  });

  const sourceConnectorId =
    migrationStartedEvent?.metadata?.source_connector_id ||
    migrationCompletedEvent?.metadata?.source_connector_id ||
    extractionCompletedEvent?.metadata?.connector_id ||
    mappingStartedEvent?.metadata?.connector_id ||
    mappingPanelData?.connectorId ||
    previewData?.connectorId ||
    null;

  const sourceTableName =
    extractionCompletedEvent?.metadata?.table_name ||
    mappingStartedEvent?.metadata?.table_name ||
    mappingPanelData?.tableName ||
    previewData?.tableName ||
    null;

  const sourceName =
    migrationCompletedEvent?.metadata?.source_name ||
    extractionCompletedEvent?.metadata?.source_name ||
    mappingStartedEvent?.metadata?.source_name ||
    mappingPanelData?.sourceName ||
    previewData?.sourceName ||
    sourceTableName ||
    null;

  return {
    extractionCompletedEvent,
    mappingStartedEvent,
    mappingPanelData,
    previewData,
    sourceType,
    extractedSchemaType,
    sqlQuery,
    sourceConnectorId,
    sourceTableName,
    sourceName,
  };
}

function sqlQueryFromPreview(preview) {
  if (!preview) return null;
  return preview.sqlQuery || preview.query || null;
}

function sqlQueryFromExtractionMeta(metadata) {
  if (!metadata) return null;
  return metadata.sql_query || metadata.query || metadata.soql || null;
}

/**
 * Resolve the source SQL/SOQL used for save-flow and migration metadata.
 * After preview transformation, row data may live on a child job while the query
 * remains on the original extraction (or mapping_started) metadata.
 */
export function resolveSourceSqlQuery({
  chatHistory = [],
  inlineDataPreviews = {},
  sourceJobId = null,
  recordsJobId = null,
  migrationCompletedEvent = null,
  backendMigrationStartedEvent = null,
  extractionCompletedEvent = null,
  frontendMappingStartedEvent = null,
  mappingPanelData = null,
  previewData = null,
} = {}) {
  const fromEvents =
    migrationCompletedEvent?.metadata?.sql_query ||
    backendMigrationStartedEvent?.metadata?.sql_query ||
    sqlQueryFromExtractionMeta(extractionCompletedEvent?.metadata) ||
    frontendMappingStartedEvent?.metadata?.sql_query ||
    null;
  if (fromEvents) return fromEvents;

  const fromPanel =
    sqlQueryFromPreview(previewData) || sqlQueryFromPreview(mappingPanelData) || null;
  if (fromPanel) return fromPanel;

  const walkJobChain = (jobId) => {
    if (!jobId) return null;
    const fromPreview = sqlQueryFromPreview(inlineDataPreviews[jobId]);
    if (fromPreview) return fromPreview;
    const direct = (chatHistory || []).find(
      (e) =>
        isExtractionContextEntry(e) &&
        metadataJobId(e.metadata) === jobId,
    );
    const fromDirect = sqlQueryFromExtractionMeta(direct?.metadata);
    if (fromDirect) return fromDirect;
    const evt = resolveExtractionCompletedEvent(chatHistory, jobId);
    const fromMeta = sqlQueryFromExtractionMeta(evt?.metadata);
    if (fromMeta) return fromMeta;
    const parentId = evt?.metadata?.parent_job_id || direct?.metadata?.parent_job_id;
    if (parentId && parentId !== jobId) return walkJobChain(parentId);
    return null;
  };

  if (sourceJobId) {
    const fromSource = walkJobChain(sourceJobId);
    if (fromSource) return fromSource;
  }
  if (recordsJobId && recordsJobId !== sourceJobId) {
    const fromRecords = walkJobChain(recordsJobId);
    if (fromRecords) return fromRecords;
  }
  return null;
}

/**
 * Prefer the migration_completed row that has save-flow fields (e.g. destination_connector_id).
 * Backend and frontend may both log completion; a sparse event must not win over a full one.
 */
export function findBestMigrationCompletedEvent(chatHistory, { jobId, batchId } = {}) {
  if (!Array.isArray(chatHistory)) return null;
  const matches = chatHistory.filter((e) => {
    if (e?.metadata?.event_type !== 'migration_completed') return false;
    const meta = e.metadata || {};
    return (
      (jobId && (meta.job_id === jobId || meta.batch_id === jobId)) ||
      (batchId && (meta.batch_id === batchId || meta.job_id === batchId))
    );
  });
  if (!matches.length) return null;
  const score = (e) => {
    const m = e.metadata || {};
    let s = 0;
    if (m.destination_connector_id) s += 4;
    if (m.spreadsheet_id || m.destination_spreadsheet_id) s += 4;
    if (m.mapping_config && Object.keys(m.mapping_config).length) s += 3;
    if (m.source_job_id) s += 2;
    if (m.extracted_schema && Object.keys(m.extracted_schema).length) s += 1;
    return s;
  };
  matches.sort((a, b) => {
    const diff = score(b) - score(a);
    if (diff !== 0) return diff;
    return (b.timestamp || b.created_at || 0) - (a.timestamp || a.created_at || 0);
  });
  return matches[0];
}

/**
 * Resolve destination id for save-flow by destination type (SF / SQL / Google Sheets).
 */
export function pickDestinationConnectorIdForSave({
  destination,
  migrationCompletedEvent,
  backendMigrationStartedEvent,
  frontendMappingStartedEvent,
  mappingPanelData,
  extractionCompletedEvent,
  previewData,
  jobStatusSpreadsheetId,
}) {
  const dest = (destination || '').toLowerCase();
  if (dest === 'google_sheets' || dest === 'gsheets') {
    return (
      migrationCompletedEvent?.metadata?.spreadsheet_id ||
      migrationCompletedEvent?.metadata?.destination_spreadsheet_id ||
      migrationCompletedEvent?.metadata?.destination_connector_id ||
      backendMigrationStartedEvent?.metadata?.destination_spreadsheet_id ||
      backendMigrationStartedEvent?.metadata?.spreadsheet_id ||
      backendMigrationStartedEvent?.metadata?.destination_connector_id ||
      frontendMappingStartedEvent?.metadata?.destination_spreadsheet_id ||
      frontendMappingStartedEvent?.metadata?.spreadsheet_id ||
      frontendMappingStartedEvent?.metadata?.destination_connector_id ||
      mappingPanelData?.destinationSpreadsheetId ||
      mappingPanelData?.destinationConnectorId ||
      jobStatusSpreadsheetId ||
      null
    );
  }
  if (dest === 'sql') {
    return (
      migrationCompletedEvent?.metadata?.destination_connector_id ||
      backendMigrationStartedEvent?.metadata?.destination_connector_id ||
      frontendMappingStartedEvent?.metadata?.destination_connector_id ||
      mappingPanelData?.destinationConnectorId ||
      null
    );
  }
  if (dest === 'salesforce') {
    return pickSalesforceDestinationConnectorId({
      migrationCompletedEvent,
      backendMigrationStartedEvent,
      frontendMappingStartedEvent,
      mappingPanelData,
      extractionCompletedEvent,
      previewData,
    });
  }
  return (
    migrationCompletedEvent?.metadata?.destination_connector_id ||
    backendMigrationStartedEvent?.metadata?.destination_connector_id ||
    frontendMappingStartedEvent?.metadata?.destination_connector_id ||
    mappingPanelData?.destinationConnectorId ||
    null
  );
}

/** Resolve Salesforce org/token id for save-flow from chat + mapping panel state. */
export function pickSalesforceDestinationConnectorId({
  migrationCompletedEvent,
  backendMigrationStartedEvent,
  frontendMappingStartedEvent,
  mappingPanelData,
  extractionCompletedEvent,
  previewData,
}) {
  return (
    migrationCompletedEvent?.metadata?.destination_connector_id ||
    backendMigrationStartedEvent?.metadata?.destination_connector_id ||
    frontendMappingStartedEvent?.metadata?.destination_connector_id ||
    mappingPanelData?.destinationConnectorId ||
    extractionCompletedEvent?.metadata?.salesforce_token_id ||
    extractionCompletedEvent?.metadata?.destination_connector_id ||
    previewData?.salesforceTokenId ||
    previewData?.connectorId ||
    null
  );
}

/** Extraction-stage rules (filter / dedupe / transforms), not mapping-panel field splits. */
export function resolveExtractionRules(metadata, previewData) {
  if (metadata?.extraction_rules && typeof metadata.extraction_rules === 'object') {
    return metadata.extraction_rules;
  }
  const intent = metadata?.intent_state;
  if (intent?.extraction_rules && typeof intent.extraction_rules === 'object') {
    return intent.extraction_rules;
  }
  if (previewData?.extractionRules && typeof previewData.extractionRules === 'object') {
    return previewData.extractionRules;
  }
  return null;
}
