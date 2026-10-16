import api from '../api';
import { buildInitialExtractionCompletedHeadline, buildPostExtractDatasetUpdatedHeadline, extractionFailedHeadline, extractionSourceLabel } from './extractionCompletedHeadline';
import { buildIntentStateFromExtraction } from './intentState';
import { createEmptyOperationPreview } from './reduceOperationPreview';
import { dataPreviewEntryId, metadataWithExtractionMessageId } from './extractionMessageId';
import { hasExistingExtractionPreview, isMigrationShapedExtractionFinish } from './extractionJobFinish';
import { hasPendingPostExtractPhases } from './operationPhaseRegistry';
import { metadataMatchesJobId } from './metadataJobId';
import { pipelineStepsForPreviewDisplay } from '../utils/dataOperationsDisplay';
import { resolveRequiresDestinationForPreview } from '../utils/destinationTargetOptions';
import { syncMappingPanelRecordCount } from './syncExtractionJobContext';

export async function applyTaskJobStatusToUi(deps, taskData, sourceType, conversationId) {
  const {
    PAGINATED_PAGE_SIZE,
    agentTurnStepsRef,
    chatHistoryRef,
    inlineDataPreviewsRef,
    operationPreviewRef,
    persistMessage,
    setAgentTurnSteps,
    setChatHistory,
    setInlineDataPreviews,
    setIsLoading,
    setMappingPanels,
    setOperationPreview,
    stopLoadingMessages,
    updateMessageMetadata,
  } = deps;

  const jobId = taskData.job_id;
  if (!jobId) return;

  if (isMigrationShapedExtractionFinish(taskData)) return;

  if (
    taskData.status === 'completed' &&
    hasExistingExtractionPreview(jobId, chatHistoryRef.current, inlineDataPreviewsRef.current)
  ) {
    stopLoadingMessages();
    setIsLoading(false);
    return;
  }

  const snapPipeline = () =>
    (agentTurnStepsRef.current || []).map((s) => ({
      tool_id: s.tool_id,
      label: s.label,
      detail: s.detail,
      status: s.status || 'done',
    }));

  if (taskData.status === 'completed' && taskData.result) {
    const result = taskData.result;
    const supersedeParentId = taskData.parent_job_id;
    let taskDisplayData = result.data;
    const taskRecordCount = result.total_records ?? result.record_count ?? (taskDisplayData?.length || 0);
    if (result.status === 'success' && taskRecordCount > 0 && (!taskDisplayData || !Array.isArray(taskDisplayData))) {
      try {
        const { fetchExtractionPreviewRecords } = await import('./fetchExtractionPreviewRecords');
        const loaded = await fetchExtractionPreviewRecords(jobId);
        if (loaded?.records) {
          taskDisplayData = loaded.records;
          if (loaded.totalCount != null) {
            result.total_records = loaded.totalCount;
            result.record_count = loaded.totalCount;
          }
        }
      } catch (fetchErr) {
        console.warn('Failed to fetch records from API (Redis slim):', fetchErr);
      }
    }
    const serverPipelineSteps =
      Array.isArray(result.pipeline_steps) && result.pipeline_steps.length > 0
        ? result.pipeline_steps
        : null;
    const persistedPipelineSteps = pipelineStepsForPreviewDisplay(
      serverPipelineSteps || snapPipeline(),
      result.operations_applied,
    );
    const persistedPipelineNarrative =
      typeof result.pipeline_narrative === 'string' && result.pipeline_narrative.trim()
        ? result.pipeline_narrative.trim()
        : null;
    const pipelinePersistExtra = {
      pipeline_steps: persistedPipelineSteps?.length ? persistedPipelineSteps : null,
      pipeline_narrative: persistedPipelineNarrative,
      operations_applied: result.operations_applied ?? null,
      parent_job_id: taskData.parent_job_id ?? null,
    };

    if (result.status === 'success' && taskDisplayData && Array.isArray(taskDisplayData)) {
      const taskTotalCount = Math.max(
        Number(result.total_records) || 0,
        Number(result.record_count) || 0,
        taskDisplayData.length,
      );
      let sourceName = null;
      if (sourceType === 'sql') {
        sourceName = result.table_name || taskData.connector_name || null;
      } else if (sourceType === 'gsheets') {
        sourceName = result.spreadsheet_name || taskData.spreadsheet_name || result.sheet_name || null;
      } else if (sourceType === 'salesforce') {
        sourceName =
          result.connection_name ||
          taskData.connection_name ||
          result.object_label ||
          result.object_api_name ||
          null;
      } else if (sourceType === 'file') {
        sourceName = result.file_name || result.document_type || taskData.connector_name || null;
      }

      let salesforceObjectApiName =
        sourceType === 'salesforce'
          ? result.object_api_name || result.object_name || result.table_name || null
          : null;
      let sourceTableForMeta =
        sourceType === 'salesforce'
          ? salesforceObjectApiName
          : result.table_name || result.sheet_name || result.object_label || result.object_api_name || null;
      let sourceConnectorForMeta =
        sourceType === 'salesforce'
          ? result.salesforce_token_id || taskData.salesforce_token_id || result.connector_id || null
          : result.connector_id || result.salesforce_token_id || null;

      const parentJobId = taskData.parent_job_id;
      const parentPreview = parentJobId ? inlineDataPreviewsRef.current[parentJobId] : null;
      if (parentPreview) {
        if (!sourceConnectorForMeta && parentPreview.connectorId) {
          sourceConnectorForMeta = parentPreview.connectorId;
        }
        if (!salesforceObjectApiName && parentPreview.objectName) {
          salesforceObjectApiName = parentPreview.objectName;
        }
        if (!sourceTableForMeta && parentPreview.tableName) {
          sourceTableForMeta = parentPreview.tableName;
        }
      }
      const parentCompleted = parentJobId
        ? chatHistoryRef.current.find(
            (e) =>
              e.metadata?.event_type === 'extraction_completed' &&
              metadataMatchesJobId(e.metadata, parentJobId),
          )
        : null;
      if (parentCompleted) {
        if (!sourceConnectorForMeta) {
          sourceConnectorForMeta = parentCompleted.metadata?.connector_id || sourceConnectorForMeta;
        }
        if (!salesforceObjectApiName && parentCompleted.metadata?.object_api_name) {
          salesforceObjectApiName = parentCompleted.metadata.object_api_name;
        }
        if (!sourceTableForMeta && parentCompleted.metadata?.table_name) {
          sourceTableForMeta = parentCompleted.metadata.table_name;
        }
      }
      let sourceSqlForMeta = result.query || result.soql || null;
      if (parentJobId && !sourceSqlForMeta) {
        sourceSqlForMeta =
          parentPreview?.sqlQuery ||
          parentPreview?.query ||
          parentCompleted?.metadata?.sql_query ||
          null;
      }

      const destinationForUi =
        taskData.destination || result.destination || parentPreview?.destination || null;
      const destinationNameForUi =
        taskData.destination_name ||
        result.destination_name ||
        parentPreview?.destinationName ||
        null;

      const dataQualityReport = result.data_quality_report || null;
      const extractionVersion = taskData.parent_job_id ? 2 : 1;
      const intentStateForMeta =
        result.intent_state ||
        buildIntentStateFromExtraction({
          jobId,
          sourceType,
          sourceName,
          destination: destinationForUi,
          destinationName: destinationNameForUi,
          extractionRules: result.extraction_rules,
          extractionVersion,
        });
      const destinationObjectApiNameForUi =
        taskData.destination_object_api_name ||
        result.destination_object_api_name ||
        intentStateForMeta?.destination_object_api_name ||
        parentPreview?.destination_object_api_name ||
        null;
      const destinationTableNameForUi =
        taskData.destination_table_name ||
        result.destination_table_name ||
        parentPreview?.destination_table_name ||
        null;
      const destinationSheetNameForUi =
        taskData.destination_sheet_name ||
        result.destination_sheet_name ||
        parentPreview?.destination_sheet_name ||
        null;

      const needsDestPickLive = resolveRequiresDestinationForPreview({
        destination: destinationForUi,
        destinationName: destinationNameForUi,
        sourceName,
        requiresDestinationFlag: result.requires_destination,
      });

      setInlineDataPreviews(prev => ({
        ...prev,
        [jobId]: {
          jobId: jobId,
          pipelineNarrative: persistedPipelineNarrative,
          data: taskDisplayData,
          documentType: result.document_type,
          documentDescription: result.document_description,
          recordCount: taskTotalCount,
          sourceType: sourceType,
          sourceName: sourceName,
          tableName: sourceTableForMeta,
          query: result.query || result.user_prompt || result.soql,
          destination: needsDestPickLive ? null : destinationForUi,
          destinationName: needsDestPickLive ? null : destinationNameForUi,
          destination_object_api_name: destinationObjectApiNameForUi,
          destination_table_name: destinationTableNameForUi,
          destination_sheet_name: destinationSheetNameForUi,
          requiresDestination: needsDestPickLive,
          connectorId: sourceConnectorForMeta,
          sqlQuery: sourceSqlForMeta,
          extractionRules: result.extraction_rules || null,
          objectName: salesforceObjectApiName || result.object_api_name || result.object_name,
          orgId: result.org_id,
          dataQualityReport: dataQualityReport,
          data_quality_report: dataQualityReport,
          isTransformedData:
            sourceType === 'gsheets' &&
            Boolean(result.join_key || result.join_left_sheet),
          _isPaginated: taskTotalCount > PAGINATED_PAGE_SIZE,
          _totalCount: taskTotalCount,
          _fullTotalCount: taskTotalCount,
        }
      }));

      syncMappingPanelRecordCount(
        jobId,
        { totalRecordCount: taskTotalCount, detailExpired: false },
        setMappingPanels,
      );

      const operationSummary =
        typeof result.operation_summary === 'string' ? result.operation_summary.trim() : '';
      const defaultPreviewMessage = supersedeParentId
        ? buildPostExtractDatasetUpdatedHeadline(sourceType, taskTotalCount, dataQualityReport)
        : buildInitialExtractionCompletedHeadline(sourceType, taskTotalCount, dataQualityReport, {
            limitationApplied: Boolean(result.limitation_applied),
            originalFileRecords:
              result.original_file_records ?? result.total_in_file ?? null,
          });
      const previewMessage = operationSummary || defaultPreviewMessage;
      setChatHistory((prev) => [...prev, {
        id: `data-preview-${jobId}`,
        type: 'data_preview',
        text: previewMessage,
        timestamp: Date.now(),
        jobId: jobId,
        pipelineSteps: persistedPipelineSteps,
        pipelineNarrative: persistedPipelineNarrative,
        metadata: metadataWithExtractionMessageId(
          {
            event_type: 'extraction_completed',
            job_id: jobId,
            source_type: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
            source_name: sourceName || null,
            record_count: taskTotalCount,
            limitation_applied: Boolean(result.limitation_applied) || undefined,
            original_file_records:
              result.original_file_records ?? result.total_in_file ?? undefined,
            extraction_version: extractionVersion,
            sql_query: result.query || result.soql || null,
            table_name: sourceTableForMeta,
            object_api_name: salesforceObjectApiName || result.object_api_name || result.object_name || null,
            connector_id: sourceConnectorForMeta,
            destination: needsDestPickLive ? null : destinationForUi,
            destination_name: needsDestPickLive ? null : destinationNameForUi,
            destination_object_api_name: destinationObjectApiNameForUi,
            destination_table_name: destinationTableNameForUi,
            destination_sheet_name: destinationSheetNameForUi,
            requires_destination: needsDestPickLive,
            intent_state: intentStateForMeta,
            extraction_rules: result.extraction_rules || null,
            mapping_context_summary: result.mapping_context_summary || null,
            data_quality_report: result.data_quality_report || null,
            ...pipelinePersistExtra,
          },
          result.extraction_message_id,
        ),
        extractionMetadata: {
          job_id: jobId,
          source_type: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
          source_name: sourceName || null,
          record_count: taskTotalCount,
          extraction_version: taskData.parent_job_id ? 2 : 1,
          created_at: new Date().toISOString(),
        },
        isLoading: false,
        isUnavailable: false,
      }]);
      setAgentTurnSteps((prev) =>
        prev.length ? prev.map((s) => ({ ...s, status: 'done', detail: s.detail || null })) : prev,
      );

      let sourceNameForEvent = null;
      if (sourceType === 'gsheets') {
        sourceNameForEvent = result.spreadsheet_name || result.table_name || null;
      } else if (sourceType === 'sql') {
        sourceNameForEvent = result.table_name || null;
      } else if (sourceType === 'salesforce') {
        sourceNameForEvent =
          result.connection_name ||
          taskData.connection_name ||
          result.object_label ||
          result.object_api_name ||
          null;
      } else {
        sourceNameForEvent = result.table_name || result.connection_name || result.object_label || result.object_api_name || null;
      }

      stopLoadingMessages();
      const hasMappingPanelForJob = chatHistoryRef.current.some(
        (e) =>
          e.type === 'mapping_panel' &&
          metadataMatchesJobId(e.metadata, jobId),
      );
      const keepArchitectPipeline =
        hasPendingPostExtractPhases(operationPreviewRef.current) &&
        !hasMappingPanelForJob;
      if (!keepArchitectPipeline) {
        setIsLoading(false);
        setOperationPreview(createEmptyOperationPreview());
        setAgentTurnSteps([]);
      } else {
        setIsLoading(true);
      }

      try {
        if (result.extraction_message_id) {
          setChatHistory((prev) =>
            prev.map((entry) =>
              entry.id === dataPreviewEntryId(jobId)
                ? {
                    ...entry,
                    metadata: metadataWithExtractionMessageId(
                      {
                        ...entry.metadata,
                        ...(result.intent_state ? { intent_state: result.intent_state } : {}),
                      },
                      result.extraction_message_id,
                    ),
                  }
                : entry,
            ),
          );

          await updateMessageMetadata(conversationId, result.extraction_message_id, {
            job_id: jobId,
            source_type: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
            source_name: sourceNameForEvent,
            sql_query: result.query || result.soql || null,
            table_name: sourceTableForMeta,
            object_api_name: salesforceObjectApiName || result.object_api_name || result.object_name || null,
            connector_id: sourceConnectorForMeta,
            destination: needsDestPickLive ? null : destinationForUi,
            destination_name: needsDestPickLive ? null : destinationNameForUi,
            destination_object_api_name: destinationObjectApiNameForUi,
            destination_table_name: destinationTableNameForUi,
            destination_sheet_name: destinationSheetNameForUi,
            requires_destination: needsDestPickLive,
            intent_state: intentStateForMeta,
            extraction_rules: result.extraction_rules || null,
            mapping_context_summary: result.mapping_context_summary || null,
            data_quality_report: result.data_quality_report || null,
            operation_summary: operationSummary || null,
            ...pipelinePersistExtra,
          });
        } else {
          const createdMessage = await persistMessage(conversationId, 'assistant', previewMessage, {
            event_type: 'extraction_completed',
            job_id: jobId,
            source_type: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
            source_name: sourceNameForEvent,
            sql_query: result.query || result.soql || null,
            table_name: sourceTableForMeta,
            object_api_name: salesforceObjectApiName || result.object_api_name || result.object_name || null,
            connector_id: sourceConnectorForMeta,
            intent_state: intentStateForMeta,
            destination: needsDestPickLive ? null : destinationForUi,
            destination_name: needsDestPickLive ? null : destinationNameForUi,
            requires_destination: needsDestPickLive,
            destination_object_api_name: destinationObjectApiNameForUi,
            destination_table_name: destinationTableNameForUi,
            destination_sheet_name: destinationSheetNameForUi,
            extraction_rules: result.extraction_rules || null,
            mapping_context_summary: result.mapping_context_summary || null,
            data_quality_report: result.data_quality_report || null,
            operation_summary: operationSummary || null,
            ...pipelinePersistExtra,
          });
          const persistedId = createdMessage?.id;
          if (persistedId) {
            setChatHistory((prev) =>
              prev.map((entry) =>
                entry.id === dataPreviewEntryId(jobId)
                  ? {
                      ...entry,
                      metadata: metadataWithExtractionMessageId(entry.metadata, persistedId),
                    }
                  : entry,
              ),
            );
          } else {
            console.warn(
              '⚠️ extraction_message_id not found in result; persisted assistant message for thread restore',
            );
          }
        }
      } catch (metaErr) {
        console.warn('Extraction preview shown; thread metadata sync failed:', metaErr);
      }

      try {
        const verifyResponse = await api.get(`/extraction-results/${jobId}`);
        if (!verifyResponse.data?.result) {
          console.warn(`⚠️ Extraction result not found in DB for job_id: ${jobId}. Backend should have saved it.`);
        }
      } catch (verifyError) {
        if (verifyError.response?.status !== 404) {
          console.warn(`⚠️ Could not verify extraction result in DB for job_id: ${jobId}:`, verifyError);
        }
      }
    } else if (result.status === 'success') {
      const taskTotalCount = result.total_records ?? result.record_count ?? (Array.isArray(taskDisplayData) ? taskDisplayData.length : 0);
      let sourceName = null;
      if (sourceType === 'sql') {
        sourceName = result.table_name || taskData.connector_name || null;
      } else if (sourceType === 'gsheets') {
        sourceName = result.spreadsheet_name || taskData.spreadsheet_name || result.sheet_name || null;
      } else if (sourceType === 'salesforce') {
        sourceName =
          result.connection_name ||
          taskData.connection_name ||
          result.object_label ||
          result.object_api_name ||
          null;
      } else if (sourceType === 'file') {
        sourceName = result.file_name || result.document_type || taskData.connector_name || null;
      }
      const sourceLabel = sourceType === 'gsheets' ? 'Google Sheets' : sourceType === 'salesforce' ? 'Salesforce' : sourceType === 'file' ? 'File' : 'SQL';
      const ops =
        Array.isArray(result.operations_applied) && result.operations_applied.length
          ? result.operations_applied.join(', ')
          : null;
      const opsPhrase = ops ? ` (${ops})` : '';
      const isDataOpsOnly =
        Array.isArray(result.operations_applied) && result.operations_applied.length > 0;
      const limitationOpts = {
        limitationApplied: Boolean(result.limitation_applied),
        originalFileRecords: result.original_file_records ?? result.total_in_file ?? null,
      };
      const previewMessage =
        taskTotalCount === 0
          ? `✅ **${sourceLabel} — data ops finished**${opsPhrase}\n\n**0 records** passed your filters and transforms, so there is nothing to preview or migrate. Filters are often too strict when generated automatically—for example requiring empty **Id** while also requiring valid **Email** removes every Salesforce row.\n\nTry describing filters differently or rerun extraction first.`
          : isDataOpsOnly
            ? `✅ **${sourceLabel} — data ops finished**${opsPhrase}\n\n**${taskTotalCount} record(s)** were saved but preview rows could not be loaded. Try refreshing or opening extraction history for job **${jobId}**.`
            : `${buildInitialExtractionCompletedHeadline(sourceType, taskTotalCount, null, limitationOpts)} Preview is still loading—if the table does not appear shortly, click **load data preview** or refresh.`;
      setChatHistory((prev) => [...prev, {
        id: `data-preview-${jobId}`,
        type: 'data_preview',
        text: previewMessage,
        timestamp: Date.now(),
        jobId: jobId,
        pipelineSteps: persistedPipelineSteps,
        pipelineNarrative: persistedPipelineNarrative,
        metadata: {
          event_type: 'extraction_completed',
          job_id: jobId,
          source_type: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
          source_name: sourceName || null,
          record_count: taskTotalCount,
          limitation_applied: Boolean(result.limitation_applied) || undefined,
          original_file_records:
            result.original_file_records ?? result.total_in_file ?? undefined,
          extraction_rules: result.extraction_rules || null,
          destination: taskData.destination || result.destination || null,
          destination_name: taskData.destination_name || result.destination_name || null,
          operations_applied: result.operations_applied || null,
          intent_state: buildIntentStateFromExtraction({
            jobId,
            sourceType: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
            sourceName,
            destination: taskData.destination || result.destination || null,
            destinationName: taskData.destination_name || result.destination_name || null,
            extractionRules: result.extraction_rules || null,
          }),
          ...pipelinePersistExtra,
        },
        isLoading: taskTotalCount > 0 && !isDataOpsOnly,
        isUnavailable: taskTotalCount === 0 || isDataOpsOnly,
      }]);
      if (taskTotalCount > 0 && !isDataOpsOnly) {
        try {
          const { fetchExtractionPreviewRecords } = await import('./fetchExtractionPreviewRecords');
          const loaded = await fetchExtractionPreviewRecords(jobId, { maxAttempts: 16 });
          if (loaded?.records?.length) {
            const total = loaded.totalCount ?? taskTotalCount;
            const retryMessage = buildInitialExtractionCompletedHeadline(
              sourceType,
              total,
              null,
              limitationOpts,
            );
            setInlineDataPreviews((prev) => ({
              ...prev,
              [jobId]: {
                jobId,
                data: loaded.records,
                recordCount: total,
                sourceType,
                documentType: result.document_type,
                documentDescription: result.document_description,
                destination: taskData.destination || result.destination || null,
                destinationName: taskData.destination_name || result.destination_name || null,
                _isPaginated: total > PAGINATED_PAGE_SIZE,
                _totalCount: total,
                _fullTotalCount: total,
              },
            }));
            setChatHistory((prev) =>
              prev.map((entry) =>
                entry.id === `data-preview-${jobId}`
                  ? {
                      ...entry,
                      text: retryMessage,
                      isLoading: false,
                      isUnavailable: false,
                      metadata: { ...entry.metadata, record_count: total },
                    }
                  : entry,
              ),
            );
            setAgentTurnSteps([]);
            stopLoadingMessages();
            setIsLoading(false);
            return;
          }
        } catch (retryErr) {
          console.warn('Deferred extraction preview load failed:', retryErr);
        }
        setChatHistory((prev) =>
          prev.map((entry) =>
            entry.id === `data-preview-${jobId}`
              ? { ...entry, isLoading: false, isUnavailable: true }
              : entry,
          ),
        );
      }
      setAgentTurnSteps([]);
      await persistMessage(conversationId, 'assistant', previewMessage, {
        event_type: 'extraction_completed',
        job_id: jobId,
        source_type: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
        record_count: taskTotalCount,
        extraction_rules: result.extraction_rules || null,
        destination: taskData.destination || result.destination || null,
        destination_name: taskData.destination_name || result.destination_name || null,
        operations_applied: result.operations_applied || null,
        intent_state: buildIntentStateFromExtraction({
          jobId,
          sourceType: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
          sourceName,
          destination: taskData.destination || result.destination || null,
          destinationName: taskData.destination_name || result.destination_name || null,
          extractionRules: result.extraction_rules || null,
        }),
        ...pipelinePersistExtra,
      });
      stopLoadingMessages();
      setIsLoading(false);
    }
    return;
  }

  if (taskData.status === 'failed') {
    if (taskData.error_code === 'google_reauth_required') {
      stopLoadingMessages();
      setIsLoading(false);
      setOperationPreview(createEmptyOperationPreview());
      setAgentTurnSteps([]);
      return;
    }
    const failedHeadline = extractionFailedHeadline(sourceType);
    const explanation = taskData.error_explanation || taskData.error || 'Unknown error occurred during extraction.';
    const solution = taskData.error_solution;
    const errorMessage = solution
      ? `❌ **${failedHeadline}**\n\n${explanation}\n\n**What you can do:**\n${solution}`
      : `❌ **${failedHeadline}**\n\n${explanation}`;
    setChatHistory((prev) => [...prev, {
      id: `error-${jobId}`,
      type: 'system',
      text: errorMessage,
      timestamp: Date.now(),
    }]);
    await persistMessage(conversationId, 'assistant', errorMessage);
    stopLoadingMessages();
    setIsLoading(false);
    setOperationPreview(createEmptyOperationPreview());
    setAgentTurnSteps([]);
    return;
  }

  if (taskData.status === 'cancelled') {
    const cancelledHeadline = extractionSourceLabel(sourceType)
      ? `${extractionSourceLabel(sourceType)} Extraction Cancelled`
      : 'Extraction Cancelled';
    const msg = `⚠️ **${cancelledHeadline}**\n\nThe extraction was stopped before completion.`;
    setChatHistory((prev) => [...prev, {
      id: `cancelled-${jobId}`,
      type: 'system',
      text: msg,
      timestamp: Date.now(),
    }]);
    await persistMessage(conversationId, 'assistant', msg);
    stopLoadingMessages();
    setIsLoading(false);
    setOperationPreview(createEmptyOperationPreview());
    setAgentTurnSteps([]);
  }
}
