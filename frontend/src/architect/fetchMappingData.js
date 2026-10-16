import api from '../api';
import { getMappingTransformation } from '../utils/migrationRulesKeys';
import { pickMostCompleteRecord } from '../utils/pickMostCompleteRecord';
import { refetchMappingViaActionApi } from './runMapFieldsForJob';
import { syntheticSourceRowFromMappingConfig } from './sourcePreviewRow';

export async function fetchMappingData(deps, mappingId, entryId, metadata) {
  const {
    activeConversationId,
    mappingPanels,
    setChatHistory,
    setMappingPanels,
  } = deps;

  // Check if we already have this mapping data
  if (mappingPanels[mappingId]) {
    return mappingPanels[mappingId];
  }

  const hasStoredMapping =
    metadata?.mapping_config &&
    typeof metadata.mapping_config === 'object' &&
    Object.keys(metadata.mapping_config).length > 0;

  // Set loading state for this entry
  setChatHistory((prev) =>
    prev.map((entry) =>
      entry.id === entryId && (entry.mappingId === mappingId || entry.metadata?.mapping_id === mappingId)
        ? { ...entry, isLoading: true, isUnavailable: false }
        : entry
    )
  );

  try {
    const jobId = metadata.job_id;
    if (!jobId) {
      throw new Error('Job ID not found');
    }

    // Get conversationId from metadata or use activeConversationId
    const conversationId = metadata.conversation_id || activeConversationId;

    let extractionPayload = null;
    try {
      const extractionResponse = await api.get(`/extraction-results/${jobId}`);
      extractionPayload = extractionResponse.data?.result || extractionResponse.data;
    } catch (err) {
      if (err.response?.status === 404) {
        extractionPayload = null;
      } else {
        throw err;
      }
    }

    let extractionRows = Array.isArray(extractionPayload?.extracted_data)
      ? extractionPayload.extracted_data
      : extractionPayload?.extracted_data != null
        ? [extractionPayload.extracted_data]
        : [];

    let detailExpired = !!(
      extractionPayload &&
      (extractionPayload.detail_expired === true || extractionPayload.detail_available === false)
    );

    if (!extractionPayload && metadata.mapping_config) {
      detailExpired = true;
      extractionRows = [syntheticSourceRowFromMappingConfig(metadata.mapping_config)];
    } else if (extractionRows.length === 0 && metadata.mapping_config) {
      extractionRows = [syntheticSourceRowFromMappingConfig(metadata.mapping_config)];
    } else if (extractionRows.length === 0 && detailExpired) {
      extractionRows = [{}];
    } else if (!extractionRows || extractionRows.length === 0) {
      console.error('❌ No extraction data found for job_id:', jobId, 'payload:', extractionPayload);
      throw new Error(`No extraction data found for job ${jobId}`);
    }

    const recordCountForPanel =
      extractionPayload?.record_count ??
      metadata.record_count ??
      extractionRows.length;
    const hadPurgedRows =
      detailExpired &&
      (extractionPayload?.record_count ?? metadata.record_count ?? 0) > 0;
    const firstRecord =
      hadPurgedRows && metadata.mapping_config
        ? syntheticSourceRowFromMappingConfig(metadata.mapping_config)
        : pickMostCompleteRecord(extractionRows);
    const allRecords = hadPurgedRows ? [] : extractionRows;

    // Get mapping from mapping_started event metadata (always has the latest mapping)
    let restoredMapping = metadata.mapping_config;
    let restoredTransformationRules = getMappingTransformation(metadata) || { fields: [] };
    let objectFieldMap = {};
    let orgRulesDisplayByObject = metadata.org_rules_display_by_object || {};
    let destinationConnectorId = null;
    let destinationSpreadsheetId = null;
    let availableSheets = null;
    let availableTables = null;
    let panelPrimaryTable = metadata.primary_table || null;

    if (hasStoredMapping) {
      // Use stored mapping from mapping_started / panel edits — do not re-run /map-fields
      restoredMapping = metadata.mapping_config;
      restoredTransformationRules = getMappingTransformation(metadata) || { fields: [] };
      objectFieldMap = metadata.object_field_map || {};
      orgRulesDisplayByObject = metadata.org_rules_display_by_object || {};
      destinationConnectorId = metadata.destination_connector_id || null;
      destinationSpreadsheetId = metadata.destination_spreadsheet_id || null;
      availableSheets = metadata.available_sheets || null;
      availableTables = metadata.available_tables || null;
    } else if (conversationId) {
      const refetched = await refetchMappingViaActionApi({
        conversationId,
        jobId,
        metadata,
      });
      restoredMapping = refetched.mappingConfig;
      restoredTransformationRules = refetched.transformationRules;
      objectFieldMap = refetched.objectFieldMap;
      orgRulesDisplayByObject = refetched.orgRulesDisplayByObject;
      destinationConnectorId = refetched.destinationConnectorId;
      destinationSpreadsheetId = refetched.destinationSpreadsheetId;
      availableSheets = refetched.availableSheets;
      availableTables = refetched.availableTables;
      panelPrimaryTable = refetched.primaryTable;
    } else {
      throw new Error('Mapping is not available without a conversation context.');
    }

    // Store mapping panel data
    const mappingData = {
      firstRecord,
      allRecords,
      totalRecordCount: recordCountForPanel,
      detailExpired: hadPurgedRows || detailExpired,
      mapping: restoredMapping,
      transformationRules: restoredTransformationRules,
      objectFieldMap: objectFieldMap,
      orgRulesDisplayByObject,
      documentType: metadata.document_type,
      documentDescription: metadata.document_description,
      destination: metadata.destination || 'salesforce',
      destinationName: metadata.destination_name,
      primaryTable: panelPrimaryTable,
      conversationId: conversationId,
      // Destination-specific metadata
      destinationConnectorId: destinationConnectorId,
      destinationSpreadsheetId: destinationSpreadsheetId,
      availableSheets: availableSheets,
      availableTables: availableTables,
      // Restore missing field preference from metadata
      missingFieldPreference: metadata.missing_field_preference || {
        option: 'skip',
        placeholderValue: 'Unknown',
      },
    };

    setMappingPanels(prev => {
      return {
        ...prev,
        [mappingId]: mappingData
      };
    });

    // Clear loading state
    setChatHistory((prev) =>
      prev.map((entry) =>
        entry.id === entryId && (entry.mappingId === mappingId || entry.metadata?.mapping_id === mappingId)
          ? { ...entry, isLoading: false, isUnavailable: false }
          : entry
      )
    );

    return mappingData;
  } catch (error) {
    console.error('❌ Failed to fetch mapping data:', mappingId, error);

    // Set unavailable state
    setChatHistory((prev) =>
      prev.map((entry) =>
        entry.id === entryId && (entry.mappingId === mappingId || entry.metadata?.mapping_id === mappingId)
          ? { ...entry, isLoading: false, isUnavailable: true }
          : entry
      )
    );

    return null;
  }
}
