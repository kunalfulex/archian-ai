import { applyMappingStartedToChat } from './applyMappingStartedToChat';
import { buildSourcePreviewRow } from './sourcePreviewRow';
import { createEmptyOperationPreview } from './reduceOperationPreview';
import { ensureExtractionCompletedChatEntry } from './syncExtractionJobContext';
import { getMappingTransformationFromMapResponse } from '../utils/migrationRulesKeys';
import { metadataMatchesJobId } from './metadataJobId';
import { normalizeMappingConfig } from './mappingContextUtils';
import { resolveExtractionCompletedEvent, resolveSourceSqlQuery } from './resolveExtractionContextForSave';
import { resolveMapFieldsSseContext } from './resolveMapFieldsSsePayload';
import { syncMappingFlagsFromTransformationRules } from '../utils/mappingTransformEdit';

export async function handleAgentMapFieldsReady(deps, payload) {
  const {
    activeConversationId,
    chatHistoryRef,
    inlineDataPreviewsRef,
    mappingPanelsRef,
    persistMessage,
    setAgentTurnSteps,
    setChatHistory,
    setIsLoading,
    setMappingPanels,
    setOperationPreview,
    stopLoadingMessages,
  } = deps;

  const jobId = payload.job_id;
  if (!jobId) return;
  try {
    const extractionCompletedEvent = resolveExtractionCompletedEvent(
      chatHistoryRef.current,
      jobId,
    );
    const previewForJob = inlineDataPreviewsRef.current[jobId];
    const recordCount =
      payload.record_count ??
      payload.total_records ??
      extractionCompletedEvent?.metadata?.record_count ??
      previewForJob?._totalCount ??
      previewForJob?.recordCount ??
      0;
    const mappingConfig =
      normalizeMappingConfig(payload.mapping_config) ||
      normalizeMappingConfig(payload.mapping?.mapping) ||
      normalizeMappingConfig(payload.mapping) ||
      {};
    const previewSample = previewForJob?.data?.[0] || null;
    const sourceFields =
      payload.source_fields ||
      payload.mapping?.source_fields ||
      (previewSample ? Object.keys(previewSample) : null);
    const firstRecordForMapping = buildSourcePreviewRow({
      sourceFields,
      previewRecord: previewSample,
      mappingConfig,
    });
    const sseCtx = resolveMapFieldsSseContext(payload);
    ensureExtractionCompletedChatEntry({
      jobId,
      recordCount,
      metadata: {
        source_type: payload.source_type || extractionCompletedEvent?.metadata?.source_type,
        source_name: payload.source_name || extractionCompletedEvent?.metadata?.source_name,
        connector_id:
          payload.source_connector_id ||
          payload.connector_id ||
          extractionCompletedEvent?.metadata?.connector_id,
        sql_query: payload.sql_query || extractionCompletedEvent?.metadata?.sql_query,
        table_name: payload.table_name || extractionCompletedEvent?.metadata?.table_name,
        object_api_name:
          payload.object_api_name || extractionCompletedEvent?.metadata?.object_api_name,
        destination: payload.destination || extractionCompletedEvent?.metadata?.destination,
        destination_name:
          payload.destination_name || extractionCompletedEvent?.metadata?.destination_name,
        destination_spreadsheet_id:
          sseCtx.destinationSpreadsheetId ||
          extractionCompletedEvent?.metadata?.destination_spreadsheet_id,
        destination_connector_id:
          sseCtx.destinationConnectorId ||
          extractionCompletedEvent?.metadata?.destination_connector_id,
        available_sheets: sseCtx.availableSheets,
      },
      setChatHistory,
      metadataMatchesJobId,
    });
    const transformationRules = getMappingTransformationFromMapResponse(payload);
    const destForSync = String(payload.destination || 'salesforce').toLowerCase();
    const baseMapping =
      Object.keys(mappingConfig).length > 0 ? mappingConfig : payload.mapping || {};
    const syncedMapping = syncMappingFlagsFromTransformationRules(
      baseMapping,
      transformationRules,
      destForSync === 'gsheets' ? 'google_sheets' : destForSync,
    );
    const mapResult = {
      mappingId: payload.mapping_id || `mapping-${Date.now()}`,
      mapping: syncedMapping,
      mappingConfig: syncedMapping,
      transformationRules,
      objectFieldMap:
        payload.mapping?.available_objects ||
        payload.mapping?.available_sheets ||
        payload.mapping?.available_tables ||
        sseCtx.availableObjects ||
        sseCtx.availableSheets ||
        sseCtx.availableTables ||
        {},
      destinationForMap: payload.destination,
      destinationNameForMap: payload.destination_name,
      firstRecordForMapping,
      allRecordsForMapping: [],
      totalRecordCount: recordCount,
      previewData: previewForJob || {
        jobId,
        documentType: payload.document_type,
        documentDescription: payload.document_description,
        sourceType: payload.source_type,
        recordCount,
        _totalCount: recordCount,
        data: [],
      },
      sourceConnectorId:
        sseCtx.sourceConnectorId ||
        previewForJob?.connectorId ||
        extractionCompletedEvent?.metadata?.connector_id ||
        payload.source_connector_id ||
        null,
      sqlQuery: resolveSourceSqlQuery({
        chatHistory: chatHistoryRef.current,
        inlineDataPreviews: inlineDataPreviewsRef.current,
        sourceJobId: jobId,
        extractionCompletedEvent,
        previewData: previewForJob,
      }),
      destinationConnectorId:
        sseCtx.destinationConnectorId ||
        extractionCompletedEvent?.metadata?.destination_connector_id ||
        null,
      destinationSpreadsheetId: sseCtx.destinationSpreadsheetId,
      availableSheets: sseCtx.availableSheets,
      availableTables: sseCtx.availableTables,
      orgRulesDisplayByObject: sseCtx.orgRulesDisplayByObject,
      extractionCompletedEvent,
      primaryTable: sseCtx.primaryTable || payload.primary_table || payload.mapping?.primary_table,
      sourceDocumentType: payload.source_document_type,
      sourceDocumentDescription: payload.source_document_description,
      destinationDocumentType: payload.destination_document_type || payload.document_type,
      destinationDocumentDescription:
        payload.destination_document_description || payload.document_description,
      destinationObjectApiName: payload.destination_object_api_name,
      mappingPlan: payload.mapping_plan,
    };
    const applied = await applyMappingStartedToChat({
      jobId,
      mapResult,
      activeConversationId,
      chatHistory: chatHistoryRef.current,
      setChatHistory,
      setMappingPanels,
      persistMessage,
      mappingInstruction:
        payload.mapping_instruction ||
        chatHistoryRef.current
          .filter((e) => e.type === 'user' || e.role === 'user')
          .slice(-1)[0]?.text ||
        null,
    });
    if (applied?.mappingId && applied?.panelSnapshot) {
      mappingPanelsRef.current = {
        ...mappingPanelsRef.current,
        [applied.mappingId]: applied.panelSnapshot,
      };
    }
    setOperationPreview(createEmptyOperationPreview());
    setAgentTurnSteps([]);
    stopLoadingMessages();
    setIsLoading(false);
  } catch (err) {
    console.error('Agent map_fields apply failed:', err);
    stopLoadingMessages();
    setIsLoading(false);
  }
}
