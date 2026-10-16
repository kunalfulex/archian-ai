/**
 * Apply mapping_started panel to chat — each remap appends a new message (v1, v2, …).
 */
import { MAPPING_TRANSFORMATION_KEY } from '../utils/migrationRulesKeys';
import { nextMappingVersionForJob } from './mappingPanelHistory';

export async function applyMappingStartedToChat({
  jobId,
  mapResult,
  activeConversationId,
  chatHistory,
  setChatHistory,
  setMappingPanels,
  persistMessage,
  mappingVersion,
  mappingInstruction,
}) {
  const {
    mappingId: newMappingId,
    mappingConfig,
    transformationRules,
    objectFieldMap,
    destinationForMap,
    destinationNameForMap,
    firstRecordForMapping,
    allRecordsForMapping,
    previewData,
    sourceConnectorId,
    sqlQuery,
    destinationConnectorId,
    destinationSpreadsheetId,
    orgRulesDisplayByObject,
    extractionCompletedEvent,
    mapping,
    primaryTable,
  } = mapResult;

  const panelKey = newMappingId || `mapping-${Date.now()}`;
  const version =
    mappingVersion ?? (nextMappingVersionForJob(chatHistory, jobId) || 1);

  const extractionSourceName =
    previewData.sourceName || extractionCompletedEvent?.metadata?.source_name || null;
  const extractionObjectApiName =
    previewData.objectName ||
    extractionCompletedEvent?.metadata?.object_api_name ||
    (previewData.sourceType === 'salesforce' ? previewData.tableName : null) ||
    null;
  const extractionSourceTableName =
    previewData.sourceType === 'salesforce'
      ? extractionObjectApiName
      : previewData.tableName || extractionCompletedEvent?.metadata?.table_name || null;

  const sourceDocType =
    mapResult.sourceDocumentType ||
    previewData.documentType ||
    extractionCompletedEvent?.metadata?.source_document_type ||
    extractionCompletedEvent?.metadata?.document_type ||
    '';
  const sourceDocDesc =
    mapResult.sourceDocumentDescription ||
    previewData.documentDescription ||
    extractionCompletedEvent?.metadata?.source_document_description ||
    extractionCompletedEvent?.metadata?.document_description ||
    '';
  const destDocType =
    mapResult.destinationDocumentType ||
    mapResult.mapping?.destination_document_type ||
    mapResult.mapping?.document_type ||
    previewData.documentType ||
    '';
  const destDocDesc =
    mapResult.destinationDocumentDescription ||
    mapResult.mapping?.destination_document_description ||
    mapResult.mapping?.document_description ||
    previewData.documentDescription ||
    '';

  const panelSnapshot = {
    firstRecord: firstRecordForMapping,
    allRecords: allRecordsForMapping,
    totalRecordCount:
      mapResult.totalRecordCount ??
      previewData._totalCount ??
      previewData.recordCount ??
      allRecordsForMapping.length,
    mapping: mappingConfig,
    transformationRules,
    objectFieldMap,
    documentType: destDocType,
    documentDescription: destDocDesc,
    sourceDocumentType: sourceDocType,
    sourceDocumentDescription: sourceDocDesc,
    destinationDocumentType: destDocType,
    destinationDocumentDescription: destDocDesc,
    destinationObjectApiName: mapResult.destinationObjectApiName || null,
    destinationTableName: mapResult.destinationTableName || null,
    destinationSheetName: mapResult.destinationSheetName || null,
    destinationTarget: mapResult.destinationTarget || null,
    destination: destinationForMap,
    destinationName: destinationNameForMap,
    conversationId: activeConversationId,
    connectorId: sourceConnectorId,
    sqlQuery,
    tableName: extractionSourceTableName,
    sourceName: extractionSourceName,
    objectName: extractionObjectApiName,
    destinationConnectorId,
    destinationSpreadsheetId,
    availableSheets: mapping?.available_sheets,
    availableTables: mapping?.available_tables,
    orgRulesDisplayByObject,
    missingFieldPreference: {
      option: 'skip',
      placeholderValue: 'Unknown',
    },
    primaryTable: primaryTable || null,
    jobId,
  };

  setMappingPanels((prev) => ({
    ...prev,
    [panelKey]: panelSnapshot,
  }));

  const mappingMetadata = {
    event_type: 'mapping_started',
    mapping_id: panelKey,
    job_id: jobId,
    destination: destinationForMap,
    destination_name: destinationNameForMap,
    document_type: destDocType,
    document_description: destDocDesc,
    source_document_type: sourceDocType,
    source_document_description: sourceDocDesc,
    destination_document_type: destDocType,
    destination_document_description: destDocDesc,
    destination_object_api_name: mapResult.destinationObjectApiName || null,
    destination_table_name: mapResult.destinationTableName || null,
    destination_sheet_name: mapResult.destinationSheetName || null,
    mapping_plan: mapResult.mappingPlan || null,
    source_type: previewData.sourceType,
    source_name: extractionSourceName,
    connector_id: sourceConnectorId,
    destination_connector_id: destinationConnectorId,
    sql_query: sqlQuery,
    table_name: extractionSourceTableName,
    object_api_name: extractionObjectApiName,
    mapping_config: mappingConfig,
    [MAPPING_TRANSFORMATION_KEY]: transformationRules,
    object_field_map: objectFieldMap,
    org_rules_display_by_object: orgRulesDisplayByObject,
    destination_spreadsheet_id: destinationSpreadsheetId,
    available_sheets: mapping?.available_sheets,
    available_tables: mapping?.available_tables,
    primary_table: primaryTable || null,
    missing_field_preference: {
      option: 'skip',
      placeholderValue: 'Unknown',
    },
    mapping_version: version,
    mapping_instruction: mappingInstruction || null,
    record_count:
      mapResult.totalRecordCount ??
      previewData._totalCount ??
      previewData.recordCount ??
      null,
  };

  const destLabel = destinationNameForMap || destinationForMap || 'destination';
  const destTarget =
    mapResult.destinationTarget ||
    mapResult.destinationObjectApiName ||
    mapResult.destinationTableName ||
    mapResult.destinationSheetName;
  const destPhrase = destTarget ? `${destLabel} · ${destTarget}` : destLabel;
  const versionSuffix = version > 1 ? ` (v${version})` : '';
  const mappingMessage = `🗺️ **Field mapping updated**${versionSuffix}\n\nMap fields to ${destPhrase}.`;

  let messageId = mapResult.backendMessageId || panelKey;

  if (activeConversationId && persistMessage && !mapResult.backendMessageId) {
    const createdMessage = await persistMessage(
      activeConversationId,
      'assistant',
      mappingMessage,
      mappingMetadata,
    );
    if (createdMessage?.id) {
      messageId = createdMessage.id;
    }
  }

  setChatHistory((prev) => {
    const withOlderPanelsFrozen = prev.map((e) => {
      if (
        e.type === 'mapping_panel' &&
        e.metadata?.job_id === jobId &&
        e.canUpdate !== false
      ) {
        return { ...e, canUpdate: false };
      }
      return e;
    });
    const entry = {
      id: messageId,
      type: 'mapping_panel',
      text: mappingMessage,
      timestamp: Date.now(),
      mappingId: panelKey,
      metadata: mappingMetadata,
      canUpdate: true,
    };
    return [...withOlderPanelsFrozen, entry];
  });

  return { mappingId: panelKey, messageId, mappingMetadata, panelSnapshot };
}
