import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiChevronDown, FiChevronUp, FiCheckCircle, FiXCircle, FiClock, FiEye, FiRefreshCw, FiBarChart2, FiSave, FiTrash2 } from 'react-icons/fi';
import { BsSquareFill } from 'react-icons/bs';
import InlineMigrationRecords from './InlineMigrationRecords';
import AlertModal from './AlertModal';
import { useUserTimezone } from '../hooks/useUserTimezone';
import api from '../api';
import { getApiErrorMessage, getApiErrorCode } from '../utils/apiErrorDetail';
import './InlineMigrationStatus.css';

const InlineMigrationStatus = ({
  jobId,
  batchId,
  status,
  progress,
  successCount,
  errorCount,
  totalRecords,
  destination,
  destinationName,
  message,
  error,
  isCollapsed: initialCollapsed = false,
  onViewRecords,
  mapping,
  migrationInstanceURL,
  destinationSpreadsheetId,
  // Save flow props
  firstRecord,
  allRecords,
  documentType,
  documentDescription,
  userPrompt,
  extractedSchemaType,
  sourceConnectorId,
  sourceSqlQuery,
  sourceTableName,
  sourceName,
  destinationConnectorId,
  destinationTableName,
  destinationSheetName,
  fileKey,
  // Flow info props
  flowId,
  flowName: propFlowName,
  conversationId,
  migrationCompletedMessageId,
  onUpdateFlowInfo,
  onLogFlowCreated,
  onLogFlowScheduled,
  onRollbackCompleted,
  onLogExport,
  onCancelMigration, // Callback when user clicks Cancel (parent should call cancelJobs API and stop polling)
  missingFieldPreference, // Optional: missing field preference from mapping_started event
  transformationRules, // Optional: transformation rules from mapping_started event (mapping transformation)
  extractionRules, // Optional: extraction rules from extraction_completed event (extraction stage rules)
  failureSummary, // Optional: compact failure summary from migration_completed metadata
}) => {
  const navigate = useNavigate();
  const [isCollapsed, setIsCollapsed] = useState(initialCollapsed);
  const [records, setRecords] = useState(null);
  const [recordsTotalCount, setRecordsTotalCount] = useState(null);
  const recordsFullTotalRef = useRef(null);
  const [recordsCurrentPage, setRecordsCurrentPage] = useState(1);
  const [batchType, setBatchType] = useState(null);
  const [isLoadingRecords, setIsLoadingRecords] = useState(false);
  const [recordsError, setRecordsError] = useState(null);
  const [showSaveFlowPrompt, setShowSaveFlowPrompt] = useState(false);
  const [flowName, setFlowName] = useState('');
  const [saveFlowStatus, setSaveFlowStatus] = useState('');
  const [isRerunningFlow, setIsRerunningFlow] = useState(false);
  // Local state to store saved flow info immediately after saving (before props update)
  const [savedFlowId, setSavedFlowId] = useState(null);
  const [savedFlowName, setSavedFlowName] = useState(null);
  const [flowDeleted, setFlowDeleted] = useState(false);
  // Schedule inline section state
  const [showScheduleSection, setShowScheduleSection] = useState(false);
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleFrequency, setScheduleFrequency] = useState('daily');
  const [timeOfDay, setTimeOfDay] = useState('09:00');
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [month, setMonth] = useState(1);
  const [yearlyDate, setYearlyDate] = useState(''); // Date string in YYYY-MM-DD format for yearly schedules
  const [existingSchedule, setExistingSchedule] = useState(null);
  const [isSavingSchedule, setIsSavingSchedule] = useState(false);
  const [isLoadingSchedule, setIsLoadingSchedule] = useState(false);
  const { userTimezone } = useUserTimezone();
  const timeInputRef = useRef(null);
  const dateInputRef = useRef(null);
  // Rollback state
  const [isRollingBack, setIsRollingBack] = useState(false);
  const [showRollbackConfirm, setShowRollbackConfirm] = useState(false);
  const [rollbackResult, setRollbackResult] = useState(null);
  const [rollbackError, setRollbackError] = useState(null);
  const [isRollbackCompleted, setIsRollbackCompleted] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [recordsDownloadDisabled, setRecordsDownloadDisabled] = useState(false);
  /** Instance URL from batch records API when migration_completed metadata lacks it */
  const [recordsInstanceURL, setRecordsInstanceURL] = useState(null);
  const [recordsSpreadsheetId, setRecordsSpreadsheetId] = useState(null);
  const [alertModal, setAlertModal] = useState({ isOpen: false, title: '', message: '', type: 'info' });

  const effectiveInstanceURL = migrationInstanceURL || recordsInstanceURL || null;
  const effectiveSpreadsheetId = destinationSpreadsheetId || recordsSpreadsheetId || null;

  const closeAlert = () => setAlertModal({ isOpen: false, title: '', message: '', type: 'info' });

  // Initialize local state from props when component mounts or props change
  // This ensures saved flow info persists after page reload
  useEffect(() => {
    if (flowId && propFlowName) {
      setSavedFlowId(flowId);
      setSavedFlowName(propFlowName);
    }
  }, [flowId, propFlowName]);

  // Reset batch type and records when batch changes
  useEffect(() => {
    setBatchType(null);
    setRecords(null);
    setRecordsTotalCount(null);
    setRecordsCurrentPage(1);
    setRecordsDownloadDisabled(false);
    setRecordsInstanceURL(null);
    setRecordsSpreadsheetId(null);
  }, [batchId]);

  // Check if batch is already rolled back when component mounts
  useEffect(() => {
    const checkBatchStatus = async () => {
      if (batchId && (status === 'completed' || status === 'success' || status === 'cancelled')) {
        try {
          const response = await api.get(`/migration-records/batch/${batchId}/records`, {
            params: { page: 1, page_size: 1 },
          });
          const meta = response.data?.metadata || {};
          if (meta.batch_type) setBatchType(meta.batch_type);
          if (meta.instance_url) setRecordsInstanceURL(meta.instance_url);
          if (meta.spreadsheet_id) setRecordsSpreadsheetId(meta.spreadsheet_id);
          if (meta.status === 'rolled_back') {
            setIsRollbackCompleted(true);
          }
        } catch (error) {
          console.error('Failed to check batch status:', error);
        }
      }
    };
    checkBatchStatus();
  }, [batchId, status]);
  
  // Also check batch status when records are fetched (in case it changes)
  useEffect(() => {
    if (records && records.length > 0 && batchId) {
      // Check batch status from the records response (which now includes batch_status)
      // This is handled in the handleRollback function when refreshing records
    }
  }, [records, batchId]);

  // Fetch schedule information when flow is available
  useEffect(() => {
    const currentFlowId = flowId || savedFlowId;
    if (currentFlowId && status === 'completed') {
      // Fetch schedule info to display next to flow name
      api.get(`/flow/${currentFlowId}/schedule`)
        .then(response => {
          if (response.data.status === 'success' && response.data.schedule) {
            const schedule = response.data.schedule;
            if (schedule.schedule_enabled && schedule.schedule_frequency) {
              setExistingSchedule(schedule);
              setScheduleEnabled(true);
              setScheduleFrequency(schedule.schedule_frequency);
            }
          }
        })
        .catch(error => {
          // Silently fail - schedule might not exist
        });
    }
  }, [flowId, savedFlowId, status]);

  // Initialize yearly date when switching to yearly frequency
  useEffect(() => {
    if (scheduleFrequency === 'yearly' && !yearlyDate) {
      const currentYear = new Date().getFullYear();
      const dateStr = `${currentYear}-${String(month).padStart(2, '0')}-${String(dayOfMonth).padStart(2, '0')}`;
      setYearlyDate(dateStr);
    }
  }, [scheduleFrequency, month, dayOfMonth, yearlyDate]);

  const getStatusIcon = () => {
    if (status === 'completed' || status === 'success') {
      return <FiCheckCircle className="status-icon success" />;
    } else if (status === 'cancelled') {
      return <BsSquareFill className="status-icon" style={{ color: '#f59e0b' }} />;
    } else if (status === 'failed' || status === 'error') {
      return <FiXCircle className="status-icon error" />;
    } else {
      return <FiClock className="status-icon processing" />;
    }
  };

  const getStatusText = () => {
    if (status === 'completed' || status === 'success') {
      return 'Migration Completed';
    } else if (status === 'cancelled') {
      return 'Migration Cancelled';
    } else if (status === 'failed' || status === 'error') {
      return 'Migration Failed';
    } else {
      return 'Migration In Progress';
    }
  };

  const getStatusColor = () => {
    if (status === 'completed' || status === 'success') {
      return '#10b981';
    } else if (status === 'cancelled') {
      return '#f59e0b';
    } else if (status === 'failed' || status === 'error') {
      return '#ef4444';
    } else {
      return '#6366f1';
    }
  };

  const handleCancelMigration = async () => {
    if (!jobId || isCancelling) return;
    setIsCancelling(true);
    try {
      if (onCancelMigration) {
        await onCancelMigration(jobId);
      }
    } finally {
      setIsCancelling(false);
    }
  };

  // Prefer a consistent displayed total:
  // backend may report more "successful" than batch total when lookup parents are included
  const computedTotalRecords = Math.max(
    totalRecords ?? 0,
    (successCount ?? 0) + (errorCount ?? 0)
  );

  const hasPartialFailures = (errorCount ?? 0) > 0;
  const failureTopErrors = failureSummary?.top_errors || [];
  const failureClusters = failureSummary?.clusters || [];
  const failureNotice = (failureSummary?.unrecoverable_notice || '').trim();

  const renderFailureSummary = () => {
    if (!hasPartialFailures) return null;

    const clusterItems =
      failureClusters.length > 0
        ? failureClusters.slice(0, 5).map((cluster, idx) => ({
            key: cluster.theme || idx,
            theme: cluster.theme || 'Errors',
            count: cluster.count || 1,
            message: cluster.sample_message || '',
          }))
        : failureTopErrors.slice(0, 5).map((err, idx) => ({
            key: err.message || idx,
            theme: 'Migration error',
            count: err.count || 1,
            message: err.message || '',
          }));

    if (!clusterItems.length && !failureNotice) return null;

    return (
      <div className="inline-migration-failure-summary">
        <p className="inline-migration-failure-summary-title">
          {failureSummary?.recoverable_count > 0
            ? `${failureSummary.recoverable_count} record${failureSummary.recoverable_count === 1 ? '' : 's'} may be fixable via mapping`
            : `${errorCount} record${errorCount === 1 ? '' : 's'} failed`}
        </p>
        {failureNotice ? (
          <p className="inline-migration-failure-summary-notice">{failureNotice}</p>
        ) : null}
        {clusterItems.length > 0 ? (
          <ul className="inline-migration-failure-summary-list">
            {clusterItems.map((item) => (
              <li key={item.key}>
                <strong>{item.theme}</strong>
                {item.count > 1 ? ` (${item.count})` : ''}
                {item.message ? (
                  <span className="inline-migration-failure-sample"> — {item.message}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    );
  };

  const isMetadataDeployment =
    batchType === 'metadata' ||
    (typeof message === 'string' && message.toLowerCase().includes('metadata deploy'));

  const displayedMessage = (() => {
    if (!message) return message;
    if (!isMetadataDeployment) return message;
    if (typeof message !== 'string') return message;
    // For metadata deployments, backend message can include a noisy tail:
    // "Batch ID: ... Track progress and view created/updated/error fields in Migration records.."
    // Remove everything from "Batch ID:" onward for cleaner UI.
    return message.replace(/\s*Batch ID:.*$/i, '').trim();
  })();

  const handleSaveFlow = async () => {
    if (!flowName.trim()) {
      setSaveFlowStatus('❌ Please enter a flow name');
      setTimeout(() => setSaveFlowStatus(''), 3000);
      return;
    }

    if (!conversationId) {
      setSaveFlowStatus('❌ Save flow requires an active conversation');
      setTimeout(() => setSaveFlowStatus(''), 5000);
      return;
    }

    try {
      setSaveFlowStatus('Saving flow...');

      const response = await api.post(
        `/conversations/${conversationId}/actions/save-flow`,
        {
          flow_name: flowName.trim(),
          batch_id: batchId || undefined,
        },
      );
      const savedFlowId = response.data?.flow_id || response.data?.result?.flow_id;
      const savedFlowName = flowName.trim();
      setSavedFlowId(savedFlowId);
      setSavedFlowName(savedFlowName);
      if (migrationCompletedMessageId && savedFlowId) {
        try {
          await api.put(`/chat/conversations/${conversationId}/messages/${migrationCompletedMessageId}/metadata`, {
            metadata: { flow_id: savedFlowId, flow_name: savedFlowName },
          });
          onUpdateFlowInfo?.(savedFlowId, savedFlowName);
        } catch (updateError) {
          console.error('Failed to update migration_completed metadata:', updateError);
        }
      }
      setSaveFlowStatus('✅ Flow saved successfully!');
      setShowSaveFlowPrompt(false);
      setFlowName('');
      setFlowDeleted(false);
      setTimeout(() => setSaveFlowStatus(''), 3000);
    } catch (err) {
      console.error('Save flow error:', err);
      const errorMessage = err.response?.data?.detail || err.response?.data?.message || err.message || 'Unknown error';
      setSaveFlowStatus(`❌ Failed to save flow: ${errorMessage}`);
      setTimeout(() => setSaveFlowStatus(''), 5000);
    }
  };

  const handleRerunFlow = async () => {
    const currentFlowId = flowId || savedFlowId;
    if (!currentFlowId) return;
    
    setIsRerunningFlow(true);
    try {
      const response = await api.post('/flow/reuse', {
        flow_id: currentFlowId,
        user_prompt: userPrompt || ''
      });

      const data = response.data || {};
      const action = data.action || '';
      const destinationLabel = data.destination_name || data.destination_type || 'destination';

      if (action === 'sql_rerun' && data.job_id) {
        navigate('/data-table', {
          state: {
            job_id: data.job_id,
            source: 'sql',
            message: data.message || `SQL flow re-extraction started for ${destinationLabel}`,
            isReusableFlow: true
          }
        });
        return;
      }

      if (action === 'gsheets_rerun' && data.job_id) {
        navigate(`/data-table/${data.job_id}`, {
          state: {
            job_id: data.job_id,
            source: 'gsheets',
            message: data.message || `Google Sheets flow re-extraction started for ${destinationLabel}`,
            isReusableFlow: true
          }
        });
        return;
      }

      if (action === 'salesforce_rerun') {
        const jobId = data.job_id;
        navigate(`/data-table/${jobId}`, {
          state: {
            source: 'salesforce',
            message: data.message || `Salesforce flow extraction started for ${destinationLabel}`,
            isReusableFlow: true
          }
        });
        return;
      }

      if (action === 'file_upload' || data.status === 'pending_upload') {
        // For file flows, user needs to upload a file
        // Navigate to flows page where they can use the flow
        navigate('/flows');
        return;
      }

      throw new Error(data.message || 'Unsupported flow type');
    } catch (error) {
      console.error('Error rerunning flow:', error);

      const code = getApiErrorCode(error);
      const msg = getApiErrorMessage(error);

      // 404 with structured code: distinguish missing flow vs disconnected Google Sheet
      if (error.response?.status === 404 && code === 'GOOGLE_SHEET_NOT_LINKED') {
        setSaveFlowStatus(`❌ ${msg}`);
        setTimeout(() => setSaveFlowStatus(''), 12000);
      } else if (error.response?.status === 404) {
        setFlowDeleted(true);
        setSavedFlowId(null);
        setSavedFlowName(null);
        setShowSaveFlowPrompt(true);
        setSaveFlowStatus(
          code === 'FLOW_NOT_FOUND'
            ? '⚠️ This flow is no longer available. You can save the current mapping as a new flow.'
            : '⚠️ Looks like this flow was deleted or is unavailable. You can save it as a new flow.'
        );
        setTimeout(() => setSaveFlowStatus(''), 10000);
      } else {
        setSaveFlowStatus(`❌ Failed to rerun flow: ${msg}`);
        setTimeout(() => setSaveFlowStatus(''), 5000);
      }
    } finally {
      setIsRerunningFlow(false);
    }
  };

  const handleViewFlow = () => {
    // Navigate to flows page
    navigate('/flows');
  };

  const handleScheduleFlowClick = async () => {
    const currentFlowId = flowId || savedFlowId;
    if (!currentFlowId) return;

    // Toggle section
    if (showScheduleSection) {
      setShowScheduleSection(false);
      return;
    }

    setShowScheduleSection(true);
    setIsLoadingSchedule(true);

    try {
      // Fetch current schedule
      const response = await api.get(`/flow/${currentFlowId}/schedule`);
      if (response.data.status === 'success' && response.data.schedule) {
        const schedule = response.data.schedule;
        setExistingSchedule(schedule);
        setScheduleEnabled(schedule.schedule_enabled || false);
        setScheduleFrequency(schedule.schedule_frequency || 'daily');
        
        const config = schedule.schedule_config || {};
        if (config.time_of_day) {
          setTimeOfDay(config.time_of_day);
        }
        if (config.day_of_month) {
          setDayOfMonth(config.day_of_month);
        }
        if (config.month) {
          setMonth(config.month);
          // Construct date string for yearly date picker (use current year)
          if (config.month && config.day_of_month) {
            const currentYear = new Date().getFullYear();
            const dateStr = `${currentYear}-${String(config.month).padStart(2, '0')}-${String(config.day_of_month).padStart(2, '0')}`;
            setYearlyDate(dateStr);
          }
        }
      } else {
        // No schedule exists, use defaults
        setExistingSchedule(null);
        setScheduleEnabled(false);
        setScheduleFrequency('daily');
        setTimeOfDay('09:00');
        setDayOfMonth(1);
        setMonth(1);
        const currentYear = new Date().getFullYear();
        setYearlyDate(`${currentYear}-01-01`);
      }
    } catch (error) {
      console.error('Error fetching schedule:', error);
      // Use defaults if fetch fails
      setExistingSchedule(null);
      setScheduleEnabled(false);
      setScheduleFrequency('daily');
      setTimeOfDay('09:00');
      setDayOfMonth(1);
      setMonth(1);
      const currentYear = new Date().getFullYear();
      setYearlyDate(`${currentYear}-01-01`);
    } finally {
      setIsLoadingSchedule(false);
    }
  };

  const handleScheduleSave = async () => {
    const currentFlowId = flowId || savedFlowId;
    if (!currentFlowId) return;

    // Determine schedule_action by comparing previous and new state
    const previousEnabled = existingSchedule?.schedule_enabled || false;
    const previousFrequency = existingSchedule?.schedule_frequency || null;
    let schedule_action = 'updated';
    
    // Check if schedule truly exists (has been enabled before) by checking if schedule_enabled was ever true
    // If existingSchedule is null OR if schedule_enabled is explicitly false, and we're enabling it, it's "created"
    if ((!existingSchedule || !previousEnabled) && scheduleEnabled) {
      // Schedule enabled for the first time (never existed or was disabled)
      schedule_action = 'created';
    } else if (previousEnabled && scheduleEnabled) {
      // Schedule already exists and is being modified
      if (previousFrequency !== scheduleFrequency) {
        schedule_action = 'updated';
      } else {
        // Frequency same, might be time/config change - still consider it updated
        schedule_action = 'updated';
      }
    } else if (previousEnabled && !scheduleEnabled) {
      // Schedule being disabled via Save button
      schedule_action = 'disabled';
    } else if (!previousEnabled && !scheduleEnabled) {
      // Already disabled, no action needed (don't log)
      schedule_action = null;
    }

    setIsSavingSchedule(true);
    try {
      const scheduleData = {
        schedule_enabled: scheduleEnabled,
        schedule_frequency: scheduleEnabled ? scheduleFrequency : null,
        time_of_day: scheduleEnabled && scheduleFrequency !== 'hourly' ? timeOfDay : null,
        day_of_month: scheduleEnabled && (scheduleFrequency === 'monthly' || scheduleFrequency === 'yearly') ? dayOfMonth : null,
        month: scheduleEnabled && scheduleFrequency === 'yearly' ? month : null,
        timezone: scheduleEnabled ? userTimezone : null
      };

      const response = await api.post(`/flow/${currentFlowId}/schedule`, scheduleData);
      if (response.data.status === 'success') {
        setSaveFlowStatus('✅ Schedule updated successfully');
        // Refresh schedule data
        const scheduleResponse = await api.get(`/flow/${currentFlowId}/schedule`);
        if (scheduleResponse.data.status === 'success') {
          setExistingSchedule(scheduleResponse.data.schedule);
        }
        
        // Log flow_scheduled event (only if action occurred)
        if (onLogFlowScheduled && schedule_action) {
          try {
            // Use same source name logic as flow_created
            let sourceNameDisplay = sourceName || (extractedSchemaType === 'gsheets' ? sourceTableName : null);
            if (!sourceNameDisplay) {
              if (extractedSchemaType === 'sql' && sourceTableName) {
                sourceNameDisplay = sourceTableName;
              } else if (extractedSchemaType === 'gsheets' && sourceTableName) {
                sourceNameDisplay = sourceTableName;
              } else if (extractedSchemaType === 'file' && fileKey) {
                sourceNameDisplay = fileKey.split('/').pop() || 'File';
              } else {
                sourceNameDisplay = 'File';
              }
            }
            
            const destinationNameDisplay = destinationName || destination || 'Salesforce';
            
            onLogFlowScheduled({
              flow_id: currentFlowId,
              flow_name: propFlowName || savedFlowName,
              source_type: extractedSchemaType || 'file',
              source_name: sourceNameDisplay,
              destination_type: destination || 'salesforce',
              destination_name: destinationNameDisplay,
              job_id: jobId,
              batch_id: batchId,
              schedule_frequency: scheduleEnabled ? scheduleFrequency : null,
              schedule_enabled: scheduleEnabled,
              schedule_timezone: scheduleEnabled ? userTimezone : null,
              schedule_action: schedule_action
            });
          } catch (logError) {
            console.error('Failed to log flow_scheduled event:', logError);
            // Don't fail the save operation if logging fails
          }
        }
        
        setTimeout(() => setSaveFlowStatus(''), 3000);
      } else {
        setSaveFlowStatus('❌ Failed to update schedule');
        setTimeout(() => setSaveFlowStatus(''), 5000);
      }
    } catch (error) {
      console.error('Error saving schedule:', error);
      setSaveFlowStatus(`❌ Failed to save schedule: ${error.response?.data?.detail || error.message}`);
      setTimeout(() => setSaveFlowStatus(''), 5000);
    } finally {
      setIsSavingSchedule(false);
    }
  };

  const handleRollback = async () => {
    if (!batchId) return;
    
    setIsRollingBack(true);
    setRollbackError(null);
    setRollbackResult(null);
    
    try {
      const response = await api.post(`/migration-records/rollback/${batchId}`);
      const result = response.data;
      
      setRollbackResult(result);
      
      // Update status if rollback was successful
      if (result.status === 'success' || result.status === 'partial_success') {
        // Mark rollback as completed to disable the button
        setIsRollbackCompleted(true);
        
        // Refresh migration records to show updated statuses (paginated)
        if (batchId) {
          try {
            const recordsResponse = await api.get(`/migration-records/batch/${batchId}/records`, {
              params: { page: 1, page_size: 500 },
            });
            const data = recordsResponse.data;
            const fetchedRecords = data?.records || [];
            const meta = data?.metadata || {};
            if (meta.batch_type) setBatchType(meta.batch_type);
            setRecords(fetchedRecords);
            const refreshedTotal = data?.total_count ?? fetchedRecords.length;
            recordsFullTotalRef.current = refreshedTotal;
            setRecordsTotalCount(refreshedTotal);
            if (meta.status === 'rolled_back') {
              setIsRollbackCompleted(true);
            }
          } catch (error) {
            console.error('Failed to refresh migration records:', error);
          }
        }
        
        // Send rollback message to chat via callback (adds to chat history immediately)
        if (onRollbackCompleted && result.rollback_message) {
          try {
            onRollbackCompleted({
              rollback_message: result.rollback_message,
              batch_id: batchId,
              job_id: jobId,
              deleted_records: result.deleted_records,
              failed_records: result.failed_records,
              total_records: result.total_records,
              source_type: result.source_type,
              source_name: result.source_name,
              destination: destination,
              destination_name: destinationName,
            });
          } catch (error) {
            console.error('Failed to send rollback message to chat:', error);
            // Don't fail the rollback operation if message sending fails
          }
        }
        
        // Don't close dialog immediately - let user see the result
        // Close after a delay or when user clicks close
        setTimeout(() => {
          setShowRollbackConfirm(false);
        }, 5000); // Close after 5 seconds
      }
    } catch (error) {
      console.error('Rollback failed:', error);
      setRollbackError(error.response?.data?.detail || error.response?.data?.message || 'Rollback failed');
    } finally {
      setIsRollingBack(false);
    }
  };

  const handleRollbackClick = () => {
    if (isRollbackCompleted) return; // Don't allow rollback if already completed
    setShowRollbackConfirm(true);
    setRollbackError(null);
    setRollbackResult(null);
  };

  const handleRollbackCancel = () => {
    setShowRollbackConfirm(false);
    setRollbackError(null);
    setRollbackResult(null);
  };

  const handleScheduleDisable = async () => {
    const currentFlowId = flowId || savedFlowId;
    if (!currentFlowId) return;

    setIsSavingSchedule(true);
    try {
      const scheduleData = {
        schedule_enabled: false,
        schedule_frequency: null,
        time_of_day: null,
        day_of_month: null,
        month: null,
        timezone: null
      };

      const response = await api.post(`/flow/${currentFlowId}/schedule`, scheduleData);
      if (response.data.status === 'success') {
        setSaveFlowStatus('✅ Schedule disabled successfully');
        setScheduleEnabled(false);
        
        // Store previous frequency before clearing
        const previousFrequency = existingSchedule?.schedule_frequency || scheduleFrequency;
        setExistingSchedule(null);
        
        // Log flow_scheduled event with 'disabled' action
        if (onLogFlowScheduled) {
          try {
            // Use same source name logic as flow_created
            let sourceNameDisplay = sourceName || (extractedSchemaType === 'gsheets' ? sourceTableName : null);
            if (!sourceNameDisplay) {
              if (extractedSchemaType === 'sql' && sourceTableName) {
                sourceNameDisplay = sourceTableName;
              } else if (extractedSchemaType === 'gsheets' && sourceTableName) {
                sourceNameDisplay = sourceTableName;
              } else if (extractedSchemaType === 'file' && fileKey) {
                sourceNameDisplay = fileKey.split('/').pop() || 'File';
              } else {
                sourceNameDisplay = 'File';
              }
            }
            
            const destinationNameDisplay = destinationName || destination || 'Salesforce';
            
            onLogFlowScheduled({
              flow_id: currentFlowId,
              flow_name: propFlowName || savedFlowName,
              source_type: extractedSchemaType || 'file',
              source_name: sourceNameDisplay,
              destination_type: destination || 'salesforce',
              destination_name: destinationNameDisplay,
              job_id: jobId,
              batch_id: batchId,
              schedule_frequency: previousFrequency,
              schedule_enabled: false,
              schedule_timezone: null,
              schedule_action: 'disabled'
            });
          } catch (logError) {
            console.error('Failed to log flow_scheduled event:', logError);
            // Don't fail the disable operation if logging fails
          }
        }
        
        setTimeout(() => setSaveFlowStatus(''), 3000);
      } else {
        setSaveFlowStatus('❌ Failed to disable schedule');
        setTimeout(() => setSaveFlowStatus(''), 5000);
      }
    } catch (error) {
      console.error('Error disabling schedule:', error);
      setSaveFlowStatus(`❌ Failed to disable schedule: ${error.response?.data?.detail || error.message}`);
      setTimeout(() => setSaveFlowStatus(''), 5000);
    } finally {
      setIsSavingSchedule(false);
    }
  };

  if (!jobId && !status) return null;

  return (
    <>
    <div className="inline-migration-status">
      <div
        className="inline-migration-status-header"
        onClick={() => setIsCollapsed(!isCollapsed)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setIsCollapsed(!isCollapsed);
          }
        }}
      >
        <div className="inline-migration-status-title">
          {getStatusIcon()}
          <span>
            <strong>{getStatusText()}</strong>
            {destinationName && (
              <span className="inline-migration-status-destination"> → {destinationName}</span>
            )}
          </span>
        </div>
        <div className="inline-migration-status-toggle">
          {isCollapsed ? <FiChevronDown /> : <FiChevronUp />}
        </div>
      </div>

      {!isCollapsed && (
        <div className="inline-migration-status-content">
          {status === 'processing' || status === 'in_progress' ? (
            <>
              <div className="inline-migration-progress-bar">
                <div
                  className="inline-migration-progress-fill"
                  style={{
                    width: `${progress || 0}%`,
                    backgroundColor: getStatusColor(),
                  }}
                />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                <div className="inline-migration-progress-text">
                  {(progress || 0).toFixed(2)}% complete
                </div>
                {jobId && onCancelMigration && (
                  <button
                    onClick={handleCancelMigration}
                    disabled={isCancelling}
                    style={{
                      padding: '4px 12px',
                      fontSize: '13px',
                      backgroundColor: '#f59e0b',
                      color: 'white',
                      border: 'none',
                      borderRadius: '6px',
                      cursor: isCancelling ? 'not-allowed' : 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      fontWeight: '500',
                      opacity: isCancelling ? 0.6 : 1,
                    }}
                  >
                    <BsSquareFill size={14} />
                    {isCancelling ? 'Cancelling...' : 'Cancel'}
                  </button>
                )}
              </div>
              {displayedMessage && (
                <div className="inline-migration-message">{displayedMessage}</div>
              )}
            </>
          ) : status === 'completed' || status === 'success' || status === 'cancelled' ? (
            <> 
              {(displayedMessage || status === 'cancelled' || hasPartialFailures) && (
                <div className={`inline-migration-message ${status === 'cancelled' ? 'cancelled' : hasPartialFailures ? 'partial' : 'success'}`}>
                  {hasPartialFailures && !displayedMessage
                    ? `Migration completed with ${errorCount} failed record${errorCount === 1 ? '' : 's'}.`
                    : (displayedMessage || 'Partial progress saved. You can rollback migrated records if needed.')}
                </div>
              )}
              <div className="inline-migration-results">
                <div className="inline-migration-result-item success">
                  <FiCheckCircle />
                  <span>
                    <strong>{successCount != null ? successCount : (totalRecords || 0)}</strong> successful
                  </span>
                </div>
                {(errorCount != null ? errorCount : 0) > 0 && (
                  <div className="inline-migration-result-item error">
                    <FiXCircle />
                    <span>
                      <strong>{errorCount != null ? errorCount : 0}</strong> failed
                    </span>
                  </div>
                )}
                <div className="inline-migration-result-item total">
                  <span>
                    <strong>{computedTotalRecords}</strong> total records
                  </span>
                </div>
              </div>

              {renderFailureSummary()}
              
              {/* Flow Section - hidden for cancelled migrations */}
              {!isMetadataDeployment && status !== 'cancelled' && ((flowId || savedFlowId) && (propFlowName || savedFlowName) && !flowDeleted) ? (
                // Flow already exists and is not deleted - show flow name and action buttons
                <div className="inline-migration-flow-info" style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #e5e7eb' }}>
                  <div style={{ marginBottom: '12px' }}>
                    <span style={{ fontWeight: '600', color: '#065f46' }}>Flow: </span>
                    <span style={{ color: '#374151' }}>{propFlowName || savedFlowName}</span>
                    {existingSchedule?.schedule_enabled && existingSchedule?.schedule_frequency && (
                      <span style={{ 
                        marginLeft: '8px', 
                        padding: '2px 8px', 
                        backgroundColor: '#eff6ff', 
                        color: '#1e40af', 
                        borderRadius: '4px', 
                        fontSize: '12px',
                        fontWeight: '500'
                      }}>
                        <FiClock style={{ display: 'inline', marginRight: '4px', verticalAlign: 'middle' }} />
                        {existingSchedule.schedule_frequency.charAt(0).toUpperCase() + existingSchedule.schedule_frequency.slice(1)}
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button
                      className="view-flow-button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleViewFlow();
                      }}
                      style={{
                        padding: '8px 16px',
                        backgroundColor: '#6366f1',
                        color: 'white',
                        border: 'none',
                        borderRadius: '6px',
                        cursor: 'pointer',
                        fontSize: '14px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        fontWeight: '500'
                      }}
                    >
                      <FiEye /> View Flow
                    </button>
                    <button
                      className="rerun-flow-button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRerunFlow();
                      }}
                      disabled={isRerunningFlow}
                      style={{
                        padding: '8px 16px',
                        backgroundColor: '#10b981',
                        color: 'white',
                        border: 'none',
                        borderRadius: '6px',
                        cursor: isRerunningFlow ? 'not-allowed' : 'pointer',
                        fontSize: '14px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        fontWeight: '500',
                        opacity: isRerunningFlow ? 0.6 : 1
                      }}
                    >
                      <FiRefreshCw style={{ animation: isRerunningFlow ? 'spin 1s linear infinite' : 'none' }} /> 
                      {isRerunningFlow ? 'Running...' : 'Run Flow'}
                    </button>
                    {extractedSchemaType !== 'file' && (
                      <button
                        className="schedule-flow-button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleScheduleFlowClick();
                        }}
                        style={{
                          padding: '8px 16px',
                          backgroundColor: showScheduleSection ? '#2563eb' : '#3b82f6',
                          color: 'white',
                          border: 'none',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontSize: '14px',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          fontWeight: '500'
                        }}
                      >
                        <FiClock /> {showScheduleSection ? 'Hide Schedule' : 'Schedule'}
                      </button>
                    )}
                  </div>
                  
                  {/* Inline Schedule Section */}
                  {showScheduleSection && extractedSchemaType !== 'file' && (
                    <div className="inline-schedule-section" style={{
                      marginTop: '16px',
                      padding: '16px',
                      backgroundColor: '#f9fafb',
                      border: '1px solid #e5e7eb',
                      borderRadius: '8px'
                    }}>
                      {isLoadingSchedule ? (
                        <div style={{ textAlign: 'center', padding: '20px', color: '#6b7280' }}>
                          Loading schedule...
                        </div>
                      ) : (
                        <>
                          <div style={{ marginBottom: '16px' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                              <input
                                type="checkbox"
                                checked={scheduleEnabled}
                                onChange={(e) => setScheduleEnabled(e.target.checked)}
                                disabled={isSavingSchedule}
                                onClick={(e) => e.stopPropagation()}
                              />
                              <span style={{ fontWeight: '500', color: '#374151' }}>Enable scheduled execution</span>
                            </label>
                          </div>

                          {scheduleEnabled && (
                            <>
                              <div style={{ marginBottom: '12px' }}>
                                <label style={{ display: 'block', marginBottom: '4px', fontSize: '14px', fontWeight: '500', color: '#374151' }}>
                                  Frequency *
                                </label>
                                <select
                                  value={scheduleFrequency}
                                  onChange={(e) => setScheduleFrequency(e.target.value)}
                                  disabled={isSavingSchedule}
                                  onClick={(e) => e.stopPropagation()}
                                  style={{
                                    width: '100%',
                                    padding: '8px 12px',
                                    border: '1px solid #d1d5db',
                                    borderRadius: '6px',
                                    fontSize: '14px',
                                    backgroundColor: 'white'
                                  }}
                                >
                                  <option value="hourly">Hourly</option>
                                  <option value="daily">Daily</option>
                                  <option value="monthly">Monthly</option>
                                  <option value="yearly">Yearly</option>
                                </select>
                              </div>

                              {scheduleFrequency !== 'hourly' && (
                                <div style={{ marginBottom: '12px' }}>
                                  <label style={{ display: 'block', marginBottom: '4px', fontSize: '14px', fontWeight: '500', color: '#374151' }}>
                                    Time of Day *
                                  </label>
                                  <input
                                    ref={timeInputRef}
                                    type="time"
                                    value={timeOfDay}
                                    onChange={(e) => setTimeOfDay(e.target.value)}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      // Open time picker when clicking anywhere on the input field
                                      if (timeInputRef.current && !isSavingSchedule) {
                                        timeInputRef.current.focus();
                                        if (timeInputRef.current.showPicker) {
                                          try {
                                            timeInputRef.current.showPicker();
                                          } catch (error) {
                                          }
                                        }
                                      }
                                    }}
                                    disabled={isSavingSchedule}
                                    style={{
                                      width: '100%',
                                      padding: '8px 12px',
                                      border: '1px solid #d1d5db',
                                      borderRadius: '6px',
                                      fontSize: '14px',
                                      backgroundColor: 'white',
                                      boxSizing: 'border-box'
                                    }}
                                  />
                                </div>
                              )}

                              {scheduleFrequency === 'monthly' && (
                                <div style={{ marginBottom: '12px' }}>
                                  <label style={{ display: 'block', marginBottom: '4px', fontSize: '14px', fontWeight: '500', color: '#374151' }}>
                                    Day of Month *
                                  </label>
                                  <select
                                    value={dayOfMonth}
                                    onChange={(e) => setDayOfMonth(parseInt(e.target.value))}
                                    disabled={isSavingSchedule}
                                    onClick={(e) => e.stopPropagation()}
                                    style={{
                                      width: '100%',
                                      padding: '8px 12px',
                                      border: '1px solid #d1d5db',
                                      borderRadius: '6px',
                                      fontSize: '14px',
                                      backgroundColor: 'white',
                                      boxSizing: 'border-box'
                                    }}
                                  >
                                    {Array.from({ length: 31 }, (_, i) => i + 1).map(day => (
                                      <option key={day} value={day}>{day}</option>
                                    ))}
                                  </select>
                                </div>
                              )}

                              {scheduleFrequency === 'yearly' && (
                                <div style={{ marginBottom: '12px' }}>
                                  <label style={{ display: 'block', marginBottom: '4px', fontSize: '14px', fontWeight: '500', color: '#374151' }}>
                                    Date *
                                  </label>
                                  <input
                                    ref={dateInputRef}
                                    type="date"
                                    value={yearlyDate || (() => {
                                      const currentYear = new Date().getFullYear();
                                      return `${currentYear}-${String(month).padStart(2, '0')}-${String(dayOfMonth).padStart(2, '0')}`;
                                    })()}
                                    onChange={(e) => {
                                      const selectedDate = e.target.value;
                                      setYearlyDate(selectedDate);
                                      if (selectedDate) {
                                        // Parse date string directly to avoid timezone issues
                                        const [, monthStr, dayStr] = selectedDate.split('-');
                                        const monthNum = parseInt(monthStr, 10); // 1-12
                                        const day = parseInt(dayStr, 10); // 1-31
                                        setMonth(monthNum);
                                        setDayOfMonth(day);
                                      }
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      // Open date picker when clicking anywhere on the input field
                                      if (dateInputRef.current && !isSavingSchedule) {
                                        dateInputRef.current.focus();
                                        if (dateInputRef.current.showPicker) {
                                          try {
                                            dateInputRef.current.showPicker();
                                          } catch (error) {
                                          }
                                        }
                                      }
                                    }}
                                    disabled={isSavingSchedule}
                                    min={`${new Date().getFullYear()}-01-01`}
                                    max={`${new Date().getFullYear()}-12-31`}
                                    style={{
                                      width: '100%',
                                      padding: '8px 12px',
                                      border: '1px solid #d1d5db',
                                      borderRadius: '6px',
                                      fontSize: '14px',
                                      backgroundColor: 'white',
                                      boxSizing: 'border-box'
                                    }}
                                  />
                                  <div style={{ marginTop: '4px', fontSize: '12px', color: '#6b7280' }}>
                                    Select a date that will repeat yearly
                                  </div>
                                </div>
                              )}
                            </>
                          )}

                          <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '16px' }}>
                            {existingSchedule?.schedule_enabled && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleScheduleDisable();
                                }}
                                disabled={isSavingSchedule}
                                style={{
                                  padding: '8px 16px',
                                  backgroundColor: '#ef4444',
                                  color: 'white',
                                  border: 'none',
                                  borderRadius: '6px',
                                  cursor: isSavingSchedule ? 'not-allowed' : 'pointer',
                                  fontSize: '14px',
                                  fontWeight: '500',
                                  opacity: isSavingSchedule ? 0.6 : 1
                                }}
                              >
                                Disable
                              </button>
                            )}
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleScheduleSave();
                              }}
                              disabled={isSavingSchedule || (scheduleEnabled && !scheduleFrequency)}
                              style={{
                                padding: '8px 16px',
                                backgroundColor: '#3b82f6',
                                color: 'white',
                                border: 'none',
                                borderRadius: '6px',
                                cursor: (isSavingSchedule || (scheduleEnabled && !scheduleFrequency)) ? 'not-allowed' : 'pointer',
                                fontSize: '14px',
                                fontWeight: '500',
                                opacity: (isSavingSchedule || (scheduleEnabled && !scheduleFrequency)) ? 0.6 : 1
                              }}
                            >
                              {isSavingSchedule ? 'Saving...' : 'Save Schedule'}
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                  
                  {saveFlowStatus && (
                    <div className="save-flow-status" style={{
                      marginTop: '8px',
                      fontSize: '13px',
                      color: saveFlowStatus.includes('✅') ? '#065f46' : saveFlowStatus.includes('❌') ? '#991b1b' : '#374151'
                    }}>
                      {saveFlowStatus}
                    </div>
                  )}
                </div>
              ) : !isMetadataDeployment && status !== 'cancelled' && flowDeleted && !showSaveFlowPrompt ? (
                // Flow was deleted - show message and save flow button
                <div className="inline-migration-save-flow" style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #e5e7eb' }}>
                  <div style={{ marginBottom: '12px', padding: '12px', backgroundColor: '#fef3c7', border: '1px solid #fbbf24', borderRadius: '6px', color: '#92400e' }}>
                    <strong>⚠️ Flow Deleted</strong>
                    <p style={{ margin: '8px 0 0 0', fontSize: '14px' }}>This flow was deleted. You can save it as a new flow to run it again.</p>
                  </div>
                  <button
                    className="save-flow-toggle-button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowSaveFlowPrompt(true);
                      setFlowDeleted(false); // Reset deleted state when user saves
                    }}
                  >
                    <FiSave style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> Save as Flow
                  </button>
                </div>
              ) : !isMetadataDeployment && status !== 'cancelled' && !showSaveFlowPrompt ? (
                // No flow exists - show save flow button
                <div className="inline-migration-save-flow" style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #e5e7eb' }}>
                  <button
                    className="save-flow-toggle-button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowSaveFlowPrompt(true);
                    }}
                  >
                    <FiSave style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> Save as Flow
                  </button>
                </div>
              ) : (
                // Save flow prompt is showing
                !isMetadataDeployment && status !== 'cancelled' ? (
                <div className="inline-migration-save-flow" style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #e5e7eb' }}>
                  {showSaveFlowPrompt && (
                    <div className="save-flow-section" style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #e5e7eb' }}>
                  <div className="save-flow-content">
                    <input
                      type="text"
                      placeholder="Name this flow"
                      value={flowName}
                      onChange={(e) => setFlowName(e.target.value)}
                      className="flow-name-input"
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          handleSaveFlow();
                        } else if (e.key === 'Escape') {
                          e.preventDefault();
                          setShowSaveFlowPrompt(false);
                          setFlowName('');
                          setSaveFlowStatus('');
                        }
                      }}
                    />
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleSaveFlow();
                      }}
                      className="save-flow-button"
                      disabled={!flowName.trim() || saveFlowStatus.includes('Saving')}
                    >
                      <FiSave style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} /> Save Flow
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowSaveFlowPrompt(false);
                        setFlowName('');
                        setSaveFlowStatus('');
                        setFlowDeleted(false); // Reset deleted state when canceling
                      }}
                      className="save-flow-cancel-button"
                    >
                      Cancel
                    </button>
                  </div>
                  {saveFlowStatus && (
                    <div className="save-flow-status" style={{
                      marginTop: '8px',
                      fontSize: '13px',
                      color: saveFlowStatus.includes('✅') ? '#065f46' : saveFlowStatus.includes('❌') ? '#991b1b' : '#374151'
                    }}>
                      {saveFlowStatus}
                    </div>
                  )}
                    </div>
                  )}
                </div>
                ) : null
              )}
              
              {batchId && (
                <div style={{ marginTop: '12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <div className="inline-migration-actions" style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  <button
                    className="view-records-button"
                    onClick={async (e) => {
                      e.stopPropagation();
                      
                      // If records already loaded, just toggle display
                      if (records) {
                        return;
                      }
                      
                      // Fetch records via paginated API (scalable for large batches)
                      setIsLoadingRecords(true);
                      setRecordsError(null);
                      
                      try {
                        const response = await api.get(`/migration-records/batch/${batchId}/records`, {
                          params: { page: 1, page_size: 500 },
                        });
                        const data = response.data;
                        const fetchedRecords = data?.records || [];
                        const totalCount = data?.total_count ?? 0;
                        const meta = data?.metadata || {};
                        if (meta.batch_type) setBatchType(meta.batch_type);
                        if (meta.instance_url) setRecordsInstanceURL(meta.instance_url);
                        if (meta.spreadsheet_id) setRecordsSpreadsheetId(meta.spreadsheet_id);
                        setRecordsDownloadDisabled(
                          !!(meta.detail_expired === true || meta.detail_available === false)
                        );
                        if (fetchedRecords.length === 0 && totalCount === 0) {
                          setRecordsError('No migration records found');
                        } else {
                          setRecords(fetchedRecords);
                          recordsFullTotalRef.current = totalCount;
                          setRecordsTotalCount(totalCount);
                          setRecordsCurrentPage(1);
                        }
                      } catch (error) {
                        console.error('Failed to fetch migration records:', error);
                        if (error.response?.status === 410) {
                          setRecordsError(
                            'Migration record detail is no longer available (retention policy).'
                          );
                        } else {
                          setRecordsError('Failed to load migration records. Please try again.');
                        }
                      } finally {
                        setIsLoadingRecords(false);
                      }
                      
                      if (onViewRecords) {
                        onViewRecords(batchId);
                      }
                    }}
                    disabled={isLoadingRecords}
                    style={{
                      padding: '8px 16px',
                      backgroundColor: '#6366f1',
                      color: 'white',
                      border: 'none',
                      borderRadius: '6px',
                      cursor: isLoadingRecords ? 'not-allowed' : 'pointer',
                      fontSize: '14px',
                      fontWeight: '500',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      opacity: isLoadingRecords ? 0.6 : 1
                    }}
                  >
                    {isLoadingRecords ? 'Loading...' : <><FiBarChart2 /> View Results</>}
                  </button>
                  
                  <button
                    className="rollback-button"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleRollbackClick();
                    }}
                    disabled={isRollingBack || isRollbackCompleted}
                    style={{
                      padding: '8px 16px',
                      backgroundColor: isRollbackCompleted ? '#9ca3af' : '#ef4444',
                      color: 'white',
                      border: 'none',
                      borderRadius: '6px',
                      cursor: (isRollingBack || isRollbackCompleted) ? 'not-allowed' : 'pointer',
                      fontSize: '14px',
                      fontWeight: '500',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      opacity: (isRollingBack || isRollbackCompleted) ? 0.6 : 1
                    }}
                    title={isRollbackCompleted ? 'Rollback already completed' : ''}
                  >
                    <FiTrash2 /> {isRollbackCompleted ? 'Rolled Back' : 'Rollback'}
                  </button>
                </div>
                </div>
              )}
              
              {/* Rollback Confirmation Dialog */}
              {showRollbackConfirm && (
                <div 
                  className="rollback-confirmation-dialog"
                  style={{
                    marginTop: '16px',
                    padding: '16px',
                    backgroundColor: '#fef2f2',
                    border: '1px solid #fecaca',
                    borderRadius: '8px'
                  }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <div style={{ marginBottom: '12px' }}>
                    <strong style={{ color: '#991b1b', fontSize: '15px' }}>⚠️ Confirm Rollback</strong>
                    <p style={{ margin: '8px 0 0 0', fontSize: '14px', color: '#374151' }}>
                      This will delete all successfully created records from this migration. 
                      Records that were updated (not created) will not be affected. 
                      This action cannot be undone.
                    </p>
                    {computedTotalRecords > 0 && (
                      <p style={{ margin: '8px 0 0 0', fontSize: '13px', color: '#6b7280' }}>
                        Total records in batch: <strong>{computedTotalRecords}</strong>
                      </p>
                    )}
                  </div>
                  
                  {rollbackError && (
                    <div style={{
                      marginBottom: '12px',
                      padding: '8px 12px',
                      backgroundColor: '#fee2e2',
                      border: '1px solid #fecaca',
                      borderRadius: '6px',
                      color: '#991b1b',
                      fontSize: '13px'
                    }}>
                      ❌ {rollbackError}
                    </div>
                  )}
                  
                  {rollbackResult && (
                    <div style={{
                      marginBottom: '12px',
                      padding: '8px 12px',
                      backgroundColor: rollbackResult.status === 'success' ? '#d1fae5' : '#fef3c7',
                      border: `1px solid ${rollbackResult.status === 'success' ? '#a7f3d0' : '#fde68a'}`,
                      borderRadius: '6px',
                      color: rollbackResult.status === 'success' ? '#065f46' : '#92400e',
                      fontSize: '13px'
                    }}>
                      {rollbackResult.status === 'success' ? '✅' : '⚠️'} {rollbackResult.message}
                      
                      {/* Display rollback message as readable text */}
                      {rollbackResult.rollback_message && (
                        <div style={{
                          marginTop: '8px',
                          padding: '10px 14px',
                          backgroundColor: '#f0f9ff',
                          border: '1px solid #bae6fd',
                          borderRadius: '6px',
                          fontSize: '13px',
                          color: '#0c4a6e',
                          lineHeight: '1.5'
                        }}>
                          📋 {rollbackResult.rollback_message}
                        </div>
                      )}
                      
                      {rollbackResult.deleted_records != null && (
                        <div style={{ marginTop: '4px' }}>
                          Deleted: <strong>{rollbackResult.deleted_records}</strong>
                          {rollbackResult.failed_records > 0 && (
                            <> | Failed: <strong>{rollbackResult.failed_records}</strong></>
                          )}
                        </div>
                      )}
                      
                      {/* Add close button for successful rollback */}
                      {rollbackResult.status === 'success' && (
                        <div style={{ marginTop: '8px', display: 'flex', justifyContent: 'flex-end' }}>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setShowRollbackConfirm(false);
                              setRollbackResult(null);
                            }}
                            style={{
                              padding: '4px 12px',
                              backgroundColor: '#6b7280',
                              color: 'white',
                              border: 'none',
                              borderRadius: '4px',
                              fontSize: '12px',
                              cursor: 'pointer'
                            }}
                          >
                            Close
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                  
                  <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRollbackCancel();
                      }}
                      disabled={isRollingBack}
                      style={{
                        padding: '8px 16px',
                        backgroundColor: '#6b7280',
                        color: 'white',
                        border: 'none',
                        borderRadius: '6px',
                        cursor: isRollingBack ? 'not-allowed' : 'pointer',
                        fontSize: '14px',
                        fontWeight: '500',
                        opacity: isRollingBack ? 0.6 : 1
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRollback();
                      }}
                      disabled={isRollingBack}
                      style={{
                        padding: '8px 16px',
                        backgroundColor: '#ef4444',
                        color: 'white',
                        border: 'none',
                        borderRadius: '6px',
                        cursor: isRollingBack ? 'not-allowed' : 'pointer',
                        fontSize: '14px',
                        fontWeight: '500',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        opacity: isRollingBack ? 0.6 : 1
                      }}
                    >
                      {isRollingBack ? (
                        <>
                          <FiRefreshCw style={{ animation: 'spin 1s linear infinite' }} /> Rolling back...
                        </>
                      ) : (
                        <>
                          <FiTrash2 /> Confirm Rollback
                        </>
                      )}
                    </button>
                  </div>
                </div>
              )}
              
              {/* Show error if records failed to load */}
              {recordsError && (
                <div className="inline-migration-records-error" style={{
                  marginTop: '12px',
                  padding: '12px',
                  backgroundColor: '#fee2e2',
                  border: '1px solid #fecaca',
                  borderRadius: '6px',
                  color: '#991b1b',
                  fontSize: '13px'
                }}>
                  ⚠️ {recordsError}
                </div>
              )}
              
              {/* Show migration records inline */}
              {records && records.length > 0 && (
                <InlineMigrationRecords
                  records={records}
                  totalRecordsCount={recordsTotalCount}
                  batchId={batchId}
                  apiPage={recordsCurrentPage}
                  apiPageSize={500}
                  onFetchPage={batchId ? async (bid, page, searchQuery = '') => {
                    setIsLoadingRecords(true);
                    try {
                      const params = { page, page_size: 500 };
                      const q = (searchQuery || '').trim();
                      if (q) params.q = q;
                      const res = await api.get(`/migration-records/batch/${bid}/records`, {
                        params,
                      });
                      const pageRecords = res.data?.records || [];
                      const meta = res.data?.metadata || {};
                      if (meta.instance_url) setRecordsInstanceURL(meta.instance_url);
                      if (meta.spreadsheet_id) setRecordsSpreadsheetId(meta.spreadsheet_id);
                      setRecordsDownloadDisabled(
                        !!(meta.detail_expired === true || meta.detail_available === false)
                      );
                      setRecords(pageRecords);
                      setRecordsCurrentPage(page);
                      const total = res.data?.total_count;
                      if (total != null) {
                        if (q) {
                          setRecordsTotalCount(total);
                        } else {
                          if (recordsFullTotalRef.current == null) {
                            recordsFullTotalRef.current = total;
                          }
                          setRecordsTotalCount(recordsFullTotalRef.current ?? total);
                        }
                      }
                    } catch (err) {
                      console.error('Failed to fetch migration records page:', err);
                    } finally {
                      setIsLoadingRecords(false);
                    }
                  } : undefined}
                  apiPageLoading={isLoadingRecords}
                  destination={destination}
                  destinationName={destinationName}
                  successCount={successCount}
                  mapping={mapping}
                  migrationInstanceURL={effectiveInstanceURL}
                  destinationSpreadsheetId={effectiveSpreadsheetId}
                  isCollapsed={false}
                  batchType={batchType}
                  downloadDisabled={recordsDownloadDisabled}
                />
              )}
            </>
          ) : status === 'failed' || status === 'error' ? (
            <>
              <div className="inline-migration-error">
                <FiXCircle />
                <span>{error || message || 'Migration failed'}</span>
              </div>
            </>
          ) : null}

          {jobId && (
            <div className="inline-migration-job-id">
              Job ID: <code>{jobId}</code>
            </div>
          )}
        </div>
      )}
    </div>
    <AlertModal
      isOpen={alertModal.isOpen}
      onClose={closeAlert}
      title={alertModal.title}
      message={alertModal.message}
      type={alertModal.type}
    />
  </>
  );
};

export default InlineMigrationStatus;

