import api from '../api';
import { metadataMatchesJobId } from './metadataJobId';
import { resolveRequiresDestinationForPreview } from '../utils/destinationTargetOptions';

export async function fetchExtractionData(deps, jobId, entryId) {
  const {
    PAGINATED_PAGE_SIZE,
    chatHistoryRef,
    inlineDataPreviews,
    setChatHistory,
    setInlineDataPreviews,
  } = deps;

  // Check if we already have this data
  if (inlineDataPreviews[jobId]) {
    return inlineDataPreviews[jobId];
  }

  // Set loading state for this entry
  setChatHistory((prev) =>
    prev.map((entry) =>
      entry.id === entryId && entry.jobId === jobId
        ? { ...entry, isLoading: true, isUnavailable: false }
        : entry
    )
  );

  try {
    // Use paginated API first (scalable for large datasets)
    let result;
    let extractedData;
    try {
      const paginatedResponse = await api.get(`/extraction-results/${jobId}/records`, {
        params: { page: 1, page_size: PAGINATED_PAGE_SIZE },
      });
      const paginated = paginatedResponse.data;
      if (paginated?.status === 'success' && paginated?.metadata) {
        const metadata = paginated.metadata;
        extractedData = paginated.records || [];
        const totalCount = paginated.total_count ?? metadata.record_count ?? extractedData.length;
        result = {
          ...metadata,
          job_id: metadata.job_id || jobId,
          extracted_data: extractedData,
          record_count: totalCount,
        };
        // If we have more records, we're in paginated mode (show first page only for now)
        if (totalCount > PAGINATED_PAGE_SIZE) {
          result._isPaginated = true;
          result._totalCount = totalCount;
          result._fullTotalCount = totalCount;
        }
      }
    } catch (paginatedErr) {
      // Fallback to full API if paginated fails (e.g. 404, legacy)
      result = null;
    }

    if (!result) {
      const response = await api.get(`/extraction-results/${jobId}`);
      result = response.data?.result;
      extractedData = result?.extracted_data;
    } else if (!extractedData) {
      extractedData = result.extracted_data;
    }

    if (!extractedData) extractedData = [];
    const detailExpiredMeta = !!(
      result &&
      (result.detail_expired === true || result.detail_available === false)
    );
    const recordedCount = result?.record_count ?? 0;
    const canShowPurgedSummary =
      detailExpiredMeta && recordedCount > 0 && extractedData.length === 0 && !result._isPaginated;

    if (!result || (!extractedData.length && !result._isPaginated && !canShowPurgedSummary)) {
      throw new Error('No data found');
    }
    // Find extraction_completed event metadata to get connector_id, sql_query, table_name
    // This works uniformly for all extraction types (Google Sheets, Salesforce, SQL, File)
    // Use ref to get latest chatHistory value
    const currentChatHistory = chatHistoryRef.current;
    const extractionCompletedEvent = currentChatHistory.find(e => 
      e.metadata?.event_type === 'extraction_completed' && 
      metadataMatchesJobId(e.metadata, jobId)
    );
    const previewEntry = currentChatHistory.find(
      (e) =>
        e.id === entryId &&
        (e.jobId === jobId ||
          metadataMatchesJobId(e.metadata, jobId) ||
          e.metadata?.transformed_job_id === jobId)
    );
    const entryMeta = previewEntry?.metadata || {};

    // Read metadata from extraction_completed event (single source of truth for all extraction types)
    const connectorId =
      extractionCompletedEvent?.metadata?.connector_id || entryMeta.connector_id || null;
    const sqlQuery =
      extractionCompletedEvent?.metadata?.sql_query || entryMeta.sql_query || null;
    const tableName =
      extractionCompletedEvent?.metadata?.table_name || entryMeta.table_name || null;
    const destination = extractionCompletedEvent?.metadata?.destination || entryMeta.destination || null;
    const destinationName =
      extractionCompletedEvent?.metadata?.destination_name || entryMeta.destination_name || null;
    const destinationObjectApiName =
      extractionCompletedEvent?.metadata?.destination_object_api_name ||
      entryMeta.destination_object_api_name ||
      null;
    const destinationTableName =
      extractionCompletedEvent?.metadata?.destination_table_name ||
      entryMeta.destination_table_name ||
      null;
    const destinationSheetName =
      extractionCompletedEvent?.metadata?.destination_sheet_name ||
      entryMeta.destination_sheet_name ||
      null;

    // Determine source-specific metadata
    const sourceType = result.source_type;
    let sourceName = result.source_name;
    let sourceIdentifier = result.source_identifier;
    let orgId = null;

    // For Salesforce, orgId is the same as connector_id from metadata
    if (sourceType === 'salesforce') {
      orgId = connectorId || sourceIdentifier;
    }

    // For file extractions, try to get file_key from job status
    // Destination and destination_name are now read from extraction_completed event metadata above
    let fileKey = null;

    const metaRequiresDestination =
      extractionCompletedEvent?.metadata?.requires_destination ??
      entryMeta.requires_destination;

    // Fallback: If destination not found in metadata, try job status (for backward compatibility)
    let finalDestination = destination;
    let finalDestinationName = destinationName;

    try {
      const jobStatus = await api.get(`/job-status/${jobId}`);
      if (jobStatus.data) {
        // Extract file info for file extractions
        if (sourceType === 'file') {
          if (jobStatus.data.file_key) {
            fileKey = jobStatus.data.file_key;
          }
        }
        // Avoid overwriting extract-only reloads where job store echoes source as destination
        if (!finalDestination && jobStatus.data.destination) {
          finalDestination = jobStatus.data.destination;
        }
        if (
          !finalDestinationName &&
          jobStatus.data.destination_name &&
          metaRequiresDestination !== true
        ) {
          finalDestinationName = jobStatus.data.destination_name;
        }
      }
    } catch (fetchError) {
      // Not critical, will fetch when needed for re-extraction
      // For SQL extractions, /job-status/ will fail with 404, which is expected
      console.warn('Could not fetch job status metadata:', fetchError);
    }

    // Store preview data
    // connectorId, sqlQuery, tableName, destination, destination_name are now read from extraction_completed event metadata
    // This works uniformly for all extraction types (Google Sheets, Salesforce, SQL, File)
    // Get mapping_context_summary from result or extraction_completed event metadata
    const mappingContextSummary = result.mapping_context_summary || 
                                  extractionCompletedEvent?.metadata?.mapping_context_summary || 
                                  entryMeta.mapping_context_summary ||
                                  null;

    // Get data_quality_report from result or extraction_completed event metadata
    const dataQualityReport = result.data_quality_report || 
                              extractionCompletedEvent?.metadata?.data_quality_report || 
                              entryMeta.data_quality_report ||
                              null;

    const detailDownloadOff = (m) =>
      !!(m && typeof m === 'object' && (m.detail_expired === true || m.detail_available === false));

    const pipelineStepsRestore =
      extractionCompletedEvent?.metadata?.pipeline_steps ||
      extractionCompletedEvent?.pipelineSteps ||
      entryMeta.pipeline_steps ||
      previewEntry?.pipelineSteps ||
      null;
    const pipelineNarrativeRestore =
      (typeof extractionCompletedEvent?.metadata?.pipeline_narrative === 'string' &&
        extractionCompletedEvent.metadata.pipeline_narrative.trim()) ||
      (typeof extractionCompletedEvent?.pipelineNarrative === 'string' &&
        extractionCompletedEvent.pipelineNarrative.trim()) ||
      (typeof entryMeta.pipeline_narrative === 'string' && entryMeta.pipeline_narrative.trim()) ||
      (typeof previewEntry?.pipelineNarrative === 'string' && previewEntry.pipelineNarrative.trim()) ||
      null;

    const previewSourceName =
      sourceName ||
      extractionCompletedEvent?.metadata?.source_name ||
      entryMeta.source_name;
    const needsDestinationPick = resolveRequiresDestinationForPreview({
      destination: finalDestination,
      destinationName: finalDestinationName,
      sourceName: previewSourceName,
      requiresDestinationFlag:
        extractionCompletedEvent?.metadata?.requires_destination ??
        entryMeta.requires_destination,
    });

    const previewData = {
      jobId: result.job_id || jobId,
      data: extractedData,
      documentType: result.document_type,
      documentDescription: result.document_description,
      recordCount: result.record_count,
      sourceType: sourceType,
      sourceName: sourceName,
      connectorId: connectorId, // From extraction_completed event metadata
      sqlQuery: sqlQuery, // From extraction_completed event metadata
      tableName: tableName, // From extraction_completed event metadata
      orgId: orgId,
      destination: needsDestinationPick ? null : finalDestination,
      destinationName: needsDestinationPick ? null : finalDestinationName,
      destination_object_api_name: destinationObjectApiName,
      destination_table_name: destinationTableName,
      destination_sheet_name: destinationSheetName,
      requiresDestination: needsDestinationPick,
      mappingContextSummary: mappingContextSummary, // Add mapping_context_summary
      mapping_context_summary: mappingContextSummary, // Also add with underscore for compatibility
      dataQualityReport: dataQualityReport, // Add data_quality_report
      data_quality_report: dataQualityReport, // Also add with underscore for compatibility
      pipelineSteps: Array.isArray(pipelineStepsRestore) ? pipelineStepsRestore : null,
      pipelineNarrative: pipelineNarrativeRestore,
      // Store file info for re-extraction
      fileKey: fileKey,
      // Paginated mode: only first page loaded
      _isPaginated: result._isPaginated,
      _totalCount: result._totalCount,
      _downloadDisabled: detailDownloadOff(result),
    };

    setInlineDataPreviews((prev) => ({
      ...prev,
      [jobId]: previewData,
    }));

    // Clear loading state
    setChatHistory((prev) =>
      prev.map((entry) =>
        entry.id === entryId && entry.jobId === jobId
          ? { ...entry, isLoading: false, isUnavailable: false }
          : entry
      )
    );

    return previewData;
  } catch (error) {
    console.error('Failed to fetch extraction data:', error);

    // Set unavailable state
    setChatHistory((prev) =>
      prev.map((entry) =>
        entry.id === entryId && entry.jobId === jobId
          ? { ...entry, isLoading: false, isUnavailable: true }
          : entry
      )
    );

    return null;
  }
}
