import { HIDDEN_CHAT_DATA_EVENT_TYPES } from './architectSse';
import api from '../api';
import { buildInitialExtractionCompletedHeadline, buildPostExtractDatasetUpdatedHeadline } from './extractionCompletedHeadline';
import { metadataJobId, metadataMatchesJobId } from './metadataJobId';
import { metadataWithExtractionMessageId } from './extractionMessageId';
import { migrationStatusPropsCache } from './computeMigrationStatusProps';
import { preparePlannerChatEntries, resolvePlannerPendingFromLoadedMessages, schedulePersistPlannerResolved } from './plannerState';
import { syncMigrationTerminalFromServer } from './applyMigrationStreamToChat';

export async function loadConversationMessages(deps, conversationId) {
  const {
    activeConversationId,
    activeConversationIdRef,
    appliedExtractionJobFinishIdsRef,
    chatHistoryLength,
    conversationLoadGenerationRef,
    isConversationLoading,
    mapServerMessageToEntry,
    plannerPendingRef,
    setAgentTurnSteps,
    setChatHistory,
    setConversationError,
    setIsConversationLoading,
    setMigrationStatuses,
    setStreamingText,
    updateMessageMetadata,
  } = deps;

  if (!conversationId) return;
  // Prevent multiple simultaneous calls for the same conversation
  if (isConversationLoading && activeConversationId === conversationId) return;
  // Prevent reloading if this conversation is already active and we have messages loaded
  // Only skip if we're not currently loading (to allow manual refresh if needed)
  if (activeConversationId === conversationId && chatHistoryLength > 0 && !isConversationLoading) {
    return;
  }
  const loadGeneration = conversationLoadGenerationRef.current + 1;
  conversationLoadGenerationRef.current = loadGeneration;
  const isStaleLoad = () =>
    loadGeneration !== conversationLoadGenerationRef.current ||
    conversationId !== activeConversationIdRef.current;

  setIsConversationLoading(true);
  setConversationError(null);
  setStreamingText('');
  setAgentTurnSteps([]);
  appliedExtractionJobFinishIdsRef.current.clear();

  try {
    const hydrateResponse = await api.get(
      `/chat/conversations/${conversationId}/hydrate`,
      {
        params: { message_limit: 50, extraction_limit: 50, slim: true },
      },
    );
    if (isStaleLoad()) return;

    const messages = hydrateResponse.data?.messages || [];
    const mappedMessages = preparePlannerChatEntries(
      messages.map(mapServerMessageToEntry).filter(
        (e) => e && !HIDDEN_CHAT_DATA_EVENT_TYPES.has(e.metadata?.event_type),
      ),
    );

    plannerPendingRef.current = resolvePlannerPendingFromLoadedMessages(mappedMessages);
    if (isStaleLoad()) return;
    setChatHistory(mappedMessages);
    setIsConversationLoading(false);

    // Merge extraction metadata (already fetched in parallel on the server)
    try {
      const extractionResults = hydrateResponse.data?.results || [];

      // First, collect all transformed_job_ids from transformation_completed events
      // This helps us filter out transformed data from extraction results
      const transformedJobIds = new Set();
      mappedMessages.forEach(msg => {
        if (msg.metadata?.event_type === 'transformation_completed' && msg.metadata?.transformed_job_id) {
          transformedJobIds.add(msg.metadata.transformed_job_id);
        }
      });

      // Split extraction results into transformed and regular results
      // Results with has_transformation_rules === true should show as transformation_preview
      const transformedResults = extractionResults.filter(result => 
        result.has_transformation_rules === true && 
        !transformedJobIds.has(result.job_id) // Exclude if already handled by transformation_completed event
      );

      const regularResults = extractionResults.filter(result => 
        result.has_transformation_rules !== true && 
        !transformedJobIds.has(result.job_id) // Exclude transformed data
      );

      // Create transformation_preview entries for results with has_transformation_rules === true
      const transformationPreviewEntries = transformedResults.map((result) => {
        const timestamp = result.created_at 
          ? (typeof result.created_at === 'string' ? new Date(result.created_at).getTime() : result.created_at)
          : Date.now();

        // Find the corresponding extraction_completed or transformation_completed chat message to get full metadata
        const extractionCompletedMessage = mappedMessages.find(msg => 
          (msg.metadata?.event_type === 'extraction_completed' || msg.metadata?.event_type === 'transformation_completed') && 
          (metadataMatchesJobId(msg.metadata, result.job_id) || msg.metadata?.transformed_job_id === result.job_id)
        );

        const fallbackMeta = {
          event_type: 'transformation_completed',
          job_id: result.job_id,
          transformed_job_id: result.job_id,
          source_type: result.source_type,
          source_name: result.source_name || null,
          record_count: result.record_count ?? result.total_records ?? null,
          document_type: result.document_type,
          document_description: result.document_description,
          extraction_rules: result.extraction_rules || null,
          mapping_context_summary: result.mapping_context_summary || null,
        };

        const metadata = extractionCompletedMessage?.metadata
          ? { ...extractionCompletedMessage.metadata }
          : { ...fallbackMeta };

        const fromResult =
          result.record_count ??
          result.total_records ??
          (Array.isArray(result.extracted_data) ? result.extracted_data.length : null);
        if (fromResult != null && fromResult !== '') {
          metadata.record_count = Number(fromResult) || 0;
        } else {
          metadata.record_count =
            metadata.record_count != null && metadata.record_count !== ''
              ? Number(metadata.record_count) || 0
              : 0;
        }

        // If extraction_rules exists in result but not in metadata, add it
        if (result.extraction_rules && !metadata.extraction_rules) {
          metadata.extraction_rules = result.extraction_rules;
        }
        // If mapping_context_summary exists in result but not in metadata, add it
        if (result.mapping_context_summary && !metadata.mapping_context_summary) {
          metadata.mapping_context_summary = result.mapping_context_summary;
        }
        // If data_quality_report exists in result but not in metadata, add it
        if (result.data_quality_report && !metadata.data_quality_report) {
          metadata.data_quality_report = result.data_quality_report;
        }
        if (result.parent_job_id && !metadata.parent_job_id) {
          metadata.parent_job_id = result.parent_job_id;
        }

        const dqr =
          extractionCompletedMessage?.metadata?.data_quality_report ||
          result.data_quality_report ||
          metadata.data_quality_report ||
          null;
        const postExtractHeadline =
          result.parent_job_id &&
          buildPostExtractDatasetUpdatedHeadline(
            result.source_type || metadata.source_type,
            metadata.record_count,
            dqr,
          );

        return {
          id: `transformation-preview-${result.job_id}`,
          type: 'transformation_preview',
          text: postExtractHeadline || '🔄 **Data Transformation Completed**',
          timestamp: timestamp,
          metadata: metadata,
          jobId: result.job_id, // Use job_id as transformed_job_id for fetching from extracted_results
          pipelineSteps: Array.isArray(extractionCompletedMessage?.metadata?.pipeline_steps)
            ? extractionCompletedMessage.metadata.pipeline_steps
            : null,
          pipelineNarrative:
            typeof extractionCompletedMessage?.metadata?.pipeline_narrative === 'string'
              ? extractionCompletedMessage.metadata.pipeline_narrative
              : null,
          isLoading: false,
          isUnavailable: false,
          // Store metadata for lazy loading
          extractionMetadata: result,
        };
      });

      // Add data_preview entries for each regular extraction (each version appears as separate message)
      // Use metadata from chat messages (extraction_completed events) to preserve connector_id, sql_query, table_name
      const dataPreviewEntries = regularResults.map((result) => {
        const timestamp = result.created_at 
          ? (typeof result.created_at === 'string' ? new Date(result.created_at).getTime() : result.created_at)
          : Date.now();

        // Find the corresponding extraction_completed chat message to get full metadata
        const extractionCompletedMessage = mappedMessages.find(msg => 
          msg.metadata?.event_type === 'extraction_completed' && 
          metadataMatchesJobId(msg.metadata, result.job_id)
        );

        // Use full metadata from chat message if available, otherwise create minimal metadata
        // Include extraction_rules from result if available (from API response)
        const metadata = metadataWithExtractionMessageId(
          extractionCompletedMessage?.metadata || {
            event_type: 'extraction_completed',
            job_id: result.job_id,
            source_type: result.source_type,
            source_name: result.source_name || null,
            record_count: result.record_count,
            extraction_version: result.extraction_version,
            extraction_rules: result.extraction_rules || null,
            mapping_context_summary: result.mapping_context_summary || null,
          },
          extractionCompletedMessage?.id,
        );

        // If extraction_rules exists in result but not in metadata, add it
        if (result.extraction_rules && !metadata.extraction_rules) {
          metadata.extraction_rules = result.extraction_rules;
        }
        // If mapping_context_summary exists in result but not in metadata, add it
        if (result.mapping_context_summary && !metadata.mapping_context_summary) {
          metadata.mapping_context_summary = result.mapping_context_summary;
        }
        // If data_quality_report exists in result but not in metadata, add it
        if (result.data_quality_report && !metadata.data_quality_report) {
          metadata.data_quality_report = result.data_quality_report;
        }
        if (result.parent_job_id && !metadata.parent_job_id) {
          metadata.parent_job_id = result.parent_job_id;
        }

        const dqr =
          extractionCompletedMessage?.metadata?.data_quality_report ||
          result.data_quality_report ||
          metadata.data_quality_report ||
          null;
        const opSummary =
          typeof metadata.operation_summary === 'string'
            ? metadata.operation_summary.trim()
            : '';
        const previewHeadline = opSummary
          ? opSummary
          : result.parent_job_id
            ? buildPostExtractDatasetUpdatedHeadline(
                result.source_type || metadata.source_type,
                metadata.record_count,
                dqr,
              )
            : buildInitialExtractionCompletedHeadline(
                result.source_type || metadata.source_type,
                metadata.record_count,
                dqr,
                {
                  limitationApplied:
                    Boolean(metadata.limitation_applied) || Boolean(result.limitation_applied),
                  originalFileRecords:
                    metadata.original_file_records ??
                    result.original_file_records ??
                    result.total_in_file ??
                    null,
                },
              );

        return {
          id: `data-preview-${result.job_id}`,
          type: 'data_preview',
          text: previewHeadline,
          timestamp: timestamp,
          metadata: metadata, // Use full metadata from chat message (includes connector_id, sql_query, table_name)
          jobId: result.job_id,
          pipelineSteps: Array.isArray(extractionCompletedMessage?.metadata?.pipeline_steps)
            ? extractionCompletedMessage.metadata.pipeline_steps
            : null,
          pipelineNarrative:
            typeof extractionCompletedMessage?.metadata?.pipeline_narrative === 'string'
              ? extractionCompletedMessage.metadata.pipeline_narrative
              : null,
          isLoading: false, // Will be set to true when user expands
          isUnavailable: false, // Will be set to true if fetch fails
          // Store metadata for lazy loading
          extractionMetadata: result,
        };
      });

      // Combine both preview entry types
      const previewEntries = [...dataPreviewEntries, ...transformationPreviewEntries];

      // Restore transformation_completed events from chat messages
      // These will show transformed data preview using transformed_job_id to fetch from extracted_results
      const transformationEntries = mappedMessages
        .filter(msg => msg.metadata?.event_type === 'transformation_completed')
        .map(msg => {
          const transformedJobId = msg.metadata?.transformed_job_id || msg.jobId;
          // Use msg.timestamp (already converted by mapServerMessageToEntry) or fallback to Date.now()
          const timestamp = msg.timestamp || Date.now();
          const meta = msg.metadata || {};
          const postExtractHeadline =
            meta.parent_job_id &&
            buildPostExtractDatasetUpdatedHeadline(
              meta.source_type,
              meta.record_count,
              meta.data_quality_report,
            );

          return {
            id: msg.id,
            type: 'transformation_preview',
            text: postExtractHeadline || msg.content || msg.text || '🔄 **Data Transformation Completed**',
            timestamp: timestamp,
            metadata: meta,
            jobId: transformedJobId, // Use transformed_job_id so it can fetch from extracted_results
            pipelineSteps: Array.isArray(meta.pipeline_steps) ? meta.pipeline_steps : null,
            pipelineNarrative:
              typeof meta.pipeline_narrative === 'string' ? meta.pipeline_narrative : null,
            isLoading: false,
            isUnavailable: false,
          };
        });

      const previewBackedJobIds = new Set();
      [...dataPreviewEntries, ...transformationPreviewEntries, ...transformationEntries].forEach((e) => {
        if (e.jobId) previewBackedJobIds.add(String(e.jobId));
      });

      // Filter out messages that have corresponding preview entries (to avoid duplicates)
      // - transformation_completed messages: filtered out because we create transformation_preview entries
      // - extraction_completed: omit when a synthetic preview row exists (matches live SSE: one card)
      const filteredMessages = mappedMessages.filter(msg => {
        // Filter out transformation_completed messages - we create transformation_preview entries for them
        if (msg.metadata?.event_type === 'transformation_completed') {
          return false;
        }
        if (msg.metadata?.event_type === 'extraction_completed') {
          const jid = metadataJobId(msg.metadata);
          if (jid && previewBackedJobIds.has(String(jid))) {
            return false;
          }
        }
        return true;
      });

      // Merge chat messages, preview entries, and transformation entries, sorted by timestamp
      let allEntries = [...filteredMessages, ...previewEntries, ...transformationEntries].sort((a, b) => a.timestamp - b.timestamp);

      // Deduplicate migration_status entries by jobId (keep the first one encountered)
      const seenJobIds = new Set();
      allEntries = allEntries.filter(entry => {
        if (entry.type === 'migration_status' && entry.jobId) {
          if (seenJobIds.has(entry.jobId)) {
            return false; // Skip duplicate
          }
          seenJobIds.add(entry.jobId);
        }
        return true;
      });

      // Restore migration status entries and auto-resume polling for in-progress migrations
      const migrationEntries = allEntries.filter(e => e.type === 'migration_status');
      const migrationStatusesToRestore = {};

      for (const entry of migrationEntries) {
        // IMPORTANT: For migrations, job_id is the background job_id (for polling), batch_id is the migration batch_id
        // Backend stores batch_id as job_id in migration_started event metadata, so we need to detect this
        const metadataJobId = entry.metadata?.job_id;
        const metadataBatchId = entry.metadata?.batch_id;
        const entryJobId = entry.jobId;
        const entryBatchId = entry.batchId;

        // If entry.jobId equals entry.batchId (or metadata.job_id equals metadata.batch_id),
        // then job_id is actually batch_id (backend stored batch_id as job_id)
        const isJobIdActuallyBatchId = (entryJobId && entryBatchId && entryJobId === entryBatchId) ||
                                      (metadataJobId && metadataBatchId && metadataJobId === metadataBatchId);

        // Extract actual IDs
        let actualJobId = null; // Background job_id (for polling)
        let actualBatchId = null; // Migration batch_id (for tracking)

        if (isJobIdActuallyBatchId) {
          // entry.jobId is actually batch_id, not background job_id
          // We don't have the background job_id, so we can't poll
          actualBatchId = entryJobId || metadataJobId || entryBatchId || metadataBatchId;
          // actualJobId remains null - we don't have it
        } else {
          // entry.jobId might be the actual background job_id
          // But we need to verify - if it equals batchId, it's not valid
          actualJobId = entryJobId || metadataJobId;
          actualBatchId = entryBatchId || metadataBatchId || (actualJobId && actualJobId !== entryBatchId ? null : actualJobId);

          // If jobId equals batchId, they're the same (batch_id stored as job_id)
          if (actualJobId && actualBatchId && actualJobId === actualBatchId) {
            actualBatchId = actualJobId;
            actualJobId = null; // Don't have background job_id
          }
        }

        // Skip if we don't have at least batch_id (for tracking)
        if (!actualBatchId) continue;

        // Use batch_id as the key for migrationStatuses (since we might not have job_id)
        const statusKey = actualBatchId;

        // Check if there's a migration_completed event for this migration
        const completedEvent = messages.find(m => 
          m.metadata?.event_type === 'migration_completed' && 
          ((actualBatchId && m.metadata?.batch_id === actualBatchId) ||
           (actualJobId && m.metadata?.job_id === actualJobId))
        );

        if (completedEvent) {
          // Migration is completed - restore from completed event
          // Use metadata.status so "cancelled" and "failed" display correctly on reload
          const metadata = completedEvent.metadata || {};
          const eventStatus = metadata.status || 'success';
          const resolvedStatus = eventStatus === 'cancelled' ? 'cancelled' :
            eventStatus === 'failed' ? 'failed' : 'completed';
          migrationStatusesToRestore[statusKey] = {
            jobId: metadata.job_id || actualJobId, // Use job_id from completed event if available
            batchId: metadata.batch_id || actualBatchId, // Prefer batch_id from completed event
            status: resolvedStatus,
            progress: 100,
            destination: metadata.destination || 'salesforce',
            destinationName: metadata.destination_name,
            successCount: metadata.success_count ?? 0,
            errorCount: metadata.error_count ?? 0,
            totalRecords: entry.metadata?.record_count || entry.metadata?.total_records || 0,
            message: resolvedStatus === 'cancelled' ? 'Partial progress saved. You can rollback migrated records if needed.' :
              resolvedStatus === 'failed' ? (metadata.error || 'Migration failed') : 'Migration completed successfully',
          };
        } else if (actualJobId) {
          migrationStatusesToRestore[statusKey] = {
            jobId: actualJobId,
            batchId: actualBatchId,
            status: 'processing',
            progress: 0,
            destination: entry.metadata?.destination || 'salesforce',
            destinationName: entry.metadata?.destination_name,
            totalRecords: entry.metadata?.record_count || entry.metadata?.total_records || 0,
            message: 'Migration in progress…',
          };
        } else {
          // No background job_id available - migration likely completed but event not found
          // Mark as completed to avoid trying to poll with batch_id
          migrationStatusesToRestore[statusKey] = {
            jobId: null, // No background job_id available
            batchId: actualBatchId, // Store batch_id (for tracking)
            status: 'completed', // Assume completed if no job_id (can't poll)
            progress: 100,
            destination: entry.metadata?.destination || 'salesforce',
            destinationName: entry.metadata?.destination_name,
            totalRecords: entry.metadata?.record_count || entry.metadata?.total_records || 0,
            message: 'Migration status unknown (no background job ID available)',
          };
        }
      }

      // Update migration statuses state
      if (Object.keys(migrationStatusesToRestore).length > 0) {
        setMigrationStatuses(prev => ({ ...prev, ...migrationStatusesToRestore }));

        for (const [, status] of Object.entries(migrationStatusesToRestore)) {
          if (status.status === 'processing' && status.jobId) {
            void syncMigrationTerminalFromServer({
              conversationId,
              jobId: status.jobId,
              batchId: status.batchId,
              setChatHistory,
              setMigrationStatuses,
              plannerPendingRef,
              updateMessageMetadata,
              clearMigrationStatusPropsCache: () => migrationStatusPropsCache.clear(),
            });
          }
        }
      }

      // Re-resolve planner cards using the merged timeline (includes synthetic data_preview rows
      // that replace persisted extraction_completed messages on reload).
      const preparedEntries = preparePlannerChatEntries(allEntries);
      plannerPendingRef.current = resolvePlannerPendingFromLoadedMessages(preparedEntries);
      schedulePersistPlannerResolved(conversationId, preparedEntries, updateMessageMetadata);
      if (isStaleLoad()) return;
      setChatHistory(preparedEntries);

      // Note: Mapping panels will be lazy-loaded when user clicks "Click to load mapping preview"
      // Similar to how data previews work
    } catch (extractionError) {
      console.error('Failed to load extraction metadata:', extractionError);
      // Continue with just chat messages if extraction metadata fails
      if (!isStaleLoad()) {
        setChatHistory(mappedMessages);
      }
    }
  } catch (error) {
    console.error('Failed to load conversation messages:', error);
    if (!isStaleLoad()) {
      setConversationError('Unable to load this conversation. Please try again.');
      setChatHistory([]);
    }
  } finally {
    if (!isStaleLoad()) {
      setIsConversationLoading(false);
    }
  }
}
