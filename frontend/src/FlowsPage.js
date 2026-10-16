import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiEye, FiTrash2, FiClock, FiPlay, FiShare2 } from 'react-icons/fi';
import api from './api';
import { useUserTimezone } from './hooks/useUserTimezone';
import { formatDateTimeForUser } from './utils/timezoneUtils';
import {
  getSourceTypeKey,
  getDestinationTypeKey,
  formatSourceLabel,
  formatDestinationLabel,
  supportsIncrementalSync,
  supportsBidirectionalSync,
} from './utils/flowDisplayUtils';
import { executeMappingFlowReuse } from './utils/mappingFlowReuse';
import AlertModal from './components/AlertModal';
import DeleteFlowModal from './components/DeleteFlowModal';
import ScheduleFlowModal from './components/ScheduleFlowModal';
import ResubmitFlowModal from './components/ResubmitFlowModal';
import EditFlowConnectionsModal from './components/EditFlowConnectionsModal';
import './FlowsPage.css';

const FlowsPage = () => {
  const FLOWS_PER_PAGE = 10;
  const navigate = useNavigate();
  const { userTimezone } = useUserTimezone();
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [currentPage, setCurrentPage] = useState(1);

  const [showResubmitModal, setShowResubmitModal] = useState(false);
  const [selectedFlowForResubmit, setSelectedFlowForResubmit] = useState(null);
  const hasInitialized = useRef(false); // Use ref to prevent duplicate calls
  
  // Alert modal state
  const [alertModal, setAlertModal] = useState({ isOpen: false, title: '', message: '', type: 'error' });
  
  // Delete flow modal state
  const [deleteFlowModal, setDeleteFlowModal] = useState({ isOpen: false, flowId: null, flowName: '' });
  const [isDeletingFlow, setIsDeletingFlow] = useState(false);
  
  // Schedule modal state
  const [scheduleModal, setScheduleModal] = useState({ isOpen: false, flowId: null, existingSchedule: null });
  const [isSavingSchedule, setIsSavingSchedule] = useState(false);
  const [incrementalTogglingId, setIncrementalTogglingId] = useState(null);
  const [editConnectionsOpen, setEditConnectionsOpen] = useState(false);
  const [editConnectionsFlow, setEditConnectionsFlow] = useState(null);
  const [editConnectionsDetailed, setEditConnectionsDetailed] = useState(null);
  const pendingRerunFlowRef = useRef(null);

  // Helper function to show alerts
  const showAlert = (title, message, type = 'error') => {
    setAlertModal({ isOpen: true, title, message, type });
  };
  
  const closeAlert = () => {
    setAlertModal({ isOpen: false, title: '', message: '', type: 'error' });
  };

  useEffect(() => {
    // Prevent multiple executions using ref
    if (hasInitialized.current) {
      return;
    }
    hasInitialized.current = true;

    const fetchFlows = async () => {
      try {
        const response = await api.get('/list-mapping-flows');
        if (response.data.status === 'success') {
          setFlows(response.data.flows);
        } else {
          showAlert('Error', 'Failed to load flows');
        }
      } catch (error) {
        console.error('Error fetching flows:', error);
        showAlert('Error', 'Error fetching flows');
      } finally {
        setLoading(false);
      }
    };

    fetchFlows();
  }, []);

  const handleUseFlow = (flow) => {
    executeMappingFlowReuse(flow, navigate, {
      showAlert,
      onFileUploadRequired: (f) => {
        setSelectedFlowForResubmit(f);
        setShowResubmitModal(true);
      },
      onReuseComplete: () => {},
      onConnectionStale: async (staleFlow) => {
        pendingRerunFlowRef.current = staleFlow;
        try {
          const res = await api.get(`/flow/${staleFlow.id}`);
          if (res.data?.status === 'success') {
            setEditConnectionsDetailed(res.data);
          } else {
            setEditConnectionsDetailed(null);
          }
        } catch (fetchErr) {
          console.warn('Could not load flow details for connection edit:', fetchErr);
          setEditConnectionsDetailed(null);
        }
        setEditConnectionsFlow(staleFlow);
        setEditConnectionsOpen(true);
      },
    });
  };

  const handleDeleteFlowClick = (flowId, flowName) => {
    setDeleteFlowModal({ isOpen: true, flowId, flowName });
  };

  const handleDeleteFlowConfirm = async () => {
    const { flowId } = deleteFlowModal;
    if (!flowId) return;

    setIsDeletingFlow(true);
    try {
      const response = await api.delete(`/flow/${flowId}`);
      if (response.data.status === 'success') {
        setFlows(prevFlows => prevFlows.filter(flow => flow.id !== flowId));

        setDeleteFlowModal({ isOpen: false, flowId: null, flowName: '' });
        showAlert('Success', 'Flow deleted successfully', 'success');
      } else {
        showAlert('Error', 'Failed to delete flow');
      }
    } catch (error) {
      console.error('Error deleting flow:', error);
      showAlert('Error', 'Error deleting flow');
    } finally {
      setIsDeletingFlow(false);
    }
  };

  const handleDeleteFlowCancel = () => {
    setDeleteFlowModal({ isOpen: false, flowId: null, flowName: '' });
  };

  const handleScheduleFlowClick = async (flowId) => {
    try {
      // Fetch current schedule
      const response = await api.get(`/flow/${flowId}/schedule`);
      if (response.data.status === 'success') {
        setScheduleModal({ 
          isOpen: true, 
          flowId, 
          existingSchedule: response.data.schedule 
        });
      } else {
        // If no schedule exists, open modal with empty schedule
        setScheduleModal({ isOpen: true, flowId, existingSchedule: null });
      }
    } catch (error) {
      console.error('Error fetching schedule:', error);
      // Open modal anyway (schedule might not exist)
      setScheduleModal({ isOpen: true, flowId, existingSchedule: null });
    }
  };

  const handleScheduleSave = async (scheduleData) => {
    const { flowId } = scheduleModal;
    if (!flowId) return;

    setIsSavingSchedule(true);
    try {
      const response = await api.post(`/flow/${flowId}/schedule`, scheduleData);
      if (response.data.status === 'success') {
        // Refresh flows list to get updated schedule info
        const flowsResponse = await api.get('/list-mapping-flows');
        if (flowsResponse.data.status === 'success') {
          setFlows(flowsResponse.data.flows);
        }
        
        setScheduleModal({ isOpen: false, flowId: null, existingSchedule: null });
        showAlert('Success', 'Schedule updated successfully', 'success');
      } else {
        showAlert('Error', 'Failed to update schedule');
      }
    } catch (error) {
      console.error('Error saving schedule:', error);
      showAlert('Error', `Failed to save schedule: ${error.response?.data?.detail || error.message}`);
    } finally {
      setIsSavingSchedule(false);
    }
  };

  const handleScheduleModalClose = () => {
    setScheduleModal({ isOpen: false, flowId: null, existingSchedule: null });
  };

  const handleIncrementalToggle = async (flow, nextEnabled) => {
    if (!flow?.id) return;
    setIncrementalTogglingId(flow.id);
    try {
      const response = await api.patch(`/flow/${flow.id}/incremental-sync`, {
        incremental_sync_enabled: nextEnabled,
      });
      if (response.data?.status === 'success') {
        setFlows((prev) =>
          prev.map((f) =>
            f.id === flow.id ? { ...f, incremental_sync_enabled: nextEnabled } : f,
          ),
        );
        showAlert(
          'Success',
          nextEnabled
            ? 'Incremental sync enabled for this flow.'
            : 'Incremental sync disabled for this flow.',
          'success',
        );
      } else {
        showAlert('Error', 'Failed to update incremental sync');
      }
    } catch (error) {
      console.error('Incremental sync toggle failed:', error);
      const d = error.response?.data?.detail;
      showAlert(
        'Error',
        typeof d === 'string' ? d : error.message || 'Failed to update incremental sync',
      );
    } finally {
      setIncrementalTogglingId(null);
    }
  };

  const closeResubmitModal = () => {
    setShowResubmitModal(false);
    setSelectedFlowForResubmit(null);
  };

  const filteredFlows = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (!term) return flows;

    return flows.filter((flow) => {
      const searchableFields = [
        flow.migration_name,
        flow.document_type,
        flow.object_label,
        flow.table_name,
        flow.source_name,
        flow.user_prompt,
        formatSourceLabel(flow),
        formatDestinationLabel(flow),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

      return searchableFields.includes(term);
    });
  }, [flows, searchTerm]);

  const totalPages = Math.max(1, Math.ceil(filteredFlows.length / FLOWS_PER_PAGE));
  const startIndex = (currentPage - 1) * FLOWS_PER_PAGE;
  const paginatedFlows = filteredFlows.slice(startIndex, startIndex + FLOWS_PER_PAGE);

  useEffect(() => {
    setCurrentPage(1);
  }, [searchTerm]);

  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  return (
    <div className="flows-container">
      <div className="flows-header">
        <h2><FiShare2 /> Flows</h2>
        <p className="flows-description">
          View and manage your flows. Each flow contains the field mappings and schema configuration for a specific document type.
        </p>
      </div>

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading your flows...</p>
        </div>
      ) : flows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">📋</div>
          <h3>No flows found</h3>
          <p>You haven't created any reusable pipeline flows yet.</p>
          <p>Run a pipeline and save it as your first flow!</p>
        </div>
      ) : (
        <>
          <div className="flows-controls">
            <input
              type="text"
              className="flows-search-input"
              placeholder="Search flows by name, source, destination, or prompt..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
            <span className="flows-count">
              Showing {paginatedFlows.length} of {filteredFlows.length} flow{filteredFlows.length === 1 ? '' : 's'}
            </span>
          </div>

          {filteredFlows.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">🔎</div>
              <h3>No matching flows</h3>
              <p>Try a different search term.</p>
            </div>
          ) : (
            <>
              <div className="flows-grid">
                {paginatedFlows.map((flow, idx) => (
            <div key={`${flow.id || 'flow'}-${startIndex + idx}`} className="flow-card">
              <div className="flow-header">
                <h3 className="flow-name">{flow.migration_name}</h3>
                <span className="flow-date">
                  {formatDateTimeForUser(flow.created_at, userTimezone)}
                </span>
              </div>
              <div className="flow-content">
                <div className="flow-meta-row">
                  <span
                    className={`flow-tag source-tag source-${getSourceTypeKey(flow)}`}
                  >
                    Source: {formatSourceLabel(flow)}
                  </span>
                  <span
                    className={`flow-tag destination-tag destination-${getDestinationTypeKey(flow)}`}
                  >
                    Destination: {formatDestinationLabel(flow)}
                  </span>
                </div>
                <div className="flow-info">
                  <p className="flow-document-type">
                    <strong>Document Type:</strong> {flow.document_type || 'N/A'}
                  </p>
                  <p>
                    <strong>Source Target:</strong>{' '}
                    {flow.object_label || flow.table_name || flow.source_name || 'N/A'}
                  </p>
                  
                  {flow.user_prompt && (
                    <p className="flow-user-prompt">
                      <strong>Prompt:</strong> {flow.user_prompt}
                    </p>
                  )}
                  <p className="flow-mappings">
                    <strong>Field Mappings:</strong> {flow.field_mappings_count || 0}
                  </p>

                  {supportsBidirectionalSync(flow) && flow.bidirectional_sync_enabled && (
                    <p className="flow-mappings">
                      <strong>Bidirectional sync:</strong> on
                    </p>
                  )}
                  {supportsBidirectionalSync(flow) && flow.reverse_flow_id && !flow.bidirectional_sync_enabled && (
                    <p className="flow-mappings">
                      <strong>Reverse leg:</strong> created (enable on flow detail)
                    </p>
                  )}

                  {supportsIncrementalSync(flow) && (
                    <div className="flow-incremental-row">
                      <div className="flow-toggle flow-toggle--card">
                        <span className="flow-toggle-text" id={`incremental-sync-label-${flow.id}`}>
                          Incremental sync
                        </span>
                        <label
                          className="flow-toggle-switch-wrap"
                          htmlFor={`incremental-sync-toggle-${flow.id}`}
                        >
                          <input
                            id={`incremental-sync-toggle-${flow.id}`}
                            type="checkbox"
                            role="switch"
                            className="flow-toggle-input"
                            checked={!!flow.incremental_sync_enabled}
                            disabled={incrementalTogglingId === flow.id}
                            aria-checked={!!flow.incremental_sync_enabled}
                            aria-labelledby={`incremental-sync-label-${flow.id}`}
                            onChange={(e) => handleIncrementalToggle(flow, e.target.checked)}
                          />
                          <span className="flow-toggle-track" aria-hidden="true">
                            <span className="flow-toggle-thumb" />
                          </span>
                        </label>
                      </div>
                    </div>
                  )}
                  
                  {flow.schedule && flow.schedule.schedule_enabled && (
                    <div className="flow-schedule-info">
                      <div className="schedule-status">
                        <FiClock className="schedule-icon" />
                        <span className="schedule-frequency">
                          {flow.schedule.schedule_frequency?.charAt(0).toUpperCase() + flow.schedule.schedule_frequency?.slice(1)}
                        </span>
                      </div>
                      {flow.schedule.next_run_at && (
                        <p className="schedule-next-run">
                          <strong>Next run:</strong> {formatDateTimeForUser(flow.schedule.next_run_at, flow.schedule.schedule_timezone || userTimezone)}
                        </p>
                      )}
                    </div>
                  )}
                </div>
                <div className="flow-actions">
                  <button
                    className="btn btn-primary"
                    onClick={() => navigate(`/flows/${flow.id}`)}
                  >
                    <FiEye /> View
                  </button>
                  <button
                    className="btn btn-secondary"
                    onClick={() => handleUseFlow(flow)}
                  >
                    <FiPlay /> Run
                  </button>
                  {getSourceTypeKey(flow) !== 'file' && (
                    <button
                      className={`btn ${flow.schedule?.schedule_enabled ? 'btn-schedule-active' : 'btn-schedule'}`}
                      onClick={() => handleScheduleFlowClick(flow.id)}
                    >
                      <FiClock /> Schedule
                    </button>
                  )}
                  <button
                    className="btn btn-danger"
                    onClick={() => handleDeleteFlowClick(flow.id, flow.migration_name)}
                  >
                    <FiTrash2 /> Delete
                  </button>
                </div>
              </div>
            </div>
                ))}
              </div>

              {totalPages > 1 && (
                <div className="flows-pagination">
                  <button
                    className="pagination-btn"
                    onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
                    disabled={currentPage === 1}
                  >
                    Previous
                  </button>
                  <span className="pagination-info">
                    Page {currentPage} of {totalPages}
                  </span>
                  <button
                    className="pagination-btn"
                    onClick={() => setCurrentPage((prev) => Math.min(totalPages, prev + 1))}
                    disabled={currentPage === totalPages}
                  >
                    Next
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}

      <ResubmitFlowModal
        isOpen={showResubmitModal}
        flow={selectedFlowForResubmit}
        onClose={closeResubmitModal}
      />

      <EditFlowConnectionsModal
        isOpen={editConnectionsOpen}
        onClose={() => {
          setEditConnectionsOpen(false);
          setEditConnectionsFlow(null);
          setEditConnectionsDetailed(null);
          pendingRerunFlowRef.current = null;
        }}
        flowId={editConnectionsFlow?.id}
        flow={editConnectionsFlow}
        detailedFlow={editConnectionsDetailed}
        onSaved={async () => {
          const flowToRerun = pendingRerunFlowRef.current || editConnectionsFlow;
          setEditConnectionsOpen(false);
          setEditConnectionsDetailed(null);
          if (flowToRerun) {
            showAlert(
              'Connections updated',
              'Your flow connections were saved. Starting the flow run…',
              'success',
            );
            handleUseFlow(flowToRerun);
          }
          pendingRerunFlowRef.current = null;
        }}
      />

      {/* Alert Modal */}
      <AlertModal
        isOpen={alertModal.isOpen}
        onClose={closeAlert}
        title={alertModal.title}
        message={alertModal.message}
        type={alertModal.type}
      />

      {/* Delete Flow Modal */}
      <DeleteFlowModal
        isOpen={deleteFlowModal.isOpen}
        onClose={handleDeleteFlowCancel}
        onConfirm={handleDeleteFlowConfirm}
        flowName={deleteFlowModal.flowName}
        isLoading={isDeletingFlow}
      />

      <ScheduleFlowModal
        isOpen={scheduleModal.isOpen}
        flowId={scheduleModal.flowId}
        existingSchedule={scheduleModal.existingSchedule}
        onClose={handleScheduleModalClose}
        onSave={handleScheduleSave}
        isLoading={isSavingSchedule}
      />
    </div>
  );
};

export default FlowsPage;
