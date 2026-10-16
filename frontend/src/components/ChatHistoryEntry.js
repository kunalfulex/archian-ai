import React from 'react';
import { FiCheckCircle, FiMap, FiBarChart2, FiXCircle, FiTrendingUp, FiFile, FiZap, FiArrowUp, FiPaperclip, FiRefreshCw } from 'react-icons/fi';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import api, { cancelJobs } from '../api';
import InlineDataPreview from './InlineDataPreview';
import DataQualitySummary from './DataQualitySummary';
import InlineMappingPanel from './InlineMappingPanel';
import InlineMigrationStatus from './InlineMigrationStatus';
import RecoveryActivityLoader from './RecoveryActivityLoader';
import PipelineSummaryBlocks, { hasPipelineSummaryForEntry } from './PipelineSummaryBlocks';
import { PlannerClarificationPanel } from './PlannerClarificationPanel';
import { PlannerPlanPanel } from './PlannerPlanPanel';
import { PlannerSchemaGatePanel } from './PlannerSchemaGatePanel';
import { PlannerRecoveryPanel } from './PlannerRecoveryPanel';
import { buildPlannerPlanPanelProps } from '../architect/plannerPlanDisplay';
import { metadataMatchesJobId } from '../architect/metadataJobId';
import { resolveSourceSqlQuery } from '../architect/resolveExtractionContextForSave';
import { getMappingTransformation } from '../utils/migrationRulesKeys';
import { runMapFieldsForJob } from '../architect/runMapFieldsForJob';
import { applyMappingStartedToChat } from '../architect/applyMappingStartedToChat';
import {
  buildMigrationDataFromPanel,
  executeChatMigration,
  handleMigrationStartError,
} from '../architect/executeChatMigration';
import {
  flushPersistMappingPanelEdit,
  schedulePersistMappingPanelEdit,
} from '../architect/persistMappingPanelEdit';
import { getPlannerEntryRenderState, shouldHideChatEntry } from '../architect/plannerState';
import {
  destinationTargetValueForType,
  resolveRequiresDestinationForPreview,
} from '../utils/destinationTargetOptions';
import {
  computeMigrationStatusProps,
  migrationStatusPropsCache,
} from '../architect/computeMigrationStatusProps';

const MAPPING_LEARNING_DEBOUNCE_MS = 2000;

/** One architect chat row: user text, assistant markdown, planner panels, inline preview / mapping / migration cards. */
export default function ChatHistoryEntry({
  entry,
  index,
  abortControllerRef,
  activeConversationId,
  activePlannerEntryId,
  beginInlineMappingLoader,
  chatHistory,
  chatHistoryRef,
  downloadExtractionCsvFull,
  endInlineMappingLoader,
  fetchExtractionData,
  fetchExtractionDataPage,
  fetchMappingData,
  handlePlannerClarificationSubmit,
  handlePlannerPlanApprove,
  handlePlannerPlanDismiss,
  handlePlannerRecoveryApprove,
  handlePlannerRecoveryDismiss,
  handlePlannerSchemaDeployApprove,
  handlePreviewDestinationPick,
  inlineDataPreviews,
  inlineDataPreviewsRef,
  isLoading,
  loadingMessageIntervalRef,
  mapButtonRef,
  mappingContextPersistTimersRef,
  mappingLearningFinalizeTimersRef,
  mappingPanels,
  migrationExecutionDeps,
  migrationStatuses,
  persistMessage,
  plannerPendingRef,
  processingPreferenceUpdates,
  recoveryActivity,
  setChatHistory,
  setInlineDataPreviews,
  setIsLoading,
  setLoadingMessage,
  setMappingPanels,
  setPendingReExtract,
  startMigrationButtonRef,
  stopLoadingMessages,
  textareaRef,
  updateMessageMetadata,
}) {
  const fileName = entry.fileName || entry.metadata?.fileName;
  const previewData = entry.jobId ? inlineDataPreviews[entry.jobId] : null;
  
  if (shouldHideChatEntry(entry)) {
    return null;
  }

  const showRecoveryPanel =
    entry.metadata?.event_type === 'planner_recovery_required' &&
      !entry.metadata?.recovery_dismissed &&
      !entry.plannerResolved &&
      entry.metadata?.recovery_proposal;

  const { showPlannerClarPanel, showPlannerPlanPanel, hidePlannerMarkdown } =
    getPlannerEntryRenderState(entry, activePlannerEntryId);
  
  return (
    <div
      className={`chat-message ${entry.type === 'user' ? 'user-message' : 'system-message'}`}
    >
    {entry.type === 'user' ? (
      <div className="user-message-text">{entry.text}</div>
    ) : entry.type !== 'data_preview' &&
      entry.text &&
      !showPlannerClarPanel &&
      !showPlannerPlanPanel &&
      !showRecoveryPanel &&
      !hidePlannerMarkdown ? (
    <div className="react-markdown">
      <ReactMarkdown 
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer">{props.children || ''}</a>,
          // Custom renderer for paragraphs that replaces emojis with icons
          p: ({ node, children, ...props }) => {
            // Process text nodes in children
            const processedChildren = React.Children.map(children, (child) => {
              if (typeof child === 'string') {
                const parts = [];
                let lastIndex = 0;
                
                // Match all emojis
                const emojiPatterns = [
                  { pattern: /✅/g, type: 'check', length: 1, icon: FiCheckCircle, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px', color: '#10b981' } },
                  { pattern: /🗺️/g, type: 'map', length: 2, icon: FiMap, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px' } },
                  { pattern: /📊/g, type: 'barchart', length: 2, icon: FiBarChart2, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px' } },
                  { pattern: /📈/g, type: 'trending', length: 2, icon: FiTrendingUp, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px' } },
                  { pattern: /💡/g, type: 'lightbulb', length: 2, icon: FiZap, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px', color: '#fbbf24' } },
                  { pattern: /📁/g, type: 'file', length: 2, icon: FiFile, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px' } },
                  { pattern: /🚀/g, type: 'rocket', length: 2, icon: FiArrowUp, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px', color: '#6366f1' } },
                  { pattern: /❌/g, type: 'error', length: 1, icon: FiXCircle, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px', color: '#ef4444' } },
                  { pattern: /🔄/g, type: 'refresh', length: 2, icon: FiRefreshCw, style: { display: 'inline', verticalAlign: 'middle', marginLeft: '2px', marginRight: '4px', color: '#6366f1' } }
                ];
                
                const allMatches = [];
                emojiPatterns.forEach(({ pattern, type, length }) => {
                  const matches = [...child.matchAll(pattern)];
                  allMatches.push(...matches.map(m => ({ index: m.index, type, length })));
                });
                
                allMatches.sort((a, b) => a.index - b.index);
                
                allMatches.forEach((match, idx) => {
                  if (match.index > lastIndex) {
                    parts.push(child.substring(lastIndex, match.index));
                  }
                  const emojiConfig = emojiPatterns.find(e => e.type === match.type);
                  if (emojiConfig) {
                    const IconComponent = emojiConfig.icon;
                    parts.push(<IconComponent key={`${match.type}-${idx}`} style={emojiConfig.style} />);
                  }
                  lastIndex = match.index + match.length;
                });
                
                if (lastIndex < child.length) {
                  parts.push(child.substring(lastIndex));
                }
                
                return parts.length > 1 ? parts : child;
              }
              return child;
            });
            
            return <p {...props}>{processedChildren || children}</p>;
          }
        }}
      >
        {entry.text}
      </ReactMarkdown>
    </div>
    ) : null}

    {showPlannerClarPanel && (
      <PlannerClarificationPanel
        clarifications={entry.metadata.clarifications}
        onSubmit={handlePlannerClarificationSubmit}
        disabled={isLoading}
      />
    )}

    {showPlannerPlanPanel && (
      <PlannerPlanPanel
        {...buildPlannerPlanPanelProps(entry)}
        onApprove={() =>
          handlePlannerPlanApprove(entry.metadata?.planner_pending)
        }
        onDismiss={() =>
          handlePlannerPlanDismiss({
            pending: entry.metadata?.planner_pending,
            messageId: entry.id,
          })
        }
        disabled={isLoading}
      />
    )}

    {(entry.metadata?.event_type === 'schema_gate_proposal' ||
      entry.metadata?.event_type === 'planner_schema_deploy_required') &&
      !entry.metadata?.schema_gate_dismissed &&
      !entry.plannerResolved &&
      entry.metadata?.schema_gate_proposal && (
      <PlannerSchemaGatePanel
        proposal={entry.metadata.schema_gate_proposal}
        showDeployApproval={Boolean(entry.metadata?.show_deploy_approval)}
        onApproveDeploy={() => handlePlannerSchemaDeployApprove(false)}
        onDismiss={() => {
          if (entry.metadata?.show_deploy_approval) {
            handlePlannerSchemaDeployApprove(true);
          } else {
            setChatHistory((prev) =>
              prev.map((e) =>
                e.id === entry.id
                  ? { ...e, metadata: { ...e.metadata, schema_gate_dismissed: true } }
                  : e,
              ),
            );
          }
        }}
      />
    )}

    {entry.metadata?.event_type === 'planner_recovery_required' &&
      !entry.metadata?.recovery_dismissed &&
      !entry.plannerResolved &&
      entry.metadata?.recovery_proposal && (
      <PlannerRecoveryPanel
        proposal={entry.metadata.recovery_proposal}
        showRecoveryApproval={Boolean(entry.metadata?.show_recovery_approval)}
        isRunning={isLoading}
        onApplyFix={() => {
          plannerPendingRef.current = entry.metadata?.planner_pending;
          handlePlannerRecoveryApprove(false);
        }}
        onDismiss={() => {
          plannerPendingRef.current = entry.metadata?.planner_pending;
          handlePlannerRecoveryDismiss();
        }}
      />
    )}

    {/* Show inline migration status if available */}
    {entry.type === 'migration_status' && entry.jobId && (() => {
      // Use memoized helper function to compute props (prevents infinite re-execution)
      const props = computeMigrationStatusProps(
        entry,
        migrationStatuses,
        chatHistory,
        mappingPanels,
        inlineDataPreviews
      );
      
      if (!props) return null;
      
      // Create callbacks (these need to be recreated for closures to work correctly)
      const handleUpdateFlowInfo = (newFlowId, newFlowName) => {
        // Update chatHistory with new flow info
        setChatHistory(prev => prev.map(msg => {
          if (msg.metadata?.event_type === 'migration_completed' && 
              (msg.metadata?.job_id === entry.jobId || msg.metadata?.batch_id === entry.batchId)) {
            return {
              ...msg,
              metadata: {
                ...msg.metadata,
                flow_id: newFlowId,
                flow_name: newFlowName,
              }
            };
          }
          return msg;
        }));
        
        // Invalidate cache for this entry so props are recomputed with new flow info
        // The cache key includes flowId, so this ensures fresh props are calculated
        const cacheKeysToDelete = [];
        for (const [key] of migrationStatusPropsCache.entries()) {
          if (key.startsWith(`${entry.jobId}-${entry.batchId || ''}`)) {
            cacheKeysToDelete.push(key);
          }
        }
        cacheKeysToDelete.forEach(key => migrationStatusPropsCache.delete(key));
      };
      
      const handleLogFlowCreated = async (flowData) => {
        if (!activeConversationId) return;
        
        try {
          const sourceLabel = flowData.source_type === 'sql' ? 'SQL Database' : 
                            flowData.source_type === 'gsheets' ? 'Google Sheets' : 
                            flowData.source_type === 'salesforce' ? 'Salesforce' : 
                            'File';
          
          const destinationLabel = flowData.destination_type === 'salesforce' ? 'Salesforce' : 
                                  flowData.destination_type === 'google_sheets' ? 'Google Sheets' : 
                                  flowData.destination_type === 'sql' ? 'SQL Database' : 
                                  'Salesforce';
          
          const flowMessage = `✅ **Flow Created: ${flowData.flow_name}**\n\nSource: ${sourceLabel} • ${flowData.source_name}\n\nDestination: ${destinationLabel} • ${flowData.destination_name}`;
          
          const flowMetadata = {
            event_type: 'flow_created',
            flow_id: flowData.flow_id,
            flow_name: flowData.flow_name,
            source_type: flowData.source_type,
            source_name: flowData.source_name,
            destination_type: flowData.destination_type,
            destination_name: flowData.destination_name,
            job_id: flowData.job_id,
            batch_id: flowData.batch_id,
          };
          
          const savedMessage = await persistMessage(activeConversationId, 'assistant', flowMessage, flowMetadata);
          
          setChatHistory(prev => {
            const newHistory = [...prev];
            // Always append flow_created messages at the end (they are the latest events)
            const flowCreatedEntry = {
              id: savedMessage?.id || `flow-created-${Date.now()}`,
              type: 'data_event',
              text: flowMessage,
              timestamp: Date.now(),
              metadata: flowMetadata,
            };
            newHistory.push(flowCreatedEntry);
            return newHistory;
          });
        } catch (error) {
          console.error('Failed to log flow_created event:', error);
        }
      };

      const handleLogFlowScheduled = async (scheduleData) => {
        if (!activeConversationId) return;
        
        try {
          const schedule_action = scheduleData.schedule_action || 'updated';
          const schedule_frequency = scheduleData.schedule_frequency || '';
          
          // Capitalize frequency for display
          const frequencyLabel = schedule_frequency 
            ? schedule_frequency.charAt(0).toUpperCase() + schedule_frequency.slice(1)
            : '';
          
          let scheduleMessage = '';
          if (schedule_action === 'created') {
            scheduleMessage = `✅ **Flow Scheduled: ${scheduleData.flow_name}**\n\nSchedule: ${frequencyLabel}`;
          } else if (schedule_action === 'updated') {
            scheduleMessage = `✅ **Flow Schedule Updated: ${scheduleData.flow_name}**\n\nNew Schedule: ${frequencyLabel}`;
          } else if (schedule_action === 'disabled') {
            scheduleMessage = `⚠️ **Flow Schedule Disabled: ${scheduleData.flow_name}**`;
          } else {
            scheduleMessage = `✅ **Flow Scheduled: ${scheduleData.flow_name}**\n\nSchedule: ${frequencyLabel}`;
          }
          
          const scheduleMetadata = {
            event_type: 'flow_scheduled',
            flow_id: scheduleData.flow_id,
            flow_name: scheduleData.flow_name,
            source_type: scheduleData.source_type,
            source_name: scheduleData.source_name,
            destination_type: scheduleData.destination_type,
            destination_name: scheduleData.destination_name,
            job_id: scheduleData.job_id,
            batch_id: scheduleData.batch_id,
            schedule_frequency: scheduleData.schedule_frequency,
            schedule_enabled: scheduleData.schedule_enabled,
            schedule_timezone: scheduleData.schedule_timezone,
            schedule_action: schedule_action
          };
          
          const savedMessage = await persistMessage(activeConversationId, 'assistant', scheduleMessage, scheduleMetadata);
          
          setChatHistory(prev => {
            const newHistory = [...prev];
            // Always append schedule messages at the end (they are the latest events)
            const scheduleEntry = {
              id: savedMessage?.id || `flow-scheduled-${Date.now()}`,
              type: 'data_event',
              text: scheduleMessage,
              timestamp: Date.now(),
              metadata: scheduleMetadata,
            };
            newHistory.push(scheduleEntry);
            return newHistory;
          });
        } catch (error) {
          console.error('Failed to log flow_scheduled event:', error);
        }
      };

      const handleCancelMigration = async (jobIdToCancel) => {
        await cancelJobs([jobIdToCancel], []);
        // Next poll will get 'cancelled' status and update with full results
      };

      const handleLogExport = async (exportData) => {
        if (!activeConversationId) return;

        try {
          const { statusFilter, successCount, errorCount, totalRecords, destination, destinationName } = exportData;
          const destLabel = destination === 'salesforce' ? 'Salesforce' :
            destination === 'google_sheets' ? 'Google Sheets' :
            destination === 'sql' ? 'SQL Database' :
            (destinationName || destination || 'Unknown');
          let recordDesc = '';
          if (statusFilter === 'success') {
            recordDesc = `${successCount} successful record${successCount !== 1 ? 's' : ''}`;
          } else if (statusFilter === 'failed') {
            recordDesc = `${errorCount} failed record${errorCount !== 1 ? 's' : ''}`;
          } else {
            recordDesc = `${totalRecords} total record${totalRecords !== 1 ? 's' : ''}`;
            if (successCount > 0 || errorCount > 0) {
              recordDesc += ` (${successCount} successful${errorCount > 0 ? `, ${errorCount} failed` : ''})`;
            }
          }
          const jobIdDisplay = exportData.jobId || exportData.batchId;
          const exportMessage = `📥 **Migration Results Exported**\n\nExported ${recordDesc} to Excel\n\nDestination: ${destLabel}${destinationName ? ` • ${destinationName}` : ''}${jobIdDisplay ? `\n\nJob ID: \`${jobIdDisplay}\`` : ''}`;

          const exportMetadata = {
            event_type: 'migration_export',
            batch_id: exportData.batchId,
            job_id: exportData.jobId,
            status_filter: statusFilter,
            success_count: successCount,
            error_count: errorCount,
            total_records: totalRecords,
            destination,
            destination_name: destinationName,
          };

          const savedMessage = await persistMessage(activeConversationId, 'assistant', exportMessage, exportMetadata);

          setChatHistory(prev => {
            const newHistory = [...prev];
            const exportEntry = {
              id: savedMessage?.id || `migration-export-${Date.now()}`,
              type: 'data_event',
              text: exportMessage,
              timestamp: Date.now(),
              metadata: exportMetadata,
            };
            newHistory.push(exportEntry);
            return newHistory;
          });
        } catch (error) {
          console.error('Failed to log migration_export event:', error);
        }
      };

      const handleRollbackCompleted = async (rollbackData) => {
        if (!activeConversationId) return;
        
        try {
          // Prefer explicit source information from rollback payload if available,
          // otherwise fall back to the earlier migration_completed event.
          const migrationCompletedEvent = chatHistory.find(e =>
            e.metadata?.event_type === 'migration_completed' &&
            e.metadata?.job_id === rollbackData.job_id
          );

          const sourceType = rollbackData.source_type ||
            migrationCompletedEvent?.metadata?.source_type ||
            migrationCompletedEvent?.metadata?.extracted_schema_type ||
            'file';

          const sourceName = rollbackData.source_name ||
            migrationCompletedEvent?.metadata?.source_name ||
            migrationCompletedEvent?.metadata?.document_type ||
            'Unknown Source';
          const documentType = migrationCompletedEvent?.metadata?.document_type;
          
          // Format source type label (human-readable)
          const sourceTypeLabel = {
            'file': 'File',
            'sql': 'SQL Database',
            'gsheets': 'Google Sheets',
            'google_sheets': 'Google Sheets',
            'salesforce': 'Salesforce'
          }[sourceType] || sourceType;
          
          // Format destination type label (human-readable)
          const destinationTypeLabel = {
            'salesforce': 'Salesforce',
            'google_sheets': 'Google Sheets',
            'sql': 'SQL Database'
          }[rollbackData.destination] || rollbackData.destination;
          
          // Format message with Option 1 structure
          const rollbackMessageText = `🔄 **Rollback Completed**\n\n` +
            `Rolled back: ${rollbackData.deleted_records} records\n\n` +
            `Source: ${sourceName} • ${sourceTypeLabel}\n\n` +
            `Destination: ${rollbackData.destination_name} (${destinationTypeLabel})\n\n` +
            `Job ID: ${rollbackData.job_id}`;
          
          const rollbackMetadata = {
            event_type: 'rollback_completed',
            batch_id: rollbackData.batch_id,
            job_id: rollbackData.job_id,
            deleted_records: rollbackData.deleted_records,
            failed_records: rollbackData.failed_records,
            total_records: rollbackData.total_records,
            destination: rollbackData.destination,
            destination_name: rollbackData.destination_name,
            // Add source information from migration_completed event
            source_type: sourceType === 'gsheets' ? 'google_sheets' : sourceType,
            source_name: sourceName,
            document_type: documentType,
          };
          
          const savedMessage = await persistMessage(activeConversationId, 'assistant', rollbackMessageText, rollbackMetadata);
          
          // Add to chat history immediately so it shows up right away
          setChatHistory(prev => {
            const newHistory = [...prev];
            // Check if already exists to avoid duplicates
            const exists = prev.some(e => 
              e.id === savedMessage?.id || 
              (e.metadata?.event_type === 'rollback_completed' && 
               e.metadata?.batch_id === rollbackData.batch_id)
            );
            if (exists) return prev;
            
            const rollbackEntry = {
              id: savedMessage?.id || `rollback-completed-${Date.now()}`,
              type: 'system',
              text: rollbackMessageText,
              timestamp: savedMessage?.created_at ? new Date(savedMessage.created_at).getTime() : Date.now(),
              metadata: rollbackMetadata,
            };
            newHistory.push(rollbackEntry);
            return newHistory;
          });
        } catch (error) {
          console.error('Failed to log rollback_completed event:', error);
        }
      };

      return (
        <div className="chat-inline-preview">
          <InlineMigrationStatus
            {...props.migrationStatus}
            mapping={props.mapping}
            migrationInstanceURL={props.migrationInstanceURL}
            destinationSpreadsheetId={props.destinationSpreadsheetId}
            firstRecord={props.firstRecord}
            allRecords={props.allRecords}
            documentType={props.documentType}
            documentDescription={props.documentDescription}
            userPrompt={props.userPrompt}
            extractedSchemaType={props.extractedSchemaType}
            sourceConnectorId={props.sourceConnectorId}
            sourceSqlQuery={props.sourceSqlQuery}
            sourceTableName={props.sourceTableName}
            sourceName={props.sourceName}
            destinationConnectorId={props.destinationConnectorId}
            destinationTableName={props.destinationTableName}
            destinationSheetName={props.destinationSheetName}
            fileKey={props.fileKey}
            flowId={props.flowId}
            flowName={props.flowName}
            conversationId={activeConversationId}
            migrationCompletedMessageId={props.migrationCompletedMessageId}
            missingFieldPreference={props.missingFieldPreference}
            transformationRules={props.transformationRules}
            extractionRules={props.extractionRules}
            failureSummary={props.failureSummary}
            onUpdateFlowInfo={handleUpdateFlowInfo}
            onLogFlowCreated={handleLogFlowCreated}
            onLogFlowScheduled={handleLogFlowScheduled}
            onRollbackCompleted={handleRollbackCompleted}
            onLogExport={handleLogExport}
            onCancelMigration={handleCancelMigration}
          />
          {recoveryActivity?.active &&
            (recoveryActivity.jobId === entry.jobId ||
              recoveryActivity.batchId === entry.batchId) &&
            (recoveryActivity.items?.length ?? 0) > 0 && (
            <RecoveryActivityLoader preview={recoveryActivity} />
          )}
        </div>
      );
    })()}

    {/* Show inline mapping panel if available */}
    {entry.type === 'mapping_panel' && (() => {
      const mappingId = entry.mappingId || entry.metadata?.mapping_id;
      // Silently skip entries without mappingId (likely old entries from before metadata was stored)
      if (!mappingId) {
        return null;
      }
      return (
      <div className="chat-inline-preview">
        {entry.isLoading && (
          <div className="preview-loading">
            <div className="loading-spinner"></div>
            <span>Loading mapping panel...</span>
          </div>
        )}
        {entry.isUnavailable && !entry.isLoading && (
          <div className="preview-unavailable">
            <span>⚠️ Mapping panel unavailable</span>
            <p>The mapping data is no longer available.</p>
          </div>
        )}
        {!entry.isLoading && !entry.isUnavailable && !mappingPanels[mappingId] && (
          <div className="preview-placeholder">
            <div className="preview-metadata">
              <p><strong><FiMap style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> Field Mapping</strong></p>
              {entry.metadata && (
                <>
                  <p>Destination: {entry.metadata.destination_name || entry.metadata.destination || 'Salesforce'}</p>
                  <p>Type: {entry.metadata.document_type || 'Unknown'}</p>
                </>
              )}
            </div>
            <button
              className="expand-preview-button"
              onClick={async () => {
                if (mappingId && entry.metadata) {
                  await fetchMappingData(mappingId, entry.id, entry.metadata);
                }
              }}
            >
              <FiMap style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> Click to load mapping preview
            </button>
          </div>
        )}
        {!entry.isLoading && !entry.isUnavailable && mappingPanels[mappingId] && (
          <InlineMappingPanel
              startMigrationButtonRef={startMigrationButtonRef}
              firstRecord={mappingPanels[mappingId].firstRecord}
              allRecords={mappingPanels[mappingId].allRecords}
              totalRecordCount={mappingPanels[mappingId].totalRecordCount ?? mappingPanels[mappingId].allRecords?.length ?? 0}
              detailExpired={!!mappingPanels[mappingId].detailExpired}
              mapping={mappingPanels[mappingId].mapping}
              transformationRules={mappingPanels[mappingId].transformationRules || { fields: [] }}
              objectFieldMap={mappingPanels[mappingId].objectFieldMap}
              orgRulesDisplayByObject={mappingPanels[mappingId].orgRulesDisplayByObject || {}}
              documentType={
                entry.metadata?.destination_document_type ||
                mappingPanels[mappingId].documentType
              }
              documentDescription={
                entry.metadata?.destination_document_description ||
                mappingPanels[mappingId].documentDescription
              }
              sourceDocumentType={
                entry.metadata?.source_document_type ||
                mappingPanels[mappingId].sourceDocumentType
              }
              sourceDocumentDescription={
                entry.metadata?.source_document_description ||
                mappingPanels[mappingId].sourceDocumentDescription
              }
              destinationDocumentType={
                entry.metadata?.destination_document_type ||
                mappingPanels[mappingId].destinationDocumentType
              }
              destinationDocumentDescription={
                entry.metadata?.destination_document_description ||
                mappingPanels[mappingId].destinationDocumentDescription
              }
              destinationObjectApiName={
                entry.metadata?.destination_object_api_name ||
                mappingPanels[mappingId].destinationObjectApiName
              }
              destinationTableName={
                entry.metadata?.destination_table_name ||
                mappingPanels[mappingId].destinationTableName
              }
              destinationSheetName={
                entry.metadata?.destination_sheet_name ||
                mappingPanels[mappingId].destinationSheetName
              }
              destination={mappingPanels[mappingId].destination}
              destinationName={mappingPanels[mappingId].destinationName}
              conversationId={mappingPanels[mappingId].conversationId}
              mappingId={mappingId}
              jobId={entry.metadata?.job_id || null}
              flowId={entry.metadata?.flow_id || null}
              orgId={null}
              missingFieldPreference={
                mappingPanels[mappingId].missingFieldPreference || 
                entry.metadata?.missing_field_preference || 
                {
                  option: 'skip'
                }
              }
              mappingMessageId={entry.id}
              onMissingFieldPreferenceChange={async (preference) => {
                // Prevent duplicate processing for the same mapping
                if (processingPreferenceUpdates.current.has(mappingId)) {
                  return;
                }
                
                // Update mappingPanels state
                setMappingPanels(prev => ({
                  ...prev,
                  [mappingId]: {
                    ...prev[mappingId],
                    missingFieldPreference: preference,
                  }
                }));
                
                // Update metadata in database if we have a valid UUID
                if (activeConversationId && entry.id && entry.canUpdate !== false) {
                  const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
                  if (uuidRegex.test(entry.id)) {
                    // Mark as processing
                    processingPreferenceUpdates.current.add(mappingId);
                    
                    try {
                      const updatedMetadata = {
                        ...entry.metadata,
                        missing_field_preference: preference,
                      };
                      await updateMessageMetadata(activeConversationId, entry.id, updatedMetadata);
                      
                      // Update entry metadata in chatHistory
                      setChatHistory(prev =>
                        prev.map(e =>
                          e.id === entry.id
                            ? { ...e, metadata: updatedMetadata }
                            : e
                        )
                      );
                      
                      // ✅ ALWAYS create a NEW message for each preference change to preserve chat history
                      // This ensures users can see the full sequence of their decisions
                      const optionText = preference.option === 'placeholder'
                        ? `Fill missing required fields with placeholder: "${preference.placeholderValue}"`
                        : 'Skip records with missing required fields';
                      const preferenceMessage = `✅ **Missing Field Preference Updated**\n\n${optionText}`;
                      
                      // Create a unique ID based on timestamp and mappingId to ensure uniqueness
                      // This allows multiple preference changes for the same mapping to be tracked separately
                      const tempId = `preference-updated-${mappingId}-${Date.now()}`;
                      
                      // Add message silently (don't trigger auto-scroll)
                      // We'll add it with metadata to identify it as a preference update
                      setChatHistory(prev => [...prev, {
                        id: tempId,
                        type: 'system',
                        text: preferenceMessage,
                        timestamp: Date.now(),
                        metadata: {
                          event_type: 'mapping_preference_updated',
                          mapping_id: mappingId,
                          missing_field_preference: preference,
                        }
                      }]);
                      
                      // Persist the preference update message and update with real ID
                      // Do this in the background without blocking
                      persistMessage(activeConversationId, 'assistant', preferenceMessage, {
                        event_type: 'mapping_preference_updated',
                        mapping_id: mappingId,
                        missing_field_preference: preference,
                      }).then(persistedMessage => {
                        // Update the temporary ID with the real persisted message ID
                        if (persistedMessage?.id) {
                          setChatHistory(prev =>
                            prev.map(e =>
                              e.id === tempId
                                ? { ...e, id: persistedMessage.id, metadata: persistedMessage.metadata || {} }
                                : e
                            )
                          );
                        }
                      }).catch(error => {
                        console.error('Failed to persist preference update message:', error);
                      });
                    } catch (error) {
                      console.error('Failed to update missing field preference in database:', error);
                    } finally {
                      // Remove from processing set
                      processingPreferenceUpdates.current.delete(mappingId);
                    }
                  }
                }
              }}
              onMigrationStartLoader={(message) => {
                // Show loader when migration starts
                setLoadingMessage(message);
                setIsLoading(true);
                // Don't start rotating messages, just show static message
                if (loadingMessageIntervalRef.current) {
                  clearInterval(loadingMessageIntervalRef.current);
                  loadingMessageIntervalRef.current = null;
                }
              }}
              onMigrationEndLoader={() => {
                // Hide loader when migration ends (error case)
                stopLoadingMessages();
                setIsLoading(false);
              }}
              onPlanLimitExceeded={async (errorData) => {
                const details = errorData.details || {};
                const planType = details.plan_type || 'Unknown';
                const balance = details.credit_balance ?? details.remaining_credits ?? 0;
                const canExtract = details.can_extract ?? details.remaining_records ?? 0;
                const requestedRecords = details.requested_records || 0;

                const planLimitText = `📊 **Credit Limit Reached**\n\n` +
                  `Your current plan (${planType}) is limited by your credit balance.\n\n` +
                  `📈 **Summary:**\n` +
                  `• Credit balance: **${Number(balance).toLocaleString()}**\n` +
                  `• Records you can extract: **${Number(canExtract).toLocaleString()}**\n` +
                  (requestedRecords > 0 ? `• Requested: **${Number(requestedRecords).toLocaleString()}**\n` : '') +
                  `\n💡 **Solution:** Add credits or upgrade your plan to extract more.`;

                setChatHistory((prev) => [...prev, {
                  id: `plan-limit-${Date.now()}`,
                  type: 'system',
                  text: planLimitText,
                  timestamp: Date.now(),
                  showUpgradeButton: true,
                  metadata: {
                    event_type: 'plan_limit_exceeded',
                    planInfo: {
                      plan_type: planType,
                      credit_balance: balance,
                      can_extract: canExtract,
                      requested_records: requestedRecords
                    }
                  }
                }]);

                if (activeConversationId) {
                  await persistMessage(activeConversationId, 'assistant', planLimitText, {
                    planInfo: {
                      plan_type: planType,
                      credit_balance: balance,
                      can_extract: canExtract,
                      requested_records: requestedRecords
                    }
                  });
                }
              }}
              primaryTable={
                mappingPanels[mappingId].primaryTable ??
                entry.metadata?.primary_table ??
                null
              }
              onMappingChange={async (mappingId, updatedMapping, updatedTransformationRules) => {
                const nextRules =
                  updatedTransformationRules ??
                  mappingPanels[mappingId]?.transformationRules ??
                  getMappingTransformation(entry.metadata) ??
                  { fields: [] };

                const panelSnapshot = {
                  ...mappingPanels[mappingId],
                  mapping: updatedMapping,
                  transformationRules: nextRules,
                };

                setMappingPanels((prev) => ({
                  ...prev,
                  [mappingId]: panelSnapshot,
                }));

                if (
                  activeConversationId &&
                  entry.id &&
                  entry.metadata &&
                  entry.canUpdate !== false
                ) {
                  schedulePersistMappingPanelEdit(
                    {
                      conversationId: activeConversationId,
                      entryId: entry.id,
                      entryMetadata: entry.metadata,
                      panel: panelSnapshot,
                      updatedMapping,
                      transformationRules: nextRules,
                      updateMessageMetadata,
                      setChatHistory,
                      mappingId,
                      timersRef: mappingContextPersistTimersRef,
                    },
                    400,
                  );

                  const uuidRegex =
                    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
                  if (uuidRegex.test(entry.id)) {
                    const timerKey = `${mappingId}-${entry.id}`;
                    if (mappingLearningFinalizeTimersRef.current[timerKey]) {
                      clearTimeout(mappingLearningFinalizeTimersRef.current[timerKey]);
                    }
                    mappingLearningFinalizeTimersRef.current[timerKey] = setTimeout(() => {
                      api
                        .post('/api/learning/mapping-finalize', {
                          conversation_id: activeConversationId,
                          message_id: entry.id,
                          job_id: entry.metadata?.job_id,
                          mapping_config: updatedMapping,
                          transformation_rules: nextRules,
                          mapping_plan: entry.metadata?.mapping_plan,
                          destination_object:
                            entry.metadata?.destination_object_api_name ||
                            entry.metadata?.document_type,
                        })
                        .catch(() => {});
                    }, MAPPING_LEARNING_DEBOUNCE_MS);
                  }
                }
              }}
              onTransformationComplete={async (transformedData, transformedJobId, transformationMetadata) => {
                // Build summary of split fields from transformation metadata
                const splitFieldsSummary = {};
                if (transformationMetadata?.transformations_applied) {
                  transformationMetadata.transformations_applied.forEach((transformation) => {
                    if (transformation.transformation?.startsWith('split_')) {
                      const sourceField = transformation.source_field;
                      if (!splitFieldsSummary[sourceField]) {
                        splitFieldsSummary[sourceField] = {
                          source_field: sourceField,
                          destination_fields: new Set(), // Use Set to avoid duplicates
                        };
                      }
                      // Add destination field (extract field name from "Object.Field" format)
                      const destField = transformation.destination_field?.split('.').pop();
                      if (destField) {
                        splitFieldsSummary[sourceField].destination_fields.add(destField);
                      }
                    }
                  });
                  // Convert Sets to Arrays for JSON serialization
                  Object.keys(splitFieldsSummary).forEach(key => {
                    splitFieldsSummary[key].destination_fields = Array.from(splitFieldsSummary[key].destination_fields);
                  });
                }
                
                // Create transformation summary text
                const splitFieldsList = Object.values(splitFieldsSummary).map(sf => 
                  `**${sf.source_field}** → ${sf.destination_fields.join(', ')}`
                ).join('\n');
                
                const transformationMessage = splitFieldsList 
                  ? `🔄 **Data Transformation Completed**\n\nField(s) split into multiple fields:\n${splitFieldsList}\n\nPreview transformed data below:`
                  : `🔄 **Data Transformation Completed**\n\nPreview transformed data below:`;
                
                const resolvedTransformCount =
                  transformationMetadata?.record_count != null && transformationMetadata.record_count !== ''
                    ? Number(transformationMetadata.record_count)
                    : Array.isArray(transformedData)
                      ? transformedData.length
                      : 0;

                const mappingPanel = mappingPanels[mappingId];
                const sourceExtractionJobId = entry.metadata?.job_id || null;
                const sourceSqlQueryForTransform = resolveSourceSqlQuery({
                  chatHistory: chatHistoryRef.current,
                  inlineDataPreviews: inlineDataPreviewsRef.current,
                  sourceJobId: sourceExtractionJobId,
                  frontendMappingStartedEvent: entry,
                  mappingPanelData: mappingPanel,
                  previewData: sourceExtractionJobId
                    ? inlineDataPreviewsRef.current[sourceExtractionJobId]
                    : null,
                });
                const metadata = {
                  event_type: 'transformation_completed',
                  job_id: sourceExtractionJobId,
                  parent_job_id: sourceExtractionJobId,
                  transformed_job_id: transformedJobId,
                  mapping_id: mappingId,
                  record_count: resolvedTransformCount,
                  transformation_metadata: transformationMetadata,
                  split_fields_summary: splitFieldsSummary,
                  destination: entry.metadata?.destination || mappingPanel?.destination || 'salesforce',
                  destination_name: entry.metadata?.destination_name || mappingPanel?.destinationName || null,
                  document_type: entry.metadata?.document_type || mappingPanel?.documentType || null,
                  document_description:
                    entry.metadata?.document_description || mappingPanel?.documentDescription || null,
                  source_type: entry.metadata?.source_type || mappingPanel?.sourceType || null,
                  source_name: entry.metadata?.source_name || mappingPanel?.sourceName || null,
                  connector_id: entry.metadata?.connector_id || mappingPanel?.connectorId || null,
                  sql_query: sourceSqlQueryForTransform,
                  table_name: entry.metadata?.table_name || mappingPanel?.tableName || null,
                };
                
                // Create transformation_completed event in chat
                const transformationEventId = `transformation-${transformedJobId}`;
                setChatHistory((prev) => [...prev, {
                  id: transformationEventId,
                  type: 'transformation_preview',
                  text: transformationMessage,
                  timestamp: Date.now(),
                  jobId: transformedJobId,
                  metadata: metadata,
                  isLoading: false,
                  isUnavailable: false,
                }]);
                
                // Store transformed data in inlineDataPreviews
                setInlineDataPreviews((prev) => ({
                  ...prev,
                  [transformedJobId]: {
                    jobId: transformedJobId,
                    data: transformedData,
                    documentType: mappingPanel?.documentType || entry.metadata?.document_type || null,
                    documentDescription:
                      mappingPanel?.documentDescription || entry.metadata?.document_description || null,
                    recordCount: resolvedTransformCount,
                    sourceType: entry.metadata?.source_type || mappingPanel?.sourceType || null,
                    sourceName: entry.metadata?.source_name || mappingPanel?.sourceName || null,
                    destination: entry.metadata?.destination || mappingPanel?.destination || 'salesforce',
                    connectorId: entry.metadata?.connector_id || mappingPanel?.connectorId || null,
                    sqlQuery: sourceSqlQueryForTransform,
                    tableName: entry.metadata?.table_name || mappingPanel?.tableName || null,
                  },
                }));
                
                // Persist the transformation_completed event to database
                if (activeConversationId) {
                  persistMessage(activeConversationId, 'assistant', transformationMessage, metadata)
                    .then((persistedMessage) => {
                      if (persistedMessage?.id) {
                        setChatHistory((prev) =>
                          prev.map((e) =>
                            e.id === transformationEventId
                              ? { ...e, id: persistedMessage.id, metadata: { ...e.metadata, ...persistedMessage.metadata } }
                              : e
                          )
                        );
                      }
                    })
                    .catch((error) => {
                      console.error('Failed to persist transformation_completed event:', error);
                    });
                }
              }}
              onMigrationStart={async (migrationData) => {
                try {
                  const panel = mappingPanels[mappingId];
                  if (
                    activeConversationId &&
                    entry.id &&
                    entry.metadata &&
                    entry.canUpdate !== false &&
                    panel?.mapping
                  ) {
                    await flushPersistMappingPanelEdit({
                      conversationId: activeConversationId,
                      entryId: entry.id,
                      entryMetadata: entry.metadata,
                      panel,
                      updatedMapping: panel.mapping,
                      transformationRules:
                        panel.transformationRules ??
                        getMappingTransformation(entry.metadata) ??
                        { fields: [] },
                      updateMessageMetadata,
                      setChatHistory,
                      mappingId,
                      timersRef: mappingContextPersistTimersRef,
                    });
                  }
                  const fullMigrationData = buildMigrationDataFromPanel({
                    mappingData: {
                      ...panel,
                      mapping: migrationData.mapping ?? panel?.mapping,
                      documentType:
                        migrationData.document_type ?? panel?.documentType,
                      documentDescription:
                        migrationData.document_description ?? panel?.documentDescription,
                      destination: migrationData.destination ?? panel?.destination,
                      destinationName:
                        migrationData.destination_name ?? panel?.destinationName,
                      transformationRules:
                        migrationData.transformation_rules != null
                          ? migrationData.transformation_rules
                          : panel?.transformationRules,
                      primaryTable:
                        migrationData.primary_table ?? panel?.primaryTable,
                      jobId:
                        migrationData.job_id ??
                        panel?.jobId ??
                        entry.metadata?.job_id,
                    },
                    entry,
                    chatHistory: chatHistoryRef.current,
                    activeConversationId,
                  });
                  await executeChatMigration(
                    migrationExecutionDeps,
                    {
                      ...fullMigrationData,
                      is_transformed_data: migrationData.is_transformed_data,
                      transformed_job_id: migrationData.transformed_job_id,
                      migration_rules: migrationData.migration_rules,
                    },
                    entry,
                  );
                } catch (error) {
                  console.error('Migration start error:', error);
                  await handleMigrationStartError(migrationExecutionDeps, error);
                }
              }}
        />
        )}
      </div>
      );
    })()}

    {/* Show inline data preview */}
    {entry.type === 'data_preview' && (
      <>
        {hasPipelineSummaryForEntry(entry) ? (
          <div className="chat-extraction-pipeline-wrap">
            <PipelineSummaryBlocks entry={entry} />
          </div>
        ) : null}
        <div className="chat-inline-preview chat-inline-preview--extraction">
        {entry.text?.trim() ? (
          <div className="chat-extraction-operation-summary react-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.text}</ReactMarkdown>
          </div>
        ) : null}
        {(() => {
          const qualityReport =
            previewData?.dataQualityReport ||
            previewData?.data_quality_report ||
            entry.metadata?.data_quality_report;
          const qualityAlreadyInText =
            typeof entry.text === 'string' &&
            entry.text.includes('**Data Quality Analysis**');
          if (!qualityReport?.summary || qualityAlreadyInText) return null;
          return <DataQualitySummary report={qualityReport} />;
        })()}
        {entry.isLoading && (
          <div className="preview-loading">
            <div className="loading-spinner"></div>
            <span>Loading preview data...</span>
          </div>
        )}
        {entry.isUnavailable && !entry.isLoading && (
          <div className="preview-unavailable">
            <span>⚠️ Preview unavailable</span>
            <p>The extraction data is no longer available.</p>
          </div>
        )}
        {!entry.isLoading && !entry.isUnavailable && previewData && (() => {
          const previewRequiresDestination = resolveRequiresDestinationForPreview({
            destination: previewData.destination ?? entry.metadata?.destination,
            destinationName:
              previewData.destinationName ??
              previewData.destination_name ??
              entry.metadata?.destination_name,
            sourceName: previewData.sourceName ?? entry.metadata?.source_name,
            requiresDestinationFlag:
              previewData.requiresDestination ??
              entry.metadata?.requires_destination,
          });
          return (
          <>
            <InlineDataPreview
            jobId={previewData.jobId}
            mapButtonRef={mapButtonRef}
            data={previewData.data}
            documentType={previewData.documentType}
            documentDescription={previewData.documentDescription}
            recordCount={previewData.recordCount}
            isPaginated={previewData._isPaginated}
            loadedCount={previewData._isPaginated ? (previewData.data?.length ?? 0) : undefined}
            totalRecordCount={previewData._totalCount ?? previewData.recordCount}
            fullRecordCount={previewData._fullTotalCount ?? previewData._totalCount ?? previewData.recordCount}
            apiPageSize={500}
            onFetchPage={fetchExtractionDataPage}
            onDownloadFullCsv={downloadExtractionCsvFull}
            downloadDisabled={!!previewData._downloadDisabled}
            sourceType={previewData.sourceType}
            sourceName={previewData.sourceName}
            destination={previewData.destination}
            destinationName={
              previewData.destinationName ?? previewData.destination_name
            }
            destinationObjectApiName={
              previewData.destinationObjectApiName ??
              previewData.destination_object_api_name
            }
            destinationTableName={
              previewData.destinationTableName ??
              previewData.destination_table_name
            }
            destinationSheetName={
              previewData.destinationSheetName ??
              previewData.destination_sheet_name
            }
            requiresDestination={previewRequiresDestination}
            mapDisabled={
              previewRequiresDestination ||
              !previewData.destination ||
              previewData.destination === 'unknown'
            }
            onDestinationPick={(destType, destName, destTarget) =>
              handlePreviewDestinationPick(
                previewData.jobId,
                destType,
                destName,
                destTarget,
              )
            }
            dataQualityReport={previewData.dataQualityReport || previewData.data_quality_report}
            onMappingStart={(message) => {
              beginInlineMappingLoader(message);
            }}
            onMappingEnd={() => {
              endInlineMappingLoader();
            }}
            onMapToSalesforce={async (sortedFirstRecord, sortedAllRecords) => {
            // Create AbortController so Stop button can cancel the map-fields request
            abortControllerRef.current = new AbortController();
            const signal = abortControllerRef.current.signal;
            try {
              const currentChatHistory = chatHistoryRef.current;
              const extractionCompletedEvent = currentChatHistory.find(e => 
                e.metadata?.event_type === 'extraction_completed' && 
                metadataMatchesJobId(e.metadata, previewData.jobId)
              );
              
              const destinationForMap =
                previewData.destination ||
                extractionCompletedEvent?.metadata?.destination;
              if (
                !destinationForMap ||
                destinationForMap === 'unknown' ||
                previewData.requiresDestination ||
                extractionCompletedEvent?.metadata?.requires_destination
              ) {
                const errMsg = 'Choose a destination before mapping.';
                setChatHistory((prev) => [...prev, {
                  id: `error-mapping-${Date.now()}`,
                  type: 'system',
                  text: `❌ **Mapping blocked**\n\n${errMsg}`,
                  timestamp: Date.now(),
                }]);
                endInlineMappingLoader();
                return;
              }
              const destTargetForMap = destinationTargetValueForType(destinationForMap, {
                destinationObjectApiName:
                  previewData.destination_object_api_name ||
                  extractionCompletedEvent?.metadata?.destination_object_api_name,
                destinationTableName:
                  previewData.destination_table_name ||
                  extractionCompletedEvent?.metadata?.destination_table_name,
                destinationSheetName:
                  previewData.destination_sheet_name ||
                  extractionCompletedEvent?.metadata?.destination_sheet_name,
              });
              const mapResult = await runMapFieldsForJob({
                jobId: previewData.jobId,
                destination: destinationForMap,
                destinationName:
                  previewData.destinationName ||
                  extractionCompletedEvent?.metadata?.destination_name,
                destTarget: destTargetForMap || undefined,
                sortedFirstRecord,
                sortedAllRecords,
                inlineDataPreviews: inlineDataPreviewsRef.current,
                chatHistory: currentChatHistory,
                metadataMatchesJobId,
                signal,
                conversationId: activeConversationId,
              });
              await applyMappingStartedToChat({
                jobId: previewData.jobId,
                mapResult,
                activeConversationId,
                chatHistory: currentChatHistory,
                setChatHistory,
                setMappingPanels,
                persistMessage,
              });
              endInlineMappingLoader();
            } catch (error) {
              // User cancelled via Stop button - don't show error
              const isAborted = error?.name === 'AbortError' || error?.code === 'ERR_CANCELED' || error?.message?.includes('aborted');
              if (isAborted) {
                endInlineMappingLoader();
                return;
              }
              console.error('Error starting mapping:', error);
              
              // Extract error message from response
              let errorMessage = 'Failed to start mapping. Please try again.';
              if (error.response?.data?.detail) {
                errorMessage = error.response.data.detail;
              } else if (error.response?.data?.message) {
                errorMessage = error.response.data.message;
              } else if (error.message) {
                errorMessage = error.message;
              }
              
              // Show error in chat instead of alert
              const errorChatMessage = `❌ **Mapping Failed**\n\n${errorMessage}`;
              setChatHistory((prev) => [...prev, {
                id: `error-mapping-${Date.now()}`,
                type: 'system',
                text: errorChatMessage,
                timestamp: Date.now(),
              }]);
              
              // Persist error message
              if (activeConversationId) {
                await persistMessage(activeConversationId, 'assistant', errorChatMessage, {
                  event_type: 'mapping_failed',
                  error: errorMessage,
                });
              }
              
              // Hide loader on error
              endInlineMappingLoader();
            } finally {
              abortControllerRef.current = null;
            }
          }}
          onReExtract={() => {
            // Set pending re-extraction state for UI reference (re-extraction now goes through streaming API)
            const sourceType = previewData.sourceType || 'file';
            
            setPendingReExtract({
              jobId: previewData.jobId,
              sourceType: sourceType,
            });

            // Show prompt asking user for new extraction goals
            const reExtractPrompt = `🔄 **Re-extraction Requested**\n\nPlease enter your new extraction requirements in the chat below. For example:\n\n• "Clean and normalize email addresses"\n• "Remove duplicate records"\n• "Transform date formats to YYYY-MM-DD"\n• "Filter records where status is active"`;
            
            setChatHistory((prev) => [...prev, {
              id: `re-extract-prompt-${Date.now()}`,
              type: 'system',
              text: reExtractPrompt,
              timestamp: Date.now()
            }]);
            
            if (activeConversationId) {
              persistMessage(activeConversationId, 'assistant', reExtractPrompt, {
                event_type: 're_extraction_prompt',
                original_job_id: previewData.jobId,
              });
            }

            // Focus textarea for user input
            if (textareaRef.current) {
              textareaRef.current.focus();
            }
          }}
          isCollapsed={false}
          />
          </>
          );
        })()}
        {!entry.isLoading && !entry.isUnavailable && !previewData && entry.jobId && (
          <div className="preview-placeholder">
            <div className="preview-metadata">
              <p><strong><FiBarChart2 style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> Data Extraction</strong></p>
              {entry.extractionMetadata ? (
                <>
                  <p>Records: {entry.extractionMetadata.record_count || 0}</p>
                  <p>Source: {entry.extractionMetadata.source_name ? `${entry.extractionMetadata.source_name} (${entry.extractionMetadata.source_type || 'Unknown'})` : (entry.extractionMetadata.source_type || 'Unknown')}</p>
                  {entry.extractionMetadata.extraction_version > 1 && (
                    <p>Version: {entry.extractionMetadata.extraction_version}</p>
                  )}
                </>
              ) : (
                <p>Click below to load preview data</p>
              )}
            </div>
            <button
              className="expand-preview-button"
              onClick={async () => {
                if (entry.jobId) {
                  await fetchExtractionData(entry.jobId, entry.id);
                }
              }}
            >
              <FiBarChart2 style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> Click to load data preview
            </button>
          </div>
        )}
      </div>
      </>
    )}

    {/* Show inline transformation preview */}
    {entry.type === 'transformation_preview' && (() => {
      const transformedJobId = entry.jobId || entry.metadata?.transformed_job_id;
      const previewData = transformedJobId ? inlineDataPreviews[transformedJobId] : null;
      const tmNestedRc = entry.metadata?.transformation_metadata?.record_count;
      const transformationPlaceholderRecordCount =
        previewData?.recordCount ??
        previewData?.record_count ??
        (entry.metadata?.record_count != null && entry.metadata.record_count !== ''
          ? Number(entry.metadata.record_count)
          : null) ??
        (tmNestedRc != null && tmNestedRc !== ''
          ? Number(tmNestedRc)
          : null) ??
        (entry.extractionMetadata?.record_count != null &&
        entry.extractionMetadata.record_count !== ''
          ? Number(entry.extractionMetadata.record_count)
          : null) ??
        (entry.extractionMetadata?.total_records != null &&
        entry.extractionMetadata.total_records !== ''
          ? Number(entry.extractionMetadata.total_records)
          : null) ??
        0;

      return (
        <>
          {hasPipelineSummaryForEntry(entry) ? (
            <div className="chat-extraction-pipeline-wrap">
              <PipelineSummaryBlocks entry={entry} />
            </div>
          ) : null}
          <div className="chat-inline-preview chat-inline-preview--extraction">
          {entry.isLoading && (
            <div className="preview-loading">
              <div className="loading-spinner"></div>
              <span>Loading transformed data...</span>
            </div>
          )}
          {entry.isUnavailable && !entry.isLoading && (
            <div className="preview-unavailable">
              <span>⚠️ Preview unavailable</span>
              <p>The transformed data is no longer available.</p>
            </div>
          )}
          {!entry.isLoading && !entry.isUnavailable && previewData && (
            <>
            <InlineDataPreview
              jobId={previewData.jobId}
              data={previewData.data}
              documentType={previewData.documentType}
              documentDescription={previewData.documentDescription}
              recordCount={previewData.recordCount}
              isPaginated={previewData._isPaginated}
              loadedCount={previewData._isPaginated ? (previewData.data?.length ?? 0) : undefined}
              totalRecordCount={previewData._totalCount ?? previewData.recordCount}
              fullRecordCount={previewData._fullTotalCount ?? previewData._totalCount ?? previewData.recordCount}
              apiPageSize={500}
              onFetchPage={fetchExtractionDataPage}
              onDownloadFullCsv={downloadExtractionCsvFull}
              downloadDisabled={!!previewData._downloadDisabled}
              sourceType={previewData.sourceType}
              sourceName={previewData.sourceName}
              destination={previewData.destination}
              dataQualityReport={previewData.dataQualityReport || previewData.data_quality_report}
              onMappingStart={(message) => {
                beginInlineMappingLoader(message);
              }}
              onMappingEnd={() => {
                endInlineMappingLoader();
              }}
              onMapToSalesforce={null} // Transformation preview doesn't support re-mapping
              onReExtract={null} // Transformation preview doesn't support re-extraction
              isCollapsed={false}
              isTransformedData={true}
            />
            </>
          )}
          {!entry.isLoading && !entry.isUnavailable && !previewData && transformedJobId && (
            <div className="preview-placeholder">
              <div className="preview-metadata">
                <p><strong>🔄 Data Transformation</strong></p>
                {entry.metadata && (
                  <>
                    <p>Records: {transformationPlaceholderRecordCount}</p>
                    {entry.metadata.split_fields_summary && Object.keys(entry.metadata.split_fields_summary).length > 0 && (
                      <div>
                        <p><strong>Split fields:</strong></p>
                        <ul>
                          {Object.values(entry.metadata.split_fields_summary).map((sf, idx) => (
                            <li key={idx}>
                              {sf.source_field} → {sf.destination_fields.join(', ')}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </>
                )}
              </div>
              <button
                className="expand-preview-button"
                onClick={async () => {
                  if (transformedJobId) {
                    // Set loading state
                    setChatHistory(prev =>
                      prev.map(e =>
                        e.id === entry.id
                          ? { ...e, isLoading: true, isUnavailable: false }
                          : e
                      )
                    );
                    
                    try {
                      await fetchExtractionData(transformedJobId, entry.id);
                    } catch (error) {
                      console.error('Failed to fetch transformed data:', error);
                      setChatHistory(prev =>
                        prev.map(e =>
                          e.id === entry.id
                            ? { ...e, isLoading: false, isUnavailable: true }
                            : e
                        )
                      );
                    }
                  }
                }}
              >
                <FiBarChart2 style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> Click to load transformed data preview
              </button>
            </div>
          )}
        </div>
        </>
      );
    })()}

    {/* Show upgrade button for plan limit errors */}
    {entry.showUpgradeButton && (
      <div className="upgrade-button-container">
        <button
          onClick={() => window.location.href = '/plans'}
          className="upgrade-button"
        >
          <FiArrowUp style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px', color: '#6366f1' }} /> Upgrade Plan
        </button>
      </div>
    )}

    {fileName && (
      <div className="chat-message-filename">
        <FiPaperclip style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> {fileName}
      </div>
    )}
  </div>
  );
}
