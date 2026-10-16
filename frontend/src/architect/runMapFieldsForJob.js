/**
 * Map via POST /conversations/{id}/actions/map|remap (server-side rows by job_id).
 */
import api from '../api';
import { getMappingTransformationFromMapResponse } from '../utils/migrationRulesKeys';
import { pickMostCompleteRecord } from '../utils/pickMostCompleteRecord';

export async function runMapFieldsForJob({
  jobId,
  destination,
  destinationName,
  destTarget,
  mappingContextSummary,
  sortedFirstRecord,
  sortedAllRecords,
  inlineDataPreviews,
  chatHistory,
  metadataMatchesJobId,
  signal,
  conversationId,
  remap = false,
}) {
  if (!conversationId) {
    throw new Error('Mapping requires an active conversation.');
  }

  const previewData = inlineDataPreviews[jobId];
  if (!previewData) {
    throw new Error('Extraction preview not found for this job.');
  }

  const extractionCompletedEvent = (chatHistory || []).find(
    (e) =>
      e.metadata?.event_type === 'extraction_completed' &&
      metadataMatchesJobId(e.metadata, jobId),
  );

  const destinationForMap =
    destination ||
    previewData.destination ||
    extractionCompletedEvent?.metadata?.destination;
  const destinationNameForMap =
    destinationName ||
    previewData.destinationName ||
    extractionCompletedEvent?.metadata?.destination_name ||
    null;

  if (
    !destinationForMap ||
    destinationForMap === 'unknown' ||
    extractionCompletedEvent?.metadata?.requires_destination
  ) {
    throw new Error('Choose a destination before mapping.');
  }

  const action = remap ? 'remap' : 'map';
  const body = {
    job_id: jobId,
    destination_name: destinationNameForMap,
  };
  if (destTarget) {
    body.destination_entity = destTarget;
  }
  const actionResponse = await api.post(
    `/conversations/${conversationId}/actions/${action}`,
    body,
    { signal },
  );
  const data = actionResponse.data || {};
  const mapping = data.mapping || {};
  const mappingConfig = data.mapping_config || mapping.mapping || {};
  const mappingId = data.mapping_id || `mapping-${Date.now()}`;
  const transformationRules = getMappingTransformationFromMapResponse(mapping);
  const objectFieldMap =
    mapping.available_objects || mapping.available_sheets || mapping.available_tables || {};
  const destinationConnectorId =
    destinationForMap === 'sql'
      ? mapping.connector_id
      : mapping.token_id || mapping.connector_id || mapping.destination_connector_id;

  return {
    mappingId,
    mapping,
    mappingConfig,
    transformationRules,
    objectFieldMap,
    sourceDocumentType: mapping.source_document_type || previewData.documentType,
    sourceDocumentDescription:
      mapping.source_document_description || previewData.documentDescription,
    destinationDocumentType:
      mapping.destination_document_type || mapping.document_type || previewData.documentType,
    destinationDocumentDescription:
      mapping.destination_document_description ||
      mapping.document_description ||
      previewData.documentDescription,
    destinationObjectApiName: mapping.destination_object_api_name || null,
    destinationTableName: mapping.destination_table_name || null,
    destinationSheetName: mapping.destination_sheet_name || null,
    destinationTarget: destTarget || null,
    classificationSnapshot: {},
    mappingPlan: mapping.mapping_plan || null,
    destinationForMap,
    destinationNameForMap,
    firstRecordForMapping: sortedFirstRecord || pickMostCompleteRecord(previewData.data || []) || {},
    allRecordsForMapping: sortedAllRecords || previewData.data || [],
    sourceConnectorId:
      previewData.connectorId || extractionCompletedEvent?.metadata?.connector_id || null,
    sqlQuery: previewData.sqlQuery || extractionCompletedEvent?.metadata?.sql_query || null,
    destinationConnectorId,
    destinationSpreadsheetId: mapping.spreadsheet_id || mapping.destination_spreadsheet_id,
    orgRulesDisplayByObject: mapping.org_rules_display_by_object || {},
    extractionCompletedEvent,
    previewData,
    primaryTable: mapping.primary_table || null,
    backendMessageId: data.message_id || null,
    mappingVersion: data.mapping_version,
  };
}

/** Lazy-load mapping panel when mapping_started metadata has no mapping_config (old threads). */
export async function refetchMappingViaActionApi({
  conversationId,
  jobId,
  metadata,
  signal,
}) {
  if (!conversationId || !jobId) {
    throw new Error('Conversation and job_id are required to load mapping.');
  }
  const actionResponse = await api.post(
    `/conversations/${conversationId}/actions/map`,
    {
      job_id: jobId,
      destination_name: metadata?.destination_name || null,
    },
    { signal },
  );
  const data = actionResponse.data || {};
  const mapping = data.mapping || {};
  return {
    mappingConfig: data.mapping_config || mapping.mapping || {},
    transformationRules: getMappingTransformationFromMapResponse(mapping),
    objectFieldMap:
      mapping.available_objects || mapping.available_sheets || mapping.available_tables || {},
    orgRulesDisplayByObject: mapping.org_rules_display_by_object || {},
    destinationConnectorId:
      mapping.token_id || mapping.connector_id || mapping.destination_connector_id || null,
    destinationSpreadsheetId: mapping.spreadsheet_id || mapping.destination_spreadsheet_id || null,
    availableSheets: mapping.available_sheets || null,
    availableTables: mapping.available_tables || null,
    primaryTable: mapping.primary_table || metadata?.primary_table || null,
  };
}
