import { metadataJobId } from './metadataJobId';
import {
  resolveExtractionCompletedEvent,
  resolveExtractionRules,
  resolveSourceSqlQuery,
  resolveExtractedSchemaType,
  resolveSourceTypeForSave,
  findBestMigrationCompletedEvent,
  pickDestinationConnectorIdForSave,
} from './resolveExtractionContextForSave';
import { findLatestMappingPanelForJob } from './mappingPanelHistory';
import { getMappingTransformation } from '../utils/migrationRulesKeys';

// Cache for migration status props computation (prevents re-execution on every render)
export const migrationStatusPropsCache = new Map();
const MAX_CACHE_SIZE = 100;

const KNOWN_MIGRATION_DESTINATION_TYPES = new Set(['salesforce', 'sql', 'google_sheets', 'gsheets']);

function normalizeMigrationDestination(destination, extractedSchemaType, sourceType) {
  const d = String(destination || '').toLowerCase();
  if (KNOWN_MIGRATION_DESTINATION_TYPES.has(d)) {
    return d === 'gsheets' ? 'google_sheets' : d;
  }
  const schema = String(extractedSchemaType || sourceType || '').toLowerCase();
  if (schema === 'salesforce') return 'salesforce';
  if (schema === 'sql') return 'sql';
  if (schema === 'gsheets' || schema === 'google_sheets') return 'google_sheets';
  return destination || 'salesforce';
}

// Track logged migration statuses to prevent duplicate logs
const loggedMigrationStatuses = new Map();

// Helper function to compute migration status props (memoized to prevent infinite re-execution)
export function computeMigrationStatusProps(entry, migrationStatuses, chatHistory, mappingPanels, inlineDataPreviews) {
  // Find migrationStatus first (needed for cache key)
  let foundMigrationStatus = migrationStatuses[entry.jobId];
  if (!foundMigrationStatus && entry.batchId) {
    foundMigrationStatus = Object.values(migrationStatuses).find(status => 
      status.batchId === entry.batchId || status.batchId === entry.jobId
    );
  }
  if (!foundMigrationStatus && entry.batchId) {
    foundMigrationStatus = Object.values(migrationStatuses).find(status => 
      status.batchId === entry.batchId
    );
  }
  
  if (!foundMigrationStatus) {
    const statusKey = 'null';
    const cacheKey = `${entry.jobId}-${entry.batchId || ''}-${statusKey}-no-flow`;
    migrationStatusPropsCache.set(cacheKey, null);
    return null;
  }
  
  // Create cache key based on entry, current migration status, and flowId
  // Include flowId in cache key so flow info changes invalidate cache
  const statusKey = `${foundMigrationStatus.status}-${foundMigrationStatus.progress}`;
  
  // Find migration_completed event early to get flowId for cache key
  const isMigrationInProgress = foundMigrationStatus.status === 'processing' || 
                                foundMigrationStatus.status === 'in_progress';
  const migrationCompletedEventForCacheKey = isMigrationInProgress
    ? null
    : findBestMigrationCompletedEvent(chatHistory, {
        jobId: entry.jobId,
        batchId: entry.batchId,
      });
  const flowIdForCacheKey = migrationCompletedEventForCacheKey?.metadata?.flow_id || null;
  
  // Include flowId in cache key so flow info changes invalidate cache
  const cacheKey = `${entry.jobId}-${entry.batchId || ''}-${statusKey}-${flowIdForCacheKey || 'no-flow'}`;
  
  // Check cache first
  if (migrationStatusPropsCache.has(cacheKey)) {
    const cached = migrationStatusPropsCache.get(cacheKey);
    // Log only when status actually changes
    const lastLoggedKey = loggedMigrationStatuses.get(entry.jobId);
    if (lastLoggedKey !== statusKey && foundMigrationStatus) {
      loggedMigrationStatuses.set(entry.jobId, statusKey);
    }
    return cached;
  }
  
  // Clear cache if it gets too large
  if (migrationStatusPropsCache.size > MAX_CACHE_SIZE) {
    const firstKey = migrationStatusPropsCache.keys().next().value;
    migrationStatusPropsCache.delete(firstKey);
  }
  
  // Log only when status actually changes
  const currentStatusKey = `${foundMigrationStatus.status}-${foundMigrationStatus.progress}`;
  const lastLoggedKey = loggedMigrationStatuses.get(entry.jobId);
  if (lastLoggedKey !== currentStatusKey) {
    loggedMigrationStatuses.set(entry.jobId, currentStatusKey);
  }
  
  // Perform all the expensive computations
  // Use the migration_completed event we already found for cache key
  const migrationCompletedEvent = migrationCompletedEventForCacheKey;
  
  const backendMigrationStartedEvent = chatHistory.find(e => 
    e.metadata?.event_type === 'migration_started' && 
    (e.metadata?.job_id === entry.jobId || e.metadata?.batch_id === entry.jobId || e.metadata?.batch_id === entry.batchId)
  );
  
  const recordsJobIdForMigration =
    backendMigrationStartedEvent?.metadata?.records_job_id ||
    migrationCompletedEvent?.metadata?.records_job_id ||
    null;

  let frontendMappingStartedEvent = null;
  const migrationSourceJobId =
    migrationCompletedEvent?.metadata?.source_job_id ||
    backendMigrationStartedEvent?.metadata?.source_job_id ||
    null;
  if (migrationSourceJobId) {
    frontendMappingStartedEvent = findLatestMappingPanelForJob(chatHistory, migrationSourceJobId);
  }

  const sourceJobIdForMetadata =
    migrationSourceJobId || frontendMappingStartedEvent?.metadata?.job_id || null;
  const extractionJobIdForMigration = sourceJobIdForMetadata || recordsJobIdForMigration;

  const extractionCompletedEvent = extractionJobIdForMigration
    ? resolveExtractionCompletedEvent(chatHistory, extractionJobIdForMigration)
    : null;

  if (!frontendMappingStartedEvent && extractionCompletedEvent) {
    const extractionJobId = metadataJobId(extractionCompletedEvent.metadata);
    if (extractionJobId) {
      frontendMappingStartedEvent = findLatestMappingPanelForJob(chatHistory, extractionJobId);
    }
  }
  
  const mappingId = frontendMappingStartedEvent?.metadata?.mapping_id || frontendMappingStartedEvent?.mappingId;
  const mappingPanelData = mappingId ? mappingPanels[mappingId] : null;
  const extractionJobIdForPreview =
    metadataJobId(extractionCompletedEvent?.metadata) ||
    extractionJobIdForMigration ||
    frontendMappingStartedEvent?.metadata?.job_id ||
    entry.jobId;
  const previewData =
    inlineDataPreviews[extractionJobIdForPreview] ||
    (extractionJobIdForMigration ? inlineDataPreviews[extractionJobIdForMigration] : null) ||
    inlineDataPreviews[entry.jobId];
  // Prioritize updated mapping from mappingPanels state over event metadata (which has the old mapping)
  const mapping = mappingPanelData?.mapping || frontendMappingStartedEvent?.metadata?.mapping_config || backendMigrationStartedEvent?.metadata?.mapping_config || null;
  const migrationInstanceURL = migrationCompletedEvent?.metadata?.instance_url || null;
  
  let destination = foundMigrationStatus?.destination || 
                     migrationCompletedEvent?.metadata?.destination ||
                     backendMigrationStartedEvent?.metadata?.destination ||
                     frontendMappingStartedEvent?.metadata?.destination ||
                     'salesforce';

  const sourceTypeForDest =
    migrationCompletedEvent?.metadata?.source_type ||
    backendMigrationStartedEvent?.metadata?.source_type ||
    extractionCompletedEvent?.metadata?.source_type ||
    previewData?.sourceType ||
    null;

  let extractedSchemaTypeEarly = migrationCompletedEvent?.metadata?.extracted_schema_type;
  if (!extractedSchemaTypeEarly) {
    if (sourceTypeForDest === 'sql') extractedSchemaTypeEarly = 'sql';
    else if (sourceTypeForDest === 'gsheets' || sourceTypeForDest === 'google_sheets') {
      extractedSchemaTypeEarly = 'gsheets';
    } else if (sourceTypeForDest === 'salesforce') extractedSchemaTypeEarly = 'salesforce';
    else extractedSchemaTypeEarly = sourceTypeForDest === 'file' ? 'file' : (sourceTypeForDest || 'file');
  }

  destination = normalizeMigrationDestination(
    destination,
    extractedSchemaTypeEarly,
    sourceTypeForDest,
  );

  const resolvedDestinationName =
    foundMigrationStatus?.destinationName ||
    migrationCompletedEvent?.metadata?.destination_name ||
    backendMigrationStartedEvent?.metadata?.destination_name ||
    frontendMappingStartedEvent?.metadata?.destination_name ||
    mappingPanelData?.destinationName ||
    extractionCompletedEvent?.metadata?.destination_name ||
    (destination === 'salesforce' ? extractionCompletedEvent?.metadata?.source_name : null) ||
    null;
  
  let destinationSpreadsheetId = (destination === 'google_sheets' && foundMigrationStatus?.spreadsheetId) ||
                               migrationCompletedEvent?.metadata?.spreadsheet_id || 
                               migrationCompletedEvent?.metadata?.destination_spreadsheet_id ||
                               (destination === 'google_sheets' ? migrationCompletedEvent?.metadata?.destination_connector_id : null);
  
  if (!destinationSpreadsheetId && destination === 'google_sheets') {
    destinationSpreadsheetId = backendMigrationStartedEvent?.metadata?.destination_spreadsheet_id ||
                             backendMigrationStartedEvent?.metadata?.destination_connector_id ||
                             frontendMappingStartedEvent?.metadata?.destination_spreadsheet_id ||
                             frontendMappingStartedEvent?.metadata?.destination_connector_id ||
                             mappingPanelData?.destinationSpreadsheetId ||
                             mappingPanelData?.destinationConnectorId ||
                             null;
  } else if (!destinationSpreadsheetId) {
    destinationSpreadsheetId = backendMigrationStartedEvent?.metadata?.destination_spreadsheet_id ||
                             frontendMappingStartedEvent?.metadata?.destination_spreadsheet_id ||
                             mappingPanelData?.destinationSpreadsheetId ||
                             null;
  }
  
  const destinationConnectorIdFromEvent = pickDestinationConnectorIdForSave({
    destination,
    migrationCompletedEvent,
    backendMigrationStartedEvent,
    frontendMappingStartedEvent,
    mappingPanelData,
    extractionCompletedEvent,
    previewData,
    jobStatusSpreadsheetId: foundMigrationStatus?.spreadsheetId,
  });
  
  let userPrompt = migrationCompletedEvent?.metadata?.user_prompt || 
                  extractionCompletedEvent?.metadata?.user_prompt || 
                  frontendMappingStartedEvent?.metadata?.user_prompt || 
                  backendMigrationStartedEvent?.metadata?.user_prompt || 
                  '';
  
  if (!userPrompt && extractionCompletedEvent) {
    const extractionTimestamp = extractionCompletedEvent.timestamp || extractionCompletedEvent.created_at;
    const userMessagesBeforeExtraction = chatHistory
      .filter(e => e.type === 'user' && (e.timestamp || e.created_at) < extractionTimestamp)
      .sort((a, b) => (b.timestamp || b.created_at) - (a.timestamp || a.created_at));
    
    if (userMessagesBeforeExtraction.length > 0) {
      userPrompt = userMessagesBeforeExtraction[0].text || '';
    }
  }
  
  if (!userPrompt) {
    const mappingTimestamp = frontendMappingStartedEvent?.timestamp || backendMigrationStartedEvent?.timestamp || Date.now();
    const userMessagesBeforeMapping = chatHistory
      .filter(e => e.type === 'user' && (e.timestamp || e.created_at) < mappingTimestamp)
      .sort((a, b) => (b.timestamp || b.created_at) - (a.timestamp || a.created_at));
    
    if (userMessagesBeforeMapping.length > 0) {
      userPrompt = userMessagesBeforeMapping[0].text || '';
    }
  }
  
  const firstMapping = mapping && Object.values(mapping)[0];
  const destinationTableName = firstMapping?.table || null;
  const destinationSheetName = firstMapping?.sheet || null;
  
  const fileKey = migrationCompletedEvent?.metadata?.file_key ||
                  previewData?.fileKey || 
                  mappingPanelData?.fileKey || 
                  frontendMappingStartedEvent?.metadata?.file_key || 
                  backendMigrationStartedEvent?.metadata?.file_key || 
                  null;
  const sourceType = resolveSourceTypeForSave({
    chatHistory,
    inlineDataPreviews,
    sourceJobId: sourceJobIdForMetadata,
    recordsJobId: recordsJobIdForMigration,
    destination,
    migrationCompletedEvent,
    migrationStartedEvent: backendMigrationStartedEvent,
    extractionCompletedEvent,
    frontendMappingStartedEvent,
    mappingPanelData,
    previewData,
  });

  const extractedSchemaType = resolveExtractedSchemaType({
    sourceType,
    destination,
    migrationCompletedEvent,
    migrationStartedEvent: backendMigrationStartedEvent,
    extractionCompletedEvent,
  });
  
  const sourceConnectorId = migrationCompletedEvent?.metadata?.connector_id ||
                           backendMigrationStartedEvent?.metadata?.connector_id ||
                           extractionCompletedEvent?.metadata?.connector_id ||
                           previewData?.connectorId || 
                           mappingPanelData?.connectorId || 
                           frontendMappingStartedEvent?.metadata?.connector_id || 
                           null;
  const sourceSqlQueryResolved = resolveSourceSqlQuery({
    chatHistory,
    inlineDataPreviews,
    sourceJobId: sourceJobIdForMetadata,
    recordsJobId: recordsJobIdForMigration,
    migrationCompletedEvent,
    backendMigrationStartedEvent,
    extractionCompletedEvent,
    frontendMappingStartedEvent,
    mappingPanelData,
    previewData,
  });
  const sourceObjectApiName =
    migrationCompletedEvent?.metadata?.object_api_name ||
    backendMigrationStartedEvent?.metadata?.object_api_name ||
    extractionCompletedEvent?.metadata?.object_api_name ||
    frontendMappingStartedEvent?.metadata?.object_api_name ||
    previewData?.objectName ||
    null;

  const sourceTableNameRaw =
    migrationCompletedEvent?.metadata?.table_name ||
    backendMigrationStartedEvent?.metadata?.table_name ||
    extractionCompletedEvent?.metadata?.table_name ||
    previewData?.tableName ||
    mappingPanelData?.tableName ||
    frontendMappingStartedEvent?.metadata?.table_name ||
    null;

  // Salesforce source target = object API name (never conflate with SQL destination table_name).
  const sourceTableName =
    extractedSchemaType === 'salesforce'
      ? (sourceObjectApiName || sourceTableNameRaw)
      : sourceTableNameRaw;

  const sourceName =
    migrationCompletedEvent?.metadata?.source_name ||
    backendMigrationStartedEvent?.metadata?.source_name ||
    extractionCompletedEvent?.metadata?.source_name ||
    previewData?.sourceName ||
    frontendMappingStartedEvent?.metadata?.source_name ||
    null;
  
  const flowId = migrationCompletedEvent?.metadata?.flow_id || null;
  const flowName = migrationCompletedEvent?.metadata?.flow_name || null;
  const migrationCompletedMessageId = migrationCompletedEvent?.id || null;
  
  // Get missing field preference from multiple sources
  // 1. Check frontend mapping_started event metadata
  // 2. Check mappingPanelData (current state)
  // 3. Check backend migration_started event metadata
  // 4. Check migration_completed event metadata
  // 5. Check for preference update messages in chat history
  let missingFieldPreference = frontendMappingStartedEvent?.metadata?.missing_field_preference || 
                               mappingPanelData?.missingFieldPreference ||
                               backendMigrationStartedEvent?.metadata?.missing_field_preference ||
                               migrationCompletedEvent?.metadata?.missing_field_preference ||
                               null;
  
  // Get transformation rules from multiple sources
  // 1. Check frontend mapping_started event metadata
  // 2. Check mappingPanelData (current state)
  // 3. Check backend migration_started event metadata
  // 4. Check migration_completed event metadata
  let transformationRules =
    getMappingTransformation(frontendMappingStartedEvent?.metadata) ||
    mappingPanelData?.transformationRules ||
    getMappingTransformation(backendMigrationStartedEvent?.metadata) ||
    getMappingTransformation(migrationCompletedEvent?.metadata) ||
    frontendMappingStartedEvent?.metadata?.transformation_rules ||
    backendMigrationStartedEvent?.metadata?.transformation_rules ||
    migrationCompletedEvent?.metadata?.transformation_rules ||
    null;
  
  // Extraction-stage rules (post-extract child jobs carry merged rules on highest extraction_version)
  let extractionRules = resolveExtractionRules(
    extractionCompletedEvent?.metadata,
    previewData,
  );

  // If still not found, try to find it from preference update messages
  if (!missingFieldPreference && chatHistory) {
    // First, try to find by mappingId if available
    const mappingId = frontendMappingStartedEvent?.metadata?.mapping_id || 
                     frontendMappingStartedEvent?.mappingId ||
                     mappingPanelData?.mappingId ||
                     null;
    
    let preferenceUpdateMessages = [];
    
    if (mappingId) {
      // Find preference update messages for this specific mapping
      preferenceUpdateMessages = chatHistory
        .filter(e => 
          e.metadata?.event_type === 'mapping_preference_updated' &&
          e.metadata?.mapping_id === mappingId &&
          e.metadata?.missing_field_preference
        )
        .sort((a, b) => (b.timestamp || b.created_at || 0) - (a.timestamp || a.created_at || 0));
    } else {
      // If no mappingId, find the most recent preference update message
      // This is a fallback when we can't match by mappingId
      preferenceUpdateMessages = chatHistory
        .filter(e => 
          e.metadata?.event_type === 'mapping_preference_updated' &&
          e.metadata?.missing_field_preference
        )
        .sort((a, b) => (b.timestamp || b.created_at || 0) - (a.timestamp || a.created_at || 0));
    }
    
    if (preferenceUpdateMessages.length > 0) {
      missingFieldPreference = preferenceUpdateMessages[0].metadata?.missing_field_preference;
    }
  }
  
  const result = {
    migrationStatus: {
      ...foundMigrationStatus,
      jobId: foundMigrationStatus.jobId || entry.jobId,
      batchId: foundMigrationStatus.batchId || entry.batchId,
      destination,
      destinationName: resolvedDestinationName,
    },
    mapping,
    migrationInstanceURL,
    destinationSpreadsheetId,
    firstRecord: migrationCompletedEvent?.metadata?.extracted_schema || mappingPanelData?.firstRecord || previewData?.data?.[0] || frontendMappingStartedEvent?.metadata?.extracted_schema || backendMigrationStartedEvent?.metadata?.extracted_schema || {},
    allRecords: mappingPanelData?.allRecords || previewData?.data || [],
    documentType: migrationCompletedEvent?.metadata?.document_type || mappingPanelData?.documentType || previewData?.documentType || frontendMappingStartedEvent?.metadata?.document_type || backendMigrationStartedEvent?.metadata?.document_type || 'Unknown',
    documentDescription: migrationCompletedEvent?.metadata?.document_description || mappingPanelData?.documentDescription || previewData?.documentDescription || frontendMappingStartedEvent?.metadata?.document_description || backendMigrationStartedEvent?.metadata?.document_description || 'Unknown',
    userPrompt,
    extractedSchemaType,
    sourceConnectorId,
    sourceSqlQuery: sourceSqlQueryResolved,
    sourceTableName,
    sourceName,
    destinationConnectorId: destinationConnectorIdFromEvent,
    destinationTableName,
    destinationSheetName,
    fileKey,
    flowId,
    flowName,
    migrationCompletedMessageId,
    missingFieldPreference,
    transformationRules,
    extractionRules,
    failureSummary: migrationCompletedEvent?.metadata?.failure_summary || null,
  };
  
  migrationStatusPropsCache.set(cacheKey, result);
  return result;
}
