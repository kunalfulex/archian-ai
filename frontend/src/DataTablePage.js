import React, { useEffect, useState, useRef, useCallback } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import './DataTablePage.css';
import api from './api';
import AlertModal from './components/AlertModal';

const VIRTUALIZATION_THRESHOLD = 50;
const ROW_HEIGHT = 40;

const camelCaseToLabel = (str) =>
  str
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^./, (char) => char.toUpperCase());

const isValidDestinationName = (name) => {
  if (!name) return false;
  const trimmed = `${name}`.trim();
  if (!trimmed) return false;
  const lower = trimmed.toLowerCase();
  return lower !== 'unknown' && lower !== 'none';
};

const normalizeDestinationName = (name) => (isValidDestinationName(name) ? `${name}`.trim() : null);

/** Paginated GET /extraction-results/{id}/records — identifier may be job_id or extraction_results.id */
async function loadRecordsFromExtractionPages(apiClient, identifier) {
  const pageSize = 1000;
  let page = 1;
  const all = [];
  let total = Infinity;
  while (all.length < total) {
    const pr = await apiClient.get(`/extraction-results/${identifier}/records`, {
      params: { page, page_size: pageSize },
    });
    const d = pr.data;
    if (d?.status !== 'success') break;
    total = Number(d.total_count ?? 0);
    const chunk = d.records || [];
    all.push(...chunk);
    if (chunk.length === 0 || chunk.length < pageSize) break;
    page += 1;
    if (page > 10000) break;
  }
  return all;
}

const EditableTable = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { jobId: jobIdParam } = useParams();

  const [records, setRecords] = useState([]);
  // Spreadsheet view: show all records in a grid instead of per‑page
  // eslint-disable-next-line no-unused-vars
  const [documentType, setDocumentType] = useState('');
  // eslint-disable-next-line no-unused-vars
  const [documentDescription, setDocumentDescription] = useState('');
  const [, setFileKey] = useState('');
  
  // SQL data specific states
  const [isSqlData, setIsSqlData] = useState(false);
  const [sqlTableName, setSqlTableName] = useState('');
  // eslint-disable-next-line no-unused-vars
  const [sqlQuery, setSqlQuery] = useState('');
  const [sqlTotalRecords, setSqlTotalRecords] = useState(0);
  const [planLimitInfo, setPlanLimitInfo] = useState(null);
  const [showUpgradeOption, setShowUpgradeOption] = useState(false);
  // eslint-disable-next-line no-unused-vars
  const [connectorId, setConnectorId] = useState('');
  // eslint-disable-next-line no-unused-vars
  const [salesforceOrgId, setSalesforceOrgId] = useState(null);
  const [sourceName, setSourceName] = useState(null); // Source connection name (e.g., Salesforce org name)
  // eslint-disable-next-line no-unused-vars
  const [extractedSchemaType, setExtractedSchemaType] = useState('file');
  
  // Alert modal state
  const [alertModal, setAlertModal] = useState({ isOpen: false, title: '', message: '', type: 'error' });
  
  // Helper function to show alerts
  const showAlert = (title, message, type = 'error') => {
    setAlertModal({ isOpen: true, title, message, type });
  };
  
  const closeAlert = () => {
    setAlertModal({ isOpen: false, title: '', message: '', type: 'error' });
  };
  
  // Job-based loading state
  const [isJobLoading, setIsJobLoading] = useState(false);
  // eslint-disable-next-line no-unused-vars
  const [jobStatus, setJobStatus] = useState(null);
  const [jobProgress, setJobProgress] = useState(0);
  const [jobError, setJobError] = useState(null);
  const [jobMessage, setJobMessage] = useState('');
  const [isReusableFlow, setIsReusableFlow] = useState(false);
  const [activeJobId, setActiveJobId] = useState(jobIdParam || null);
  
  // SQL task state
  const [sqlTaskId, setSqlTaskId] = useState(null);
  // eslint-disable-next-line no-unused-vars
  const [sqlTaskStatus, setSqlTaskStatus] = useState(null);
  const [sqlTaskProgress, setSqlTaskProgress] = useState(0);
  const [sqlTaskMessage, setSqlTaskMessage] = useState('');
  const [sqlTaskError, setSqlTaskError] = useState(null);
  const [dataSource, setDataSource] = useState('file'); // 'file', 'sql', or 'gsheets'
  const [isSqlReusableFlow, setIsSqlReusableFlow] = useState(false); // Track SQL reusable flow
  const [isGoogleSheetsReusableFlow, setIsGoogleSheetsReusableFlow] = useState(false); // Track Google Sheets reusable flow
  const [isSalesforceReusableFlow, setIsSalesforceReusableFlow] = useState(false); // Track Salesforce reusable flow
  const [hasRedirectedSalesforceFlow, setHasRedirectedSalesforceFlow] = useState(false);
  
  // Destination state
  // eslint-disable-next-line no-unused-vars
  const [destination, setDestination] = useState(null); // 'salesforce', 'google_sheets', or 'sql'
  // eslint-disable-next-line no-unused-vars
  const [destinationName, setDestinationName] = useState(null); // Name of the destination connection/spreadsheet/database
  
  
  // Pagination state
  const [currentPage, setCurrentPage] = useState(0);
  const [isPageChanging, setIsPageChanging] = useState(false);
  const pageSize = 10; // Show 10 records per page
  const jobPollRef = useRef(null);

  useEffect(() => {
    setActiveJobId(jobIdParam || null);
  }, [jobIdParam]);

  const applyPersistedExtractionMetadata = useCallback((result) => {
    const st = (result.source_type || '').toLowerCase();
    setDocumentType(result.document_type || '');
    setDocumentDescription(result.document_description || '');
    setFileKey('');
    if (st === 'salesforce') {
      setIsSqlData(true);
      setDataSource('salesforce');
      setSqlTableName(result.document_type || '');
      setSqlQuery(result.extraction_goals || '');
      setSqlTotalRecords(result.record_count || 0);
      setSourceName(result.source_name || null);
      setSalesforceOrgId(result.source_identifier || null);
      setConnectorId(result.source_identifier || '');
      setExtractedSchemaType('salesforce');
    } else if (st === 'google_sheets') {
      setIsSqlData(true);
      setDataSource('gsheets');
      setSqlTableName(result.document_type || '');
      setSqlQuery(result.extraction_goals || '');
      setSqlTotalRecords(result.record_count || 0);
      setSourceName(result.source_name || null);
      setConnectorId(result.source_identifier || '');
      setExtractedSchemaType('gsheets');
    } else if (st === 'sql') {
      setIsSqlData(true);
      setDataSource('sql');
      setSqlTableName(result.document_type || '');
      setSqlQuery(result.extraction_goals || '');
      setSqlTotalRecords(result.record_count || 0);
      setSourceName(result.source_name || null);
      setConnectorId(result.source_identifier || '');
      setExtractedSchemaType('sql');
    } else {
      setIsSqlData(false);
      setDataSource('file');
      setSqlTotalRecords(result.record_count || 0);
      setExtractedSchemaType('file');
    }
    setIsSalesforceReusableFlow(false);
    setIsSqlReusableFlow(false);
    setIsGoogleSheetsReusableFlow(false);
  }, []);

  // Function to poll job status
  const pollJobStatus = async (jobId) => {
    try {
      const response = await api.get(`/job-status/${jobId}`);
      const jobData = response.data;
      const sourceType = jobData.source_type || jobData.sourceType || '';

      setJobStatus(jobData.status);
      setJobProgress(jobData.progress || 0);
      setJobMessage(jobData.message || '');
      
      if (sourceType === 'salesforce') {
        setDataSource('salesforce');
        if (jobData.salesforce_token_id) {
          setConnectorId(jobData.salesforce_token_id);
        }
        if (jobData.org_id) {
          setSalesforceOrgId(jobData.org_id);
        }
        if (jobData.connection_name) {
          setSourceName(jobData.connection_name);
        }
        setIsSqlData(true);
        if (jobData.flow_id) {
          setIsSalesforceReusableFlow(true);
        }
      }
      
      // Set destination information from job data
      if (jobData.destination) {
        setDestination(jobData.destination);
      }
      const jobDestinationName = normalizeDestinationName(jobData.destination_name);
      if (jobDestinationName) {
        setDestinationName(jobDestinationName);
      }
      
      // Set source name based on source type
      if (sourceType === 'google_sheets' && jobData.spreadsheet_name) {
        setSourceName(jobData.spreadsheet_name);
      }
      
      
      // Check if this is a reusable flow (has flow_id)
      if (jobData.flow_id) {
        setIsReusableFlow(true);
      }
      if (location.state?.isReusableFlow) {
        setIsReusableFlow(true);
        if (sourceType === 'salesforce' || dataSource === 'salesforce' || location.state?.source === 'salesforce') {
          setIsSalesforceReusableFlow(true);
        }
      }
 
      if (jobData.status === 'completed') {
        const result = jobData.result || {};
        const resultDestinationName = normalizeDestinationName(result.destination_name || result.destinationName);
        if (!jobDestinationName && resultDestinationName) {
          setDestinationName(resultDestinationName);
        }
        const isSalesforceFlow = sourceType === 'salesforce' || dataSource === 'salesforce' || location.state?.source === 'salesforce';
        const salesforceReusable = isSalesforceFlow && (isSalesforceReusableFlow || location.state?.isReusableFlow);

        if (
          salesforceReusable &&
          !hasRedirectedSalesforceFlow &&
          isSalesforceFlow
        ) {
          setRecords(result.data || []);
          setDocumentType(result.document_type || 'Salesforce Reusable Flow');
          setDocumentDescription('Salesforce reusable flow extraction completed');
          setFileKey('');
          setShowUpgradeOption(false);
          setIsJobLoading(false);
          setHasRedirectedSalesforceFlow(true);
          setTimeout(() => {
            window.location.href = '/flows';
          }, 5000);
          return true;
        }

        if (jobData.result) {
          // Job completed successfully, check if it's a reusable flow or regular flow
          if (result.status === 'migration_completed') {
            // Reusable flow completed - show migration results
            setRecords(result.data || []); // No data to display for reusable flows
            setDocumentType(result.document_type || '');
            setDocumentDescription('Reusable Flow Migration');
            setFileKey('');
            setShowUpgradeOption(false);
            showAlert('Success', result.message || 'Reusable flow migration completed! Redirecting to flows...', 'success');

            // Redirect back to flows page immediately (no alert)
            setTimeout(() => {
              window.location.href = '/flows';
            }, 5000);

            setIsJobLoading(false);
            return true; // Job completed
          } else if (result.status === 'plan_limit_reached') {
            setJobMessage(result.message || '');
            if (result.extracted_schema_type === 'salesforce') {
              setSqlTableName(result.object_label || result.object_api_name || '');
              setSqlQuery(result.soql || '');
              setSqlTotalRecords(result.total_records || 0);
              setDataSource('salesforce');
              setSalesforceOrgId(result.org_id || null);
              // Source name might be in jobData or result
              if (jobData.connection_name) {
                setSourceName(jobData.connection_name);
              }
            } else if (result.extracted_schema_type === 'gsheets') {
              setSqlTableName(result.sheet_name || result.spreadsheet_name || '');
              setSqlQuery(result.user_prompt || result.extraction_goals || '');
              setSqlTotalRecords(result.original_record_count || 0);
              setDataSource('gsheets');
              setSalesforceOrgId(null);
              // Source name for Google Sheets
              if (result.spreadsheet_name || jobData.spreadsheet_name) {
                setSourceName(result.spreadsheet_name || jobData.spreadsheet_name);
              }
            } else {
              setSalesforceOrgId(null);
            }
            setExtractedSchemaType(result.extracted_schema_type || 'file');
            if (result.extracted_schema_type === 'salesforce') {
              setConnectorId(result.salesforce_token_id || result.org_id || '');
            } else if (result.extracted_schema_type === 'gsheets') {
              setConnectorId(result.connector_id || '');
            } else {
              setConnectorId(result.connector_id || '');
            }
            // Plan limit completely reached - no records can be extracted
            setRecords([]); // No data to display
            setDocumentType(result.document_type || '');
            setDocumentDescription('Plan Limit Reached');
            setFileKey(result.file_key || '');
            setShowUpgradeOption(true); // Always show upgrade option for plan limit reached

            // Set plan limit info to show the upgrade message
            if (result.plan_info) {
              setPlanLimitInfo({
                plan_type: result.plan_info.plan_type,
                credit_balance: result.plan_info.credit_balance,
                remaining_credits: result.plan_info.remaining_credits,
                can_extract: result.plan_info.can_extract,
                total_in_file: result.original_record_count || 0,
                extracted_records: 0,
                original_file_records: result.original_record_count || 0,
                limitation_applied: true,
                is_plan_limit_reached: true
              });
            } else if (result.extraction_limit) {
              setPlanLimitInfo({
                plan_type: result.extraction_limit.plan_type,
                credit_balance: result.extraction_limit.credit_balance,
                remaining_credits: result.extraction_limit.remaining_credits,
                can_extract: result.extraction_limit.can_extract,
                total_in_file: result.original_record_count || result.original_file_records || 0,
                extracted_records: 0,
                original_file_records: result.original_record_count || result.original_file_records || 0,
                limitation_applied: true,
                is_plan_limit_reached: true
              });
            }

            setIsJobLoading(false);
            return true; // Job completed
          } else {
            setJobMessage(result.message || '');
            // Regular flow completed - load the data for review
            let rows = result.data || [];
            if (rows.length === 0) {
              const tr =
                result.total_records ??
                result.record_count ??
                result.original_record_count ??
                0;
              if (tr > 0) {
                try {
                  rows = await loadRecordsFromExtractionPages(api, jobId);
                } catch (_) {
                  /* slim Redis payload; DB fetch failed or not yet persisted */
                }
              }
            }
            setRecords(rows);
            setDocumentType(result.document_type || '');
            setDocumentDescription(result.document_description || '');
            setFileKey(result.file_key || '');
            setShowUpgradeOption(result.show_upgrade_option || false);

            if (result.extracted_schema_type === 'salesforce') {
              setSqlTableName(result.object_label || result.object_api_name || '');
              setSqlQuery(result.soql || '');
              setSqlTotalRecords(result.total_records || rows.length);
              setDataSource('salesforce');
              setSalesforceOrgId(result.org_id || null);
              // Source name might be in jobData or result
              if (jobData.connection_name) {
                setSourceName(jobData.connection_name);
              }
            } else if (result.extracted_schema_type === 'gsheets') {
              setSqlTableName(result.sheet_name || result.spreadsheet_name || '');
              setSqlQuery(result.user_prompt || result.extraction_goals || '');
              setSqlTotalRecords(result.total_records || rows.length);
              setDataSource('gsheets');
              setSalesforceOrgId(null);
              // Source name for Google Sheets
              if (result.spreadsheet_name || jobData.spreadsheet_name) {
                setSourceName(result.spreadsheet_name || jobData.spreadsheet_name);
              }
            } else {
              setSalesforceOrgId(null);
            }

            if (result.extracted_schema_type === 'salesforce') {
              setConnectorId(result.salesforce_token_id || result.org_id || '');
            } else if (result.extracted_schema_type === 'gsheets') {
              setConnectorId(result.connector_id || '');
            } else {
              setConnectorId(result.connector_id || '');
            }

            setExtractedSchemaType(result.extracted_schema_type || 'file');

            // Handle plan limit information if present
            if (result.plan_info) {
              setPlanLimitInfo({
                plan_type: result.plan_info.plan_type,
                max_records_per_month: result.plan_info.credits_per_month ?? result.plan_info.can_extract ?? result.plan_info.max_records_per_month,
                already_migrated: result.plan_info.already_migrated,
                remaining_records: result.plan_info.can_extract ?? result.plan_info.remaining_records,
                total_in_file: result.original_record_count || result.original_file_records || (result.data ? result.data.length : 1),
                extracted_records: result.data ? result.data.length : 1,
                original_file_records: result.original_record_count || result.original_file_records || (result.data ? result.data.length : 1),
                limitation_applied: result.limitation_applied || false,
                is_plan_limit_reached: result.plan_info.is_plan_limit_reached || false, // Flag for plan limit reached
              });
            } else if (result.extraction_limit) {
              // Fallback: transform extraction_limit to plan_info format (credit-based)
              setPlanLimitInfo({
                plan_type: result.extraction_limit.plan_type,
                max_records_per_month: result.extraction_limit.credits_per_month ?? result.extraction_limit.can_extract ?? result.extraction_limit.max_records_per_month,
                already_migrated: result.extraction_limit.already_migrated,
                remaining_records: result.extraction_limit.can_extract ?? result.extraction_limit.remaining_records,
                total_in_file: result.original_record_count || result.original_file_records || (result.data ? result.data.length : 1),
                extracted_records: result.data ? result.data.length : 1,
                original_file_records: result.original_record_count || result.original_file_records || (result.data ? result.data.length : 1),
                limitation_applied: result.limitation_applied || (result.extraction_limit.can_extract < (result.original_record_count || result.original_file_records || (result.data ? result.data.length : 0))),
                is_plan_limit_reached: result.extraction_limit.can_extract === 0,
              });
            }


            setIsJobLoading(false);
            return true; // Job completed
          }
        }
      } else if (jobData.status === 'failed') {
        // Job failed
        if (isSalesforceReusableFlow || location.state?.isReusableFlow) {
          // Keep state flags for diagnosis but avoid noisy logs
        }
        setJobError(jobData.error || 'Job failed');
        setJobMessage(jobData.message || '');
        setIsJobLoading(false);
        return true; // Job completed (with error)
      }
      
      return false; // Job still processing
    } catch (error) {
      console.error('Error polling job status:', error);
      if (error?.response?.status === 404) {
        setJobError('Salesforce extraction job not found. Please try running the extraction again.');
      } else {
        setJobError('Failed to check job status');
      }
      setIsJobLoading(false);
      return true; // Stop polling on error
    }
  };

  // Function to poll SQL task status
  const pollSQLTaskStatus = async (taskId) => {
    try {
      const response = await api.get(`/job-status/${taskId}`);
      const taskData = response.data;
      
      setSqlTaskStatus(taskData.status);
      setSqlTaskProgress(taskData.progress || 0);
      setSqlTaskMessage(taskData.message || '');
      
      // Set destination information from task data
      if (taskData.destination) {
        setDestination(taskData.destination);
      }
      const taskDestinationName = normalizeDestinationName(taskData.destination_name);
      if (taskDestinationName) {
        setDestinationName(taskDestinationName);
      }
      
      // Set source name for SQL (connector name)
      if (taskData.connector_name) {
        setSourceName(taskData.connector_name);
      }
      
      if (taskData.status === 'completed' && taskData.result) {
        // SQL task completed successfully
        const result = taskData.result;
        const resultDestinationName = normalizeDestinationName(result.destination_name || result.destinationName);
        if (!taskDestinationName && resultDestinationName) {
          setDestinationName(resultDestinationName);
        }

        // Match Salesforce rerun: use location.state too — interval polling closes over a stale
        // isSqlReusableFlow (still false on first render when setIsSqlReusableFlow runs in useEffect).
        const sqlFlowReuse =
          isSqlReusableFlow ||
          (location.state?.source === 'sql' && Boolean(location.state?.isReusableFlow));

        if (sqlFlowReuse) {
          const successMessage = result.message || 'SQL reusable flow migration completed! Redirecting to flows...';
          setJobMessage(successMessage);
          setIsJobLoading(false);
          showAlert('Success', successMessage, 'success');
          setTimeout(() => {
            window.location.href = '/flows';
          }, 5000);
          return true;
        }

        setRecords(result.data || []);
        setDocumentType(result.document_type || '');
        setDocumentDescription(result.document_description || '');
        setSqlTableName(result.table_name || '');
        setSqlQuery(result.query || '');
        setSqlTotalRecords(result.total_records || 0);
        setShowUpgradeOption(result.show_upgrade_option || false);
        setConnectorId(result.connector_id || '');
        setExtractedSchemaType(result.extracted_schema_type || 'file');
        
        // Set source name from result or taskData (connector name for SQL)
        if (result.connector_name || taskData.connector_name) {
          setSourceName(result.connector_name || taskData.connector_name);
        }
        
        setIsSqlData(true);
        setDataSource('sql');
        
        // Handle plan limit information if present
        if (result.plan_info) {
          setPlanLimitInfo({
            plan_type: result.plan_info.plan_type,
            max_records_per_month: result.plan_info.max_records_per_month,
            already_migrated: result.plan_info.current_records || result.plan_info.already_migrated,
            remaining_records: result.plan_info.remaining_records,
            total_in_file: result.total_records || 0,
            extracted_records: result.data ? result.data.length : 0,
            original_file_records: result.total_records || 0,
            limitation_applied: result.limitation_applied || false,
            is_plan_limit_reached: result.plan_info.is_plan_limit_reached || false,
          });
        }
        
        setIsJobLoading(false);
        
        // Redirect will be handled by useEffect when conditions are met
        
        return true; // Task completed
      } else if (taskData.status === 'failed') {
        setSqlTaskError(taskData.error || 'SQL extraction failed');
        setIsJobLoading(false);
        return true; // Task completed with error
      }
      
      return false; // Task still processing
    } catch (error) {
      console.error('Error polling SQL task status:', error);
      setSqlTaskError('Failed to check SQL task status');
      setIsJobLoading(false);
      return true; // Stop polling on error
    }
  };

  // Function to poll Google Sheets task status
  const pollGoogleSheetsTaskStatus = async (taskId) => {
    try {
      const response = await api.get(`/job-status/${taskId}`);
      const taskData = response.data;
      
      setSqlTaskStatus(taskData.status);
      setSqlTaskProgress(taskData.progress || 0);
      setSqlTaskMessage(taskData.message || '');
      
      // Set destination information from task data
      if (taskData.destination) {
        setDestination(taskData.destination);
      }
      const gsTaskDestinationName = normalizeDestinationName(taskData.destination_name);
      if (gsTaskDestinationName) {
        setDestinationName(gsTaskDestinationName);
      }
      
      // Set source name for Google Sheets
      if (taskData.spreadsheet_name) {
        setSourceName(taskData.spreadsheet_name);
      }
      
      if (taskData.status === 'completed' && taskData.result) {
        // Google Sheets task completed successfully
        const result = taskData.result;
        const resultDestinationName = normalizeDestinationName(result.destination_name || result.destinationName);
        if (!gsTaskDestinationName && resultDestinationName) {
          setDestinationName(resultDestinationName);
        }
        
        if (isGoogleSheetsReusableFlow) {
          const successMessage = result.message || 'Google Sheets reusable flow migration completed! Redirecting to flows...';
          setJobMessage(successMessage);
          setIsJobLoading(false);
          showAlert('Success', successMessage, 'success');
          setTimeout(() => {
            window.location.href = '/flows';
          }, 5000);
          return true;
        }

        // Check for plan limit reached (consistent with Salesforce)
        if (result.status === 'plan_limit_reached') {
          setJobMessage(result.message || '');
          setSqlTableName(result.sheet_name || result.spreadsheet_name || '');
          setSqlQuery(result.user_prompt || result.extraction_goals || '');
          setSqlTotalRecords(result.original_record_count || 0);
          setDataSource('gsheets');
          setRecords([]); // No data to display
          setDocumentType(result.document_type || 'google_sheets');
          setDocumentDescription('Plan Limit Reached');
          setFileKey(result.file_key || '');
          setShowUpgradeOption(true); // Always show upgrade option for plan limit reached
          setConnectorId(result.connector_id || '');
          setExtractedSchemaType(result.extracted_schema_type || 'gsheets');
          
          // Set source name from result or taskData
          if (result.spreadsheet_name || taskData.spreadsheet_name) {
            setSourceName(result.spreadsheet_name || taskData.spreadsheet_name);
          }
          
          // Set plan limit info to show the upgrade message
          if (result.plan_info) {
            setPlanLimitInfo({
              plan_type: result.plan_info.plan_type,
              max_records_per_month: result.plan_info.max_records_per_month,
              already_migrated: result.plan_info.already_migrated,
              remaining_records: result.plan_info.remaining_records,
              total_in_file: result.original_record_count || 0,
              extracted_records: 0,
              original_file_records: result.original_record_count || 0,
              limitation_applied: true, // Show as limitation applied to display the notification
              is_plan_limit_reached: true // Special flag for plan limit reached
            });
          } else if (result.extraction_limit) {
            // Fallback: transform extraction_limit to plan_info format
            setPlanLimitInfo({
              plan_type: result.extraction_limit.plan_type,
              max_records_per_month: result.extraction_limit.max_records_per_month,
              already_migrated: result.extraction_limit.already_migrated,
              remaining_records: result.extraction_limit.remaining_records,
              total_in_file: result.original_record_count || result.original_file_records || 0,
              extracted_records: 0,
              original_file_records: result.original_record_count || result.original_file_records || 0,
              limitation_applied: true,
              is_plan_limit_reached: true
            });
          }
          
          setIsSqlData(true);
          setIsJobLoading(false);
          return true; // Stop polling
        }

        setRecords(result.data || []);
        setDocumentType(result.document_type || 'google_sheets');
        setDocumentDescription(result.document_description || `Google Sheets: ${result.spreadsheet_name || 'Unknown'}`);
        setSqlTableName(result.table_name || result.spreadsheet_name || '');
        setSqlQuery(result.user_prompt || result.extraction_goals || '');
        setSqlTotalRecords(result.total_records || 0);
        setShowUpgradeOption(result.show_upgrade_option || false);
        setConnectorId(result.connector_id || '');
        setExtractedSchemaType(result.extracted_schema_type || 'gsheets');
        
        // Set source name from result or taskData
        if (result.spreadsheet_name || taskData.spreadsheet_name) {
          setSourceName(result.spreadsheet_name || taskData.spreadsheet_name);
        }
        
        // Handle plan limit information if present (for limited extractions)
        if (result.plan_info) {
          setPlanLimitInfo({
            plan_type: result.plan_info.plan_type,
            max_records_per_month: result.plan_info.max_records_per_month,
            already_migrated: result.plan_info.already_migrated,
            remaining_records: result.plan_info.remaining_records,
            total_in_file: result.original_record_count || result.original_file_records || (result.data ? result.data.length : 0),
            extracted_records: result.data ? result.data.length : 0,
            original_file_records: result.original_record_count || result.original_file_records || (result.data ? result.data.length : 0),
            limitation_applied: result.limitation_applied || false,
            is_plan_limit_reached: result.plan_info.is_plan_limit_reached || false
          });
        } else if (result.extraction_limit) {
          // Fallback: transform extraction_limit to plan_info format
          setPlanLimitInfo({
            plan_type: result.extraction_limit.plan_type,
            max_records_per_month: result.extraction_limit.max_records_per_month,
            already_migrated: result.extraction_limit.already_migrated,
            remaining_records: result.extraction_limit.remaining_records,
            total_in_file: result.original_record_count || result.original_file_records || (result.data ? result.data.length : 0),
            extracted_records: result.data ? result.data.length : 0,
            original_file_records: result.original_record_count || result.original_file_records || (result.data ? result.data.length : 0),
            limitation_applied: result.limitation_applied || (result.extraction_limit.can_extract < (result.original_record_count || result.original_file_records || 0)),
            is_plan_limit_reached: result.extraction_limit.can_extract === 0
          });
        }
        
        // Set fields needed for Salesforce mapping
        setFileKey(result.file_key || null);
        
        setIsSqlData(true);
        setDataSource('gsheets');
        
        setIsJobLoading(false);
        
        return true; // Stop polling
      } else if (taskData.status === 'failed') {
        // Task failed
        setSqlTaskError(taskData.error || 'Google Sheets extraction failed');
        setIsJobLoading(false);
        return true; // Stop polling
      }
      
      return false; // Continue polling
    } catch (error) {
      console.error('Error polling Google Sheets task status:', error);
      setSqlTaskError('Failed to check Google Sheets task status');
      setIsJobLoading(false);
      return true; // Stop polling on error
    }
  };

  // Function to handle SQL background task
  const handleSQLBackgroundTask = (taskId) => {
    setSqlTaskId(taskId);
    setDataSource('sql');
    setIsJobLoading(true);
    setSqlTaskError(null);
    startSQLTaskPolling(taskId);
  };

  // Function to handle Google Sheets background task
  const handleGoogleSheetsBackgroundTask = (taskId) => {
    setSqlTaskId(taskId); // Reuse the same state for task ID
    setDataSource('gsheets');
    setIsJobLoading(true);
    setSqlTaskError(null);
    startGoogleSheetsTaskPolling(taskId);
  };

  // Function to handle Salesforce background task
  const handleSalesforceBackgroundTask = (taskId) => {
    setSqlTaskId(taskId);
    setDataSource('salesforce');
    setIsJobLoading(true);
    setSqlTaskError(null);
    setIsSqlData(true);
    setActiveJobId(taskId);
    startSalesforceTaskPolling(taskId);
  };

  // Function to start SQL task polling
  const startSQLTaskPolling = (taskId) => {
    const pollInterval = setInterval(async () => {
      const isCompleted = await pollSQLTaskStatus(taskId);
      if (isCompleted) {
        clearInterval(pollInterval);
      }
    }, 1000); // Poll every 1 second for SQL tasks
    
    // Cleanup interval on unmount
    return () => clearInterval(pollInterval);
  };

  // Function to start Google Sheets task polling
  const startGoogleSheetsTaskPolling = (taskId) => {
    const pollInterval = setInterval(async () => {
      const isCompleted = await pollGoogleSheetsTaskStatus(taskId);
      if (isCompleted) {
        clearInterval(pollInterval);
      }
    }, 1000); // Poll every 1 second for Google Sheets tasks
    
    // Cleanup interval on unmount
    return () => clearInterval(pollInterval);
  };

  // Function to start Salesforce task polling
  const startSalesforceTaskPolling = (taskId) => {
    const pollInterval = setInterval(async () => {
      const isCompleted = await pollJobStatus(taskId);
      if (isCompleted) {
        clearInterval(pollInterval);
      }
    }, 2000); // Poll every 2 seconds for Salesforce tasks

    return () => clearInterval(pollInterval);
  };



  useEffect(() => {
    // Handle job-based loading: prefer persisted extraction (survives Redis TTL), then poll job status
    if (activeJobId) {
      let cancelled = false;
      setIsJobLoading(true);
      setJobError(null);
      if (jobPollRef.current) {
        clearInterval(jobPollRef.current);
        jobPollRef.current = null;
      }

      (async () => {
        try {
          const res = await api.get(`/extraction-results/${activeJobId}`);
          if (cancelled) return;
          const result = res.data?.result;
          if (result) {
            let rows = Array.isArray(result.extracted_data) ? result.extracted_data : [];
            const total = result.record_count || 0;
            if (rows.length === 0 && total > 0) {
              rows = await loadRecordsFromExtractionPages(api, activeJobId);
            }
            if (cancelled) return;
            applyPersistedExtractionMetadata(result);
            setRecords(rows);
            setIsJobLoading(false);
            return;
          }
        } catch (e) {
          if (cancelled) return;
          if (e.response?.status !== 404) {
            setJobError(e.response?.data?.detail || e.message || 'Failed to load extraction');
            setIsJobLoading(false);
            return;
          }
        }
        if (cancelled) return;
        jobPollRef.current = setInterval(async () => {
          const isCompleted = await pollJobStatus(activeJobId);
          if (isCompleted && jobPollRef.current) {
            clearInterval(jobPollRef.current);
            jobPollRef.current = null;
          }
        }, 2000);
      })();

      return () => {
        cancelled = true;
        if (jobPollRef.current) {
          clearInterval(jobPollRef.current);
          jobPollRef.current = null;
        }
      };
    }

    // Handle task-based loading (SQL or Google Sheets)
    const taskId = location.state?.job_id || location.state?.task_id;
    const source = location.state?.source;
    if (location.state?.isReusableFlow) {
      setIsReusableFlow(true);
    }
    
    if (taskId) {
      if (source === 'sql') {
        // Handle SQL task
        setIsSqlData(true);
        setDataSource('sql');
        
        // Check if this is a SQL reusable flow (from flows page)
        const reusableFromState = Boolean(location.state?.isReusableFlow);
        if (reusableFromState) {
          setIsSqlReusableFlow(true);
        } else {
          setIsSqlReusableFlow(false);
        }
        
        handleSQLBackgroundTask(taskId);
        return;
      } else if (source === 'gsheets') {
        // Handle Google Sheets task
        setIsSqlData(true); // Use same UI as SQL for now
        setDataSource('gsheets');
        
        // Check if this is a Google Sheets reusable flow (from flows page)
        const reusableFromState = Boolean(location.state?.isReusableFlow);
        if (reusableFromState) {
          setIsGoogleSheetsReusableFlow(true);
        } else {
          setIsGoogleSheetsReusableFlow(false);
        }
        
        handleGoogleSheetsBackgroundTask(taskId);
        return;
      } else if (source === 'salesforce') {
        setIsSqlData(true);
        setDataSource('salesforce');
        if (location.state?.isReusableFlow) {
          setIsReusableFlow(true);
          setIsSalesforceReusableFlow(true);
        }
        setHasRedirectedSalesforceFlow(false);
        handleSalesforceBackgroundTask(taskId);
        return;
      }
    } else {
      setIsSalesforceReusableFlow(false);
    }
    
    // Handle regular data loading (existing logic)
    const incoming = location.state?.data;
    const docType = location.state?.document_type;
    const docDescription = location.state?.document_description;
    const key = location.state?.file_key;

    // SQL data specific
    const tableName = location.state?.tableName;
    const query = location.state?.query;
    const totalRecords = location.state?.totalRecords;
    const sqlDocumentType = location.state?.document_type;
    const sqlDocumentDescription = location.state?.document_description;
    
    // Capture plan limit information if available
    const planInfo = location.state?.plan_info;
    const limitationApplied = location.state?.limitation_applied;
    const totalRecordsInFile = location.state?.total_records_in_file;
    const upgradeOption = location.state?.show_upgrade_option;
    
    // Handle SQL data
    if (source === 'sql' && incoming) {
      setIsSqlData(true);
      setDataSource('sql');
      setSqlTableName(tableName || '');
      setSqlQuery(query || '');
      setSqlTotalRecords(totalRecords || 0);
      setDocumentType(sqlDocumentType || 'SQL Data');
      setDocumentDescription(sqlDocumentDescription || `Data extracted from ${tableName} table`);
      
      if (Array.isArray(incoming)) {
        setRecords(incoming);
      } else if (typeof incoming === 'object') {
        setRecords([incoming]);
      }
      return; // Skip the rest of the logic for SQL data
    }
    
    // Use original data
    setDocumentType(docType || '');
    setDocumentDescription(docDescription || '');
    
    if (Array.isArray(incoming)) {
      setRecords(incoming);
    } else if (typeof incoming === 'object') {
      setRecords([incoming]);
    }
    
    setFileKey(key || '');
    
    // Set plan limit information only when limitation is actually applied
    if (planInfo && limitationApplied) {
      setPlanLimitInfo({
        plan_type: planInfo.plan_type,
        max_records_per_month: planInfo.credits_per_month ?? planInfo.can_extract ?? planInfo.max_records_per_month,
        already_migrated: planInfo.already_migrated,
        remaining_records: planInfo.can_extract ?? planInfo.remaining_records,
        total_in_file: totalRecordsInFile || (Array.isArray(incoming) ? incoming.length : 1),
        extracted_records: Array.isArray(incoming) ? incoming.length : 1,
        original_file_records: planInfo.original_file_records || (Array.isArray(incoming) ? incoming.length : 1),
        limitation_applied: limitationApplied
      });
    }
    
    // Set upgrade option flag from backend
    setShowUpgradeOption(upgradeOption || false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mixed router state + job/task handlers; full deps risks duplicate polling
  }, [location.state, activeJobId]);

  // Reset current page when records change
  useEffect(() => {
    setCurrentPage(0);
  }, [records]);

  // Handle Google Sheets reusable flow redirect when job completes (redirect regardless of record count)
  useEffect(() => {
    if (isGoogleSheetsReusableFlow && !isJobLoading) {
      setTimeout(() => {
        window.location.href = '/flows';
      }, 2000);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isGoogleSheetsReusableFlow, isJobLoading]);

  // Reset Salesforce redirect state when dependencies change
  useEffect(() => {
    if (!isJobLoading && !isSalesforceReusableFlow) {
      setHasRedirectedSalesforceFlow(false);
    }
  }, [isJobLoading, isSalesforceReusableFlow]);



  const useVirtual = records.length > VIRTUALIZATION_THRESHOLD;
  const tableScrollRef = useRef(null);
  const rowVirtualizer = useVirtualizer({
    count: useVirtual ? records.length : 0,
    getScrollElement: () => tableScrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  });

  const getColumnKeys = () => {
    // Filter out internal/metadata fields (starting with '_') from display
    // These fields are kept in data for migration but hidden from table
    const orderedFromFirst = Object.keys(records[0] || {}).filter(k => !k.startsWith('_'));
    const union = new Set(orderedFromFirst);
    for (const r of records) {
      Object.keys(r || {}).filter(k => !k.startsWith('_')).forEach((k) => union.add(k));
    }
    // Preserve first record order, then append any extras in alpha order
    const extras = Array.from(union).filter((k) => !orderedFromFirst.includes(k)).sort();
    return [...orderedFromFirst, ...extras];
  };

  const handleCellChange = (rowIdx, key, value) => {
    setRecords((prev) => {
      const next = [...prev];
      next[rowIdx] = { ...(next[rowIdx] || {}), [key]: value };
      return next;
    });
  };






  const renderInput = (rowIdx, key, value) => {
    if (typeof value === 'boolean') {
      return (
        <input
          type="checkbox"
          checked={value}
          onChange={(e) => handleCellChange(rowIdx, key, e.target.checked)}
          className="checkbox"
        />
      );
    }
    return (
      <input
        type="text"
        value={value || ''}
        onChange={(e) => handleCellChange(rowIdx, key, e.target.value)}
        className="editable-input"
      />
    );
  };

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100vh', backgroundColor: '#f7f7f8' }}>
      <div style={{ flex: 1, overflowY: 'auto', padding: '24px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <div className="table-wrapper">
          <h2>📄 Extracted Data</h2>
          
          {/* SQL Data Header */}

          {isSqlData && (
            <div style={{
              backgroundColor: '#f0f9ff',
              border: '1px solid #0ea5e9',
              borderRadius: '8px',
              padding: '16px',
              marginBottom: '16px'
            }}>
              <h3 style={{ margin: '0 0 8px 0', color: '#0369a1' }}>
                {dataSource === 'gsheets'
                  ? '📊 Google Spreadsheet Data'
                  : dataSource === 'salesforce'
                    ? '🔷 Salesforce Data'
                    : '🗄️ SQL Database Data'}
              </h3>
              <div style={{ display: 'flex', gap: '24px', fontSize: '14px', marginBottom: '8px' }}>
                {sourceName && (
                  <div>
                    <strong>Source:</strong> {sourceName}
                  </div>
                )}
                <div>
                  <strong>
                    {dataSource === 'gsheets'
                      ? 'Spreadsheet:'
                      : dataSource === 'salesforce'
                        ? 'Object:'
                        : 'Table:'}
                  </strong> {sqlTableName}
                </div>
                <div>
                  <strong>Total Records:</strong> {sqlTotalRecords}
                </div>
              </div>
            </div>
          )}
          
          {/* Job Loading State */}
          {isJobLoading && (
            <div className="processing-container">
              <div className="processing-header">
                <img src="/icon.svg" alt="archian ai" className="processing-icon" />
                <h3 className="processing-text">
                  {dataSource === 'sql' 
                    ? 'Extracting data from database...'
                    : dataSource === 'gsheets'
                      ? 'Extracting data from Google Sheets...'
                      : dataSource === 'salesforce'
                        ? 'Extracting data from Salesforce...'
                        : isReusableFlow 
                          ? 'Processing reusable flow...' 
                          : 'Processing your file...'
                  }
                </h3>
              </div>
              <div className="progress-bar-container">
                <div 
                  className="progress-bar-fill"
                  style={{ width: `${(dataSource === 'sql' || dataSource === 'gsheets') ? sqlTaskProgress : jobProgress}%` }}
                ></div>
              </div>
              <div className="progress-percentage">
                {(dataSource === 'sql' || dataSource === 'gsheets') ? sqlTaskProgress : jobProgress}% complete
              </div>
              {((dataSource === 'sql' || dataSource === 'gsheets') && sqlTaskMessage) || (dataSource === 'salesforce' && jobMessage) ? (
                <div className="progress-message" style={{
                  marginTop: '8px',
                  fontSize: '14px',
                  color: '#6b7280',
                  textAlign: 'center'
                }}>
                  {dataSource === 'salesforce' ? jobMessage : sqlTaskMessage}
                </div>
              ) : null}
              {((isReusableFlow && (dataSource === 'file' || dataSource === 'salesforce')) || (isSqlReusableFlow && dataSource === 'sql') || (isGoogleSheetsReusableFlow && dataSource === 'gsheets')) && (
                <div className="reusable-flow-message" style={{
                  marginTop: '16px',
                  padding: '12px 16px',
                  backgroundColor: '#f0f9ff',
                  border: '1px solid #0ea5e9',
                  borderRadius: '8px',
                  textAlign: 'center',
                  fontSize: '14px',
                  color: '#0c4a6e'
                }}>
                  <strong>💡 You can close this page and continue working!</strong><br />
                  We'll email you once the {dataSource === 'gsheets' ? 'Google Spreadsheet' : dataSource === 'sql' ? 'SQL Database' : dataSource === 'salesforce' ? 'Salesforce' : 'File'} migration is completed.
                </div>
              )}
            </div>
          )}
          
          {/* Job Error State */}
          {(jobError || sqlTaskError) && (
            <div className="job-error-container" style={{ 
              padding: '20px', 
              textAlign: 'center', 
              backgroundColor: '#fef2f2', 
              border: '1px solid #ef4444', 
              borderRadius: '8px', 
              marginBottom: '20px' 
            }}>
              <div style={{ fontSize: '18px', marginBottom: '10px', color: '#dc2626' }}>
                ❌ {dataSource === 'sql'
                      ? 'SQL extraction failed'
                      : dataSource === 'gsheets'
                        ? 'Google Sheets extraction failed'
                        : dataSource === 'salesforce'
                          ? 'Salesforce extraction failed'
                          : 'Processing failed'}
              </div>
              <div style={{ fontSize: '14px', color: '#7f1d1d' }}>
                {sqlTaskError || jobError}
              </div>
              {jobMessage && (
                <div style={{ fontSize: '13px', color: '#b91c1c', marginTop: '6px' }}>
                  {jobMessage}
                </div>
              )}
              {(dataSource === 'sql' || dataSource === 'gsheets') && sqlTaskId && (
                <div style={{ fontSize: '12px', color: '#9ca3af', marginTop: '8px' }}>
                  Task ID: {sqlTaskId}
                </div>
              )}
              <button 
                onClick={() => navigate('/chat')}
                style={{
                  marginTop: '10px',
                  padding: '8px 16px',
                  backgroundColor: '#dc2626',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer'
                }}
              >
                Try Again
              </button>
            </div>
          )}
          
          {/* Professional Plan Limit Notification */}
          {planLimitInfo && planLimitInfo.limitation_applied && (
            <div className="plan-limit-notification">
              <div className="plan-limit-info">
                <img src="/icon.svg" alt="archian ai" style={{ width: '16px', height: '16px', flexShrink: 0 }} />
                <span className="plan-limit-text">
                  {planLimitInfo.is_plan_limit_reached ? (
                    <>
                      <strong>📁 Plan Limit Reached</strong><br />
                      <span style={{ color: '#dc2626', fontSize: '14px', display: 'block', marginTop: '4px' }}>
                        Insufficient credits for extraction (balance: {planLimitInfo.credit_balance ?? planLimitInfo.remaining_credits ?? 0}).<br />
                        Add credits or upgrade your plan.
                      </span>
                    </>
                  ) : (
                    <>
                      <strong>{planLimitInfo.plan_type}</strong> plan: {(planLimitInfo.credit_balance ?? planLimitInfo.remaining_credits ?? 0).toLocaleString()} credits remaining{planLimitInfo.can_extract != null ? ` • up to ${planLimitInfo.can_extract} records this run` : ''}
                      {planLimitInfo.original_file_records != null &&
                        records.length > 0 &&
                        planLimitInfo.original_file_records > records.length && (
                        <span style={{ color: '#dc2626', fontSize: '12px', display: 'block', marginTop: '2px' }}>
                          Extracted {records.length.toLocaleString()} of {planLimitInfo.original_file_records.toLocaleString()} records (plan credit limit)
                        </span>
                      )}
                    </>
                  )}
                </span>
              </div>
              {showUpgradeOption && (
                <button 
                  className="upgrade-plan-button-compact"
                  onClick={() => navigate('/plans')}
                >
                  Upgrade Plan
                </button>
              )}
            </div>
          )}
          
          <p>
            {planLimitInfo && planLimitInfo.is_plan_limit_reached ? (
              <>
                <strong>No records extracted</strong> - Plan limit reached
              </>
            ) : (
              <>
                Showing <strong>{records.length}</strong> records
              </>
            )}
          </p>


          
          {/* Only show table if plan limit is not reached */}
          {!(planLimitInfo && planLimitInfo.is_plan_limit_reached) && (
            <>
              <div
                ref={tableScrollRef}
                style={{
                  overflowX: 'auto',
                  overflowY: useVirtual ? 'auto' : 'visible',
                  maxHeight: useVirtual ? 500 : undefined,
                }}
              >
                {useVirtual ? (
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: `minmax(40px, auto) repeat(${getColumnKeys().length}, minmax(100px, 1fr))`,
                      minWidth: 'fit-content',
                    }}
                  >
                    <div className="editable-table-virtual-header">#</div>
                    {getColumnKeys().map((k) => (
                      <div key={`hdr-${k}`} className="editable-table-virtual-header">
                        {camelCaseToLabel(k)}
                      </div>
                    ))}
                    <div
                      style={{
                        gridColumn: '1 / -1',
                        height: rowVirtualizer.getTotalSize(),
                        position: 'relative',
                      }}
                    >
                      {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                        const rec = records[virtualRow.index];
                        return (
                          <div
                            key={virtualRow.key}
                            style={{
                              position: 'absolute',
                              top: 0,
                              left: 0,
                              width: '100%',
                              height: `${virtualRow.size}px`,
                              transform: `translateY(${virtualRow.start}px)`,
                              display: 'grid',
                              gridTemplateColumns: `minmax(40px, auto) repeat(${getColumnKeys().length}, minmax(100px, 1fr))`,
                              alignItems: 'center',
                            }}
                          >
                            <div className="editable-table-virtual-cell">{virtualRow.index + 1}</div>
                            {getColumnKeys().map((k) => (
                              <div key={`cell-${virtualRow.index}-${k}`} className="editable-table-virtual-cell">
                                {renderInput(virtualRow.index, k, rec[k])}
                              </div>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <table className="editable-table results-table" role="grid">
                    <thead>
                      <tr>
                        <th>#</th>
                        {getColumnKeys().map((k) => (
                          <th key={`hdr-${k}`}>{camelCaseToLabel(k)}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {records
                        .slice(currentPage * pageSize, (currentPage + 1) * pageSize)
                        .map((rec, rowIdx) => {
                          const globalRowIdx = currentPage * pageSize + rowIdx;
                          return (
                            <tr key={`row-${globalRowIdx}`}>
                              <td>{globalRowIdx + 1}</td>
                              {getColumnKeys().map((k) => (
                                <td key={`cell-${globalRowIdx}-${k}`}>
                                  {renderInput(globalRowIdx, k, rec[k])}
                                </td>
                              ))}
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                )}

                {/* Professional Pagination Controls - only when not virtualized */}
                {records.length > pageSize && !useVirtual && (
                  <div className="pagination-container">
                    <div className="pagination-info">
                      Page {currentPage + 1} of {Math.ceil(records.length / pageSize)} • {records.length} total records
                    </div>
                    <div className="pagination-controls">
                      <button
                        onClick={async () => {
                          setIsPageChanging(true);
                          setCurrentPage((prev) => Math.max(prev - 1, 0));
                          setTimeout(() => setIsPageChanging(false), 300);
                        }}
                        disabled={currentPage === 0 || isPageChanging}
                        className="pagination-btn"
                      >
                        {isPageChanging ? '...' : '← Previous'}
                      </button>
                      <button
                        onClick={async () => {
                          setIsPageChanging(true);
                          setCurrentPage((prev) => prev + 1);
                          setTimeout(() => setIsPageChanging(false), 300);
                        }}
                        disabled={(currentPage + 1) * pageSize >= records.length || isPageChanging}
                        className="pagination-btn"
                      >
                        {isPageChanging ? '...' : 'Next →'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* AI Response Display - REMOVED since users can see updated data in table */}
        {/* Removed the promptResponse display section */}

      </div>



     
      
      {/* Alert Modal */}
      <AlertModal
        isOpen={alertModal.isOpen}
        onClose={closeAlert}
        title={alertModal.title}
        message={alertModal.message}
        type={alertModal.type}
      />
    </div>
  );
};

export default EditableTable;
