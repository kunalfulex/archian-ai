/**
 * Shared helpers for resolving mapping panel state (UI button + agent start_migration).
 */
import { MAPPING_TRANSFORMATION_KEY, getMappingTransformation } from '../utils/migrationRulesKeys';
import { pickMostCompleteRecord } from '../utils/pickMostCompleteRecord';
import { metadataJobId } from './metadataJobId';
import { resolveExtractionCompletedEvent } from './resolveExtractionContextForSave';

const META_ONLY_KEYS = new Set([
  'available_sheets',
  'available_tables',
  'available_objects',
  'spreadsheet_id',
  'destination',
  'destination_name',
  'document_type',
  'document_description',
  'mapping_plan',
  'primary_table',
]);

export function normalizeMappingConfig(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  if (raw.mapping && typeof raw.mapping === 'object' && !Array.isArray(raw.mapping)) {
    return raw.mapping;
  }
  return raw;
}

export function hasUsableMapping(mapping) {
  const cfg = normalizeMappingConfig(mapping);
  if (!cfg || typeof cfg !== 'object') return false;
  return Object.keys(cfg).some((k) => !META_ONLY_KEYS.has(k));
}

export function buildPanelDataFromMapPayload(payload, inlineDataPreviews = {}) {
  if (!payload) return null;
  const jobId = payload.job_id;
  const mappingConfig =
    normalizeMappingConfig(payload.mapping_config) ||
    normalizeMappingConfig(payload.mapping) ||
    null;
  if (!hasUsableMapping(mappingConfig)) return null;

  const preview = jobId ? inlineDataPreviews[jobId] : null;
  const transformationRules =
    payload[MAPPING_TRANSFORMATION_KEY] ||
    payload.transformation_rules ||
    getMappingTransformation(payload) ||
    { fields: [] };

  return {
    mapping: mappingConfig,
    transformationRules,
    objectFieldMap:
      payload.mapping?.available_objects ||
      payload.mapping?.available_sheets ||
      payload.mapping?.available_tables ||
      payload.object_field_map ||
      {},
    documentType:
      payload.destination_document_type || payload.document_type || preview?.documentType,
    documentDescription:
      payload.destination_document_description ||
      payload.document_description ||
      preview?.documentDescription,
    destination: payload.destination || preview?.destination,
    destinationName: payload.destination_name || preview?.destinationName,
    destinationConnectorId: payload.destination_connector_id,
    destinationSpreadsheetId:
      payload.mapping?.spreadsheet_id || payload.destination_spreadsheet_id,
    firstRecord: pickMostCompleteRecord(preview?.data || []),
    allRecords: preview?.data || [],
    totalRecordCount: preview?.data?.length ?? preview?.recordCount ?? 0,
    jobId,
  };
}

export function findLatestMappingPanelEntry(history, jobId) {
  const historyList = history || [];
  for (let i = historyList.length - 1; i >= 0; i--) {
    const e = historyList[i];
    if (e.type !== 'mapping_panel' || e.isLoading || e.isUnavailable) continue;
    const panelJobId = e.metadata?.job_id;
    if (jobId && panelJobId && panelJobId !== jobId) continue;
    return e;
  }
  return null;
}

export function buildPanelDataFromMappingEntry(entry, inlineDataPreviews = {}) {
  if (!entry?.metadata) return null;
  const meta = entry.metadata;
  const mappingConfig = normalizeMappingConfig(meta.mapping_config);
  if (!hasUsableMapping(mappingConfig)) return null;

  const jobId = meta.job_id;
  const preview = jobId ? inlineDataPreviews[jobId] : null;

  return {
    mapping: mappingConfig,
    transformationRules:
      getMappingTransformation(meta) || meta.transformation_rules || { fields: [] },
    objectFieldMap: meta.object_field_map || {},
    documentType: meta.destination_document_type || meta.document_type,
    documentDescription: meta.destination_document_description || meta.document_description,
    destination: meta.destination,
    destinationName: meta.destination_name,
    destinationConnectorId: meta.destination_connector_id,
    destinationSpreadsheetId: meta.destination_spreadsheet_id,
    firstRecord: pickMostCompleteRecord(preview?.data || []),
    allRecords: preview?.data || [],
    totalRecordCount: meta.record_count ?? preview?.data?.length ?? 0,
    jobId,
    primaryTable: meta.primary_table ?? null,
    orgRulesDisplayByObject: meta.org_rules_display_by_object || {},
    availableSheets: meta.available_sheets,
    availableTables: meta.available_tables,
  };
}

export function panelMatchesExtractionJob(panelJobId, targetJobId, history) {
  if (!panelJobId || !targetJobId) return false;
  if (panelJobId === targetJobId) return true;
  const latest = resolveExtractionCompletedEvent(history, panelJobId);
  if (latest && metadataJobId(latest.metadata) === targetJobId) return true;
  const latestFromTarget = resolveExtractionCompletedEvent(history, targetJobId);
  if (latestFromTarget) {
    const resolved = metadataJobId(latestFromTarget.metadata);
    return (
      panelJobId === resolved || panelJobId === latestFromTarget.metadata?.parent_job_id
    );
  }
  return false;
}
