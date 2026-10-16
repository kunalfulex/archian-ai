/**
 * Start migration from mapping panel state (Map button path + agent "start migration").
 * Chat flows use POST /conversations/{id}/actions/migrate only (SSE + server merge).
 */
import { runMigrationStream, createDefaultRecoveryStreamHandlers } from './runMigrationStream';
import { syncMigrationTerminalFromServer } from './applyMigrationStreamToChat';
import {
  resolveExtractionCompletedEvent,
} from './resolveExtractionContextForSave';
import { metadataJobId } from './metadataJobId';
import { resolveTransformedJobForMigration } from './resolveTransformedJobForMigration';

/** Split/combine rules safe to send at migrate without re-applying full map-time transforms. */
export function selectSplitCombineTransformationRules(transformationRules) {
  if (!transformationRules || typeof transformationRules !== 'object') return null;
  const fields = Array.isArray(transformationRules.fields) ? transformationRules.fields : [];
  const splitCombine = fields.filter((rule) => {
    const action = (rule?.action || '').toLowerCase();
    return action === 'combine' || action.startsWith('split');
  });
  return splitCombine.length ? { fields: splitCombine } : null;
}

export function selectMigrationTransformationRules({
  destination,
  panelTransformRules,
  isPreviewTransformed,
}) {
  const hasPanelTransform =
    panelTransformRules &&
    typeof panelTransformRules === 'object' &&
    Array.isArray(panelTransformRules.fields) &&
    panelTransformRules.fields.length > 0;

  if (isPreviewTransformed) {
    return hasPanelTransform ? panelTransformRules : null;
  }

  const dest = (destination || '').toLowerCase();
  if (dest === 'google_sheets' || dest === 'sql') {
    return hasPanelTransform ? panelTransformRules : null;
  }

  if (dest === 'salesforce') {
    return selectSplitCombineTransformationRules(panelTransformRules);
  }

  return hasPanelTransform ? panelTransformRules : null;
}

export function buildMigrationDataFromPanel({
  mappingData,
  entry,
  chatHistory,
  activeConversationId,
}) {
  const mappingId = entry?.mappingId || entry?.metadata?.mapping_id;
  const anchorJobId = entry?.metadata?.job_id || mappingData?.jobId;
  const latestExtraction = resolveExtractionCompletedEvent(chatHistory, anchorJobId);
  const recordsJobId = latestExtraction
    ? metadataJobId(latestExtraction.metadata)
    : anchorJobId;
  const previewTransformJobId = resolveTransformedJobForMigration(
    chatHistory,
    mappingId,
    anchorJobId,
  );
  const sourceJobIdForMigration = previewTransformJobId || recordsJobId;
  const isPreviewTransformed = !!previewTransformJobId;
  const missingFieldPreference =
    mappingData?.missingFieldPreference || entry?.metadata?.missing_field_preference || null;

  let migration_rules;
  if (
    missingFieldPreference?.option === 'placeholder' ||
    missingFieldPreference?.option === 'skip'
  ) {
    migration_rules = { missing_field_preference: missingFieldPreference };
  }

  const panelTransformRules = mappingData.transformationRules;
  const dest = mappingData.destination || entry?.metadata?.destination;
  const transformation_rules = selectMigrationTransformationRules({
    destination: dest,
    panelTransformRules,
    isPreviewTransformed,
  });

  return {
    mapping: mappingData.mapping,
    document_type: mappingData.documentType,
    document_description: mappingData.documentDescription,
    destination: mappingData.destination,
    destination_name: mappingData.destinationName,
    conversation_id: activeConversationId,
    transformation_rules,
    job_id: sourceJobIdForMigration,
    anchor_job_id: anchorJobId,
    records_job_id: recordsJobId,
    is_transformed_data: isPreviewTransformed,
    transformed_job_id: previewTransformJobId,
    migration_rules,
    availableSheets: mappingData.availableSheets,
    availableTables: mappingData.availableTables,
    primaryTable: mappingData.primaryTable ?? null,
    destinationConnectorId: mappingData.destinationConnectorId,
    destinationSpreadsheetId: mappingData.destinationSpreadsheetId,
    totalRecordCount:
      mappingData.totalRecordCount ??
      mappingData.allRecords?.length ??
      entry?.metadata?.record_count,
  };
}

export async function executeChatMigration(deps, migrationData, entry) {
  const {
    activeConversationId,
    runMigrationStream: runStream = runMigrationStream,
    stopLoadingMessages,
    setIsLoading,
    setChatHistory,
    setMigrationStatuses,
  } = deps;

  const anchorExtractionJobId =
    entry?.metadata?.job_id || migrationData.anchor_job_id || migrationData.job_id;

  if (!activeConversationId || !anchorExtractionJobId) {
    throw new Error(
      'Cannot start migration: open a conversation with a completed extraction and mapping first.',
    );
  }

  const endpoint = `/conversations/${activeConversationId}/actions/migrate`;
  const payload = {
    job_id: anchorExtractionJobId,
    mapping_message_id: entry?.id,
  };

  const startServerSync = (jobId, batchId) => {
    if (!jobId) return;
    void syncMigrationTerminalFromServer({
      conversationId: activeConversationId,
      jobId,
      batchId,
      setChatHistory,
      setMigrationStatuses,
      plannerPendingRef: deps.plannerPendingRef,
      updateMessageMetadata: deps.updateMessageMetadata,
      clearMigrationStatusPropsCache: deps.clearMigrationStatusPropsCache,
    });
  };

  const recoveryHandlers = createDefaultRecoveryStreamHandlers(deps);

  try {
    await runStream({
      endpoint,
      payload,
      signal: deps.signal,
      onStarted: deps.onMigrationStarted,
      onProgress: deps.onMigrationProgress,
      onFinished: deps.onMigrationJobFinished,
      onCompletedChat: deps.onMigrationCompletedChat,
      onRecoveryAnalysisStarted: recoveryHandlers.onRecoveryAnalysisStarted,
      onRecoveryActivity: recoveryHandlers.onRecoveryActivity,
      onRecoveryComplete: recoveryHandlers.onRecoveryComplete,
      onRecoveryChat: (evt) => {
        recoveryHandlers.onRecoveryComplete();
        deps.onMigrationRecoveryChat?.(evt);
      },
      onJsonResponse: (data) => {
        if (data?.job_id) {
          deps.onMigrationStarted?.({
            event_type: 'migration_started',
            job_id: data.job_id,
            batch_id: data.batch_id,
            total_records: data.total_records,
            record_count: data.total_records,
          });
          startServerSync(data.job_id, data.batch_id);
        }
      },
    });
  } finally {
    stopLoadingMessages?.();
    setIsLoading?.(false);
  }
}

export async function handleMigrationStartError(deps, error) {
  const { activeConversationId, persistMessage, setChatHistory, stopLoadingMessages, setIsLoading, formatFastApiDetail } =
    deps;
  const rawDetail = error.response?.data?.detail;
  let errorMessage = formatFastApiDetail(rawDetail);
  if (!errorMessage && error.response?.data?.message) {
    errorMessage = String(error.response.data.message);
  }
  if (!errorMessage && error.message) {
    errorMessage = error.message;
  }
  if (!errorMessage) {
    errorMessage = 'Unknown error occurred';
  }

  const isRetentionBlock =
    (typeof rawDetail === 'string' &&
      (rawDetail.includes('data retention') || rawDetail.includes('Run extraction again'))) ||
    (typeof errorMessage === 'string' &&
      (errorMessage.includes('data retention') || errorMessage.includes('Run extraction again')));

  const errorChatMessage = isRetentionBlock
    ? `⚠️ **Cannot start migration**\n\n${errorMessage}`
    : `❌ **Migration failed**\n\n${errorMessage}`;

  const failureMetadata = {
    event_type: 'migration_failed',
    error: errorMessage,
    ...(isRetentionBlock ? { failure_reason: 'extraction_detail_expired' } : {}),
  };

  setChatHistory((prev) => [
    ...prev,
    {
      id: `error-migration-${Date.now()}`,
      type: 'system',
      text: errorChatMessage,
      timestamp: Date.now(),
      metadata: failureMetadata,
    },
  ]);

  if (activeConversationId) {
    await persistMessage(activeConversationId, 'assistant', errorChatMessage, failureMetadata);
  }
  stopLoadingMessages();
  setIsLoading(false);
}
