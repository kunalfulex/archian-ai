import { buildIntentStateFromExtraction } from './intentState';

/** Inline architect SSE payload → InlineDataPreview card (SQL extract, Sheets join, etc.). */
export function applyInlineDataPreviewFromStreamPayload(data, ctx) {
  const {
    setInlineDataPreviews,
    addMessage,
    persistMessage,
    setIsLoading,
    setAgentTurnSteps,
    clearOperationPreview,
    conversationId,
  } = ctx;
  if (!data?.data || !Array.isArray(data.data)) return false;

  const rawSource = (data.source_type || 'sql').toLowerCase();
  const sourceType = rawSource === 'google_sheets' ? 'gsheets' : rawSource;
  const previewId = data.job_id || `${sourceType}-${Date.now()}`;
  const dataQualityReport = data.data_quality_report || null;
  const sourceName = data.source_name || data.table_name || null;
  const canonicalSourceType = rawSource === 'google_sheets' ? 'google_sheets' : rawSource;
  const intentStateForMeta = buildIntentStateFromExtraction({
    jobId: previewId,
    sourceType: canonicalSourceType,
    sourceName,
    destination: data.destination || null,
    destinationName: data.destination_name || null,
  });

  setInlineDataPreviews((prev) => ({
    ...prev,
    [previewId]: {
      jobId: previewId,
      data: data.data,
      documentType: data.document_type,
      documentDescription: data.document_description,
      recordCount: data.total_records || data.data.length,
      sourceType,
      sourceName,
      tableName: data.table_name,
      query: data.query,
      destination: data.destination,
      destinationName: data.destination_name,
      dataQualityReport,
      data_quality_report: dataQualityReport,
      isTransformedData: sourceType === 'gsheets' && Boolean(data.join_key),
    },
  }));

  const qualitySummaryText = dataQualityReport?.summary_text || '';
  let previewMessage = data.preview_message;
  if (!previewMessage) {
    if (sourceType === 'gsheets') {
      previewMessage = `✅ **Google Sheets**\n\nShowing **${data.data.length}** row(s) from ${sourceName || 'spreadsheet'}.${qualitySummaryText ? `\n\n${qualitySummaryText}` : ''}`;
    } else {
      previewMessage = `✅ **SQL Extraction Completed**\n\nExtracted **${data.data.length}** records from ${data.table_name || 'database'}.${qualitySummaryText ? `\n\n${qualitySummaryText}` : ''}`;
    }
  }

  addMessage(previewMessage, {
    id: `data-preview-${previewId}`,
    type: 'data_preview',
    timestamp: Date.now(),
    jobId: previewId,
  });

  if (conversationId && persistMessage) {
    persistMessage(conversationId, 'assistant', previewMessage, {
      event_type: 'extraction_completed',
      job_id: previewId,
      source_type: canonicalSourceType,
      source_name: sourceName,
      destination: data.destination || null,
      destination_name: data.destination_name || null,
      record_count: data.total_records || data.data.length,
      intent_state: intentStateForMeta,
    });
  }

  setIsLoading(false);
  setAgentTurnSteps([]);
  clearOperationPreview?.();
  return true;
}
