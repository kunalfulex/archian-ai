import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  FiArrowLeft,
  FiRefreshCw,
  FiTrash2,
  FiClock,
  FiShare2,
  FiEdit3,
  FiCheck,
  FiX,
  FiCopy,
  FiActivity,
  FiChevronDown,
} from 'react-icons/fi';
import api from './api';
import { useUserTimezone } from './hooks/useUserTimezone';
import { formatDateTimeForUser } from './utils/timezoneUtils';
import {
  isExtractedSchemaEmpty,
  isMigrationRulesEmpty,
  isDeepEmptyValue,
  labelForMigrationRuleKey,
  getSourceTypeKey,
  getDestinationTypeKey,
  formatSourceLabel,
  formatDestinationLabel,
  buildSourceDataFormatRows,
  supportsIncrementalSync,
  supportsBidirectionalSync,
} from './utils/flowDisplayUtils';
import { normalizeMigrationRulesForDisplay } from './utils/migrationRulesKeys';
import { shouldOpenMigrationRulesReviewModal } from './utils/migrationRulesValidationUtils';
import { executeMappingFlowReuse } from './utils/mappingFlowReuse';
import {
  watermarkPickerKind,
  isoWatermarkToInputValue,
  inputValueToIsoWatermark,
} from './utils/incrementalWatermarkUtils';
import AlertModal from './components/AlertModal';
import DeleteFlowModal from './components/DeleteFlowModal';
import RenameFlowModal from './components/RenameFlowModal';
import CloneFlowModal from './components/CloneFlowModal';
import ScheduleFlowModal from './components/ScheduleFlowModal';
import ResubmitFlowModal from './components/ResubmitFlowModal';
import { MigrationRuleHumanContent } from './components/MigrationRulesHumanView';
import FlowMappingEditorModal from './components/FlowMappingEditorModal';
import FlowFieldMappingsGroupedView from './components/FlowFieldMappingsGroupedView';
import EditFlowConnectionsModal from './components/EditFlowConnectionsModal';
import MigrationRulesValidationModal from './components/MigrationRulesValidationModal';
import './FlowsPage.css';

const SHOW_FIELD_MAPPING_EDITOR = true;

const FlowDetailPage = () => {
  const { flowId } = useParams();
  const navigate = useNavigate();
  const { userTimezone } = useUserTimezone();

  const [detailedFlow, setDetailedFlow] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const [showResubmitModal, setShowResubmitModal] = useState(false);
  const [isStartingMigration, setIsStartingMigration] = useState(false);

  const [alertModal, setAlertModal] = useState({ isOpen: false, title: '', message: '', type: 'error' });
  const [deleteFlowModal, setDeleteFlowModal] = useState({ isOpen: false, flowId: null, flowName: '' });
  const [isDeletingFlow, setIsDeletingFlow] = useState(false);

  const [scheduleModal, setScheduleModal] = useState({ isOpen: false, flowId: null, existingSchedule: null });
  const [isSavingSchedule, setIsSavingSchedule] = useState(false);

  const [renameFlowOpen, setRenameFlowOpen] = useState(false);
  const [isSavingFlowName, setIsSavingFlowName] = useState(false);

  const [cloneModalOpen, setCloneModalOpen] = useState(false);
  const [isCloningFlow, setIsCloningFlow] = useState(false);
  const [cloneServerError, setCloneServerError] = useState(null);

  const [mappingEditorOpen, setMappingEditorOpen] = useState(false);
  const [editConnectionsOpen, setEditConnectionsOpen] = useState(false);
  const [migrationRulesValidationOpen, setMigrationRulesValidationOpen] = useState(false);
  const [migrationRulesModalMode, setMigrationRulesModalMode] = useState('after_connection');
  /** 'connection' | 'mapping' — used for toasts after the validation modal completes */
  const [migrationRulesTrigger, setMigrationRulesTrigger] = useState('connection');

  const [cursorFieldDraft, setCursorFieldDraft] = useState('');
  const [cursorFieldOptions, setCursorFieldOptions] = useState([]);
  const [cursorFieldOptionsHint, setCursorFieldOptionsHint] = useState(null);
  const [cursorFieldOptionsLoading, setCursorFieldOptionsLoading] = useState(false);
  const [cursorSuggestionsOpen, setCursorSuggestionsOpen] = useState(false);
  /** When suggestion options exist, Save is enabled only after user picks from the list (not free typing). */
  const [cursorFieldChosenFromList, setCursorFieldChosenFromList] = useState(false);
  const incrementalCursorComboRef = useRef(null);
  const [isSavingIncremental, setIsSavingIncremental] = useState(false);
  const [isSavingCursorField, setIsSavingCursorField] = useState(false);
  const [isResettingIncremental, setIsResettingIncremental] = useState(false);
  const [isSavingBidirectional, setIsSavingBidirectional] = useState(false);
  const [isCreatingReverseLeg, setIsCreatingReverseLeg] = useState(false);
  const [watermarkDraft, setWatermarkDraft] = useState('');
  const [isSavingWatermark, setIsSavingWatermark] = useState(false);
  const [watermarkEditOpen, setWatermarkEditOpen] = useState(false);
  const watermarkInputRef = useRef(null);

  const showAlert = (title, message, type = 'error') => {
    setAlertModal({ isOpen: true, title, message, type });
  };

  const closeAlert = () => {
    setAlertModal({ isOpen: false, title: '', message: '', type: 'error' });
  };

  const loadDetails = useCallback(async () => {
    if (!flowId) return;
    setLoading(true);
    setLoadError(null);
    setDetailedFlow(null);
    try {
      const response = await api.get(`/flow/${flowId}`);
      if (response.data.status === 'success') {
        setDetailedFlow(response.data);
      } else {
        setLoadError('Failed to load flow details');
      }
    } catch (error) {
      console.error('Error fetching flow details:', error);
      setLoadError('Error fetching flow details');
    } finally {
      setLoading(false);
    }
  }, [flowId]);

  useEffect(() => {
    loadDetails();
  }, [loadDetails]);

  const flow = detailedFlow?.flow;
  const flowSourceType = flow?.source_type;
  const flowExtractedSchemaType = flow?.extracted_schema_type;
  const flowIncrementalCursorField = flow?.incremental_cursor_field;
  const flowIncrementalWatermark = flow?.incremental_high_watermark;

  useEffect(() => {
    if (flow?.incremental_cursor_field != null && flow.incremental_cursor_field !== '') {
      setCursorFieldDraft(flow.incremental_cursor_field);
    } else {
      setCursorFieldDraft('');
    }
    setCursorFieldChosenFromList(false);
  }, [flow?.id, flow?.incremental_cursor_field]);

  useEffect(() => {
    if (!flowId) {
      setCursorFieldOptions([]);
      setCursorFieldOptionsHint(null);
      return;
    }
    const st = (flowSourceType || flowExtractedSchemaType || 'file').toLowerCase();
    if (st !== 'sql' && st !== 'salesforce') {
      setCursorFieldOptions([]);
      setCursorFieldOptionsHint(null);
      return;
    }
    let cancelled = false;
    setCursorFieldOptionsLoading(true);
    setCursorFieldOptionsHint(null);
    api
      .get(`/flow/${flowId}/incremental-sync/cursor-field-options`)
      .then((res) => {
        if (cancelled) return;
        setCursorFieldOptions(Array.isArray(res.data?.options) ? res.data.options : []);
        setCursorFieldOptionsHint(res.data?.hint || null);
      })
      .catch(() => {
        if (!cancelled) {
          setCursorFieldOptions([]);
          setCursorFieldOptionsHint(null);
        }
      })
      .finally(() => {
        if (!cancelled) setCursorFieldOptionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [flowId, flowSourceType, flowExtractedSchemaType]);

  const cursorFieldSaveEnabled = useMemo(() => {
    if (!flow) return false;
    const draft = cursorFieldDraft.trim();
    const saved = (flow.incremental_cursor_field || '').trim();
    if (draft === saved) return false;
    if (cursorFieldOptions.length > 0) {
      return cursorFieldChosenFromList;
    }
    return true;
  }, [flow, cursorFieldDraft, cursorFieldOptions.length, cursorFieldChosenFromList]);

  const incrementalWatermarkPickerKind = useMemo(
    () => watermarkPickerKind(flow, cursorFieldDraft, cursorFieldOptions),
    [flow, cursorFieldDraft, cursorFieldOptions],
  );

  useEffect(() => {
    setWatermarkEditOpen(false);
  }, [flowId]);

  useEffect(() => {
    if (!flowId) {
      setWatermarkDraft('');
      return;
    }
    if (watermarkEditOpen) return;
    const st = (flowSourceType || flowExtractedSchemaType || 'file').toLowerCase();
    if (st !== 'sql' && st !== 'salesforce') {
      setWatermarkDraft('');
      return;
    }
    const flowSlice = {
      source_type: flowSourceType,
      extracted_schema_type: flowExtractedSchemaType,
      incremental_cursor_field: flowIncrementalCursorField,
    };
    const kind = watermarkPickerKind(flowSlice, cursorFieldDraft, cursorFieldOptions);
    setWatermarkDraft(isoWatermarkToInputValue(flowIncrementalWatermark, kind));
  }, [
    flowId,
    flowIncrementalWatermark,
    flowIncrementalCursorField,
    flowSourceType,
    flowExtractedSchemaType,
    cursorFieldDraft,
    cursorFieldOptions,
    watermarkEditOpen,
  ]);

  useEffect(() => {
    if (!watermarkEditOpen) return;
    const id = requestAnimationFrame(() => watermarkInputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [watermarkEditOpen]);

  const watermarkApplyEnabled = useMemo(() => {
    if (!flow) return false;
    const kind = incrementalWatermarkPickerKind;
    const nextIso = inputValueToIsoWatermark(watermarkDraft.trim(), kind);
    const prevIso = flow.incremental_high_watermark
      ? new Date(flow.incremental_high_watermark).toISOString()
      : null;
    if (nextIso === null && prevIso === null) return false;
    return nextIso !== prevIso;
  }, [flow, watermarkDraft, incrementalWatermarkPickerKind]);

  const beginWatermarkEdit = useCallback(() => {
    if (!flow) return;
    const kind = incrementalWatermarkPickerKind;
    setWatermarkDraft(isoWatermarkToInputValue(flow.incremental_high_watermark, kind));
    setWatermarkEditOpen(true);
  }, [flow, incrementalWatermarkPickerKind]);

  const cancelWatermarkEdit = useCallback(() => {
    if (!flow) return;
    setWatermarkDraft(
      isoWatermarkToInputValue(flow.incremental_high_watermark, incrementalWatermarkPickerKind),
    );
    setWatermarkEditOpen(false);
  }, [flow, incrementalWatermarkPickerKind]);

  const filteredCursorFieldOptions = useMemo(() => {
    if (!cursorFieldOptions.length) return [];
    const q = cursorFieldDraft.trim().toLowerCase();
    const base = q
      ? cursorFieldOptions.filter((o) => {
          const v = (o.value || '').toLowerCase();
          const lab = (o.label || '').toLowerCase();
          const dt = (o.data_type || '').toLowerCase();
          return v.includes(q) || lab.includes(q) || dt.includes(q);
        })
      : cursorFieldOptions;
    return base.slice(0, 50);
  }, [cursorFieldOptions, cursorFieldDraft]);

  useEffect(() => {
    if (!cursorSuggestionsOpen) return undefined;
    const onDocMouseDown = (e) => {
      if (incrementalCursorComboRef.current && !incrementalCursorComboRef.current.contains(e.target)) {
        setCursorSuggestionsOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [cursorSuggestionsOpen]);

  useEffect(() => {
    if (!cursorFieldOptions.length) setCursorSuggestionsOpen(false);
  }, [cursorFieldOptions.length]);

  const handleUseFlow = async () => {
    if (!flow) return;
    setIsStartingMigration(true);
    await executeMappingFlowReuse(flow, navigate, {
      showAlert,
      onFileUploadRequired: () => {
        setShowResubmitModal(true);
      },
      onReuseComplete: () => {
        setIsStartingMigration(false);
      },
    });
  };

  const handleDeleteFlowClick = () => {
    if (!flow) return;
    setDeleteFlowModal({ isOpen: true, flowId: flow.id, flowName: flow.migration_name });
  };

  const cloneFlowInitialName = flow?.migration_name
    ? `${flow.migration_name} (copy)`
    : 'New flow (copy)';

  const handleCloneFlowSubmit = async (newName) => {
    if (!flowId) return;
    setCloneServerError(null);
    setIsCloningFlow(true);
    try {
      const res = await api.post(`/flow/${flowId}/clone`, { migration_name: newName });
      if (res.data?.status === 'success' && res.data.flow_id) {
        setCloneModalOpen(false);
        navigate(`/flows/${res.data.flow_id}`);
      } else {
        setCloneServerError('Clone failed. Please try again.');
      }
    } catch (err) {
      const d = err.response?.data?.detail;
      const msg =
        typeof d === 'string'
          ? d
          : Array.isArray(d)
            ? d.map((x) => (typeof x === 'string' ? x : x?.msg || JSON.stringify(x))).join(' ')
            : err.message || 'Clone failed.';
      setCloneServerError(msg);
    } finally {
      setIsCloningFlow(false);
    }
  };

  const handleRenameFlowSave = async (newName) => {
    if (!flowId) return;
    setIsSavingFlowName(true);
    try {
      const res = await api.patch(`/flow/${flowId}/name`, { migration_name: newName });
      if (res.data?.status === 'success') {
        const nextName = res.data.migration_name ?? newName;
        const updatedAt = res.data.updated_at;
        setDetailedFlow((prev) => {
          if (!prev?.flow) return prev;
          return {
            ...prev,
            flow: {
              ...prev.flow,
              migration_name: nextName,
              ...(updatedAt ? { updated_at: updatedAt } : {}),
            },
          };
        });
        setRenameFlowOpen(false);
        showAlert('Success', 'Flow name updated', 'success');
      } else {
        showAlert('Error', 'Failed to update flow name');
      }
    } catch (error) {
      const d = error.response?.data?.detail;
      showAlert(
        'Error',
        typeof d === 'string' ? d : error.message || 'Failed to update flow name'
      );
    } finally {
      setIsSavingFlowName(false);
    }
  };

  const handleDeleteFlowConfirm = async () => {
    const { flowId: id } = deleteFlowModal;
    if (!id) return;

    setIsDeletingFlow(true);
    try {
      const response = await api.delete(`/flow/${id}`);
      if (response.data.status === 'success') {
        setDeleteFlowModal({ isOpen: false, flowId: null, flowName: '' });
        showAlert('Success', 'Flow deleted successfully', 'success');
        navigate('/flows');
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

  const handleScheduleFlowClick = async () => {
    if (!flow) return;
    if (flow.parent_flow_id) {
      showAlert('Info', 'Schedule the primary bidirectional flow, not the reverse leg.', 'info');
      return;
    }
    try {
      const response = await api.get(`/flow/${flow.id}/schedule`);
      if (response.data.status === 'success') {
        setScheduleModal({
          isOpen: true,
          flowId: flow.id,
          existingSchedule: response.data.schedule,
        });
      } else {
        setScheduleModal({ isOpen: true, flowId: flow.id, existingSchedule: null });
      }
    } catch (error) {
      console.error('Error fetching schedule:', error);
      setScheduleModal({ isOpen: true, flowId: flow.id, existingSchedule: null });
    }
  };

  const handleScheduleSave = async (scheduleData) => {
    const id = scheduleModal.flowId;
    if (!id) return;

    setIsSavingSchedule(true);
    try {
      const response = await api.post(`/flow/${id}/schedule`, scheduleData);
      if (response.data.status === 'success') {
        setScheduleModal({ isOpen: false, flowId: null, existingSchedule: null });
        showAlert('Success', 'Schedule updated successfully', 'success');
        await loadDetails();
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

  const handleIncrementalToggleDetail = async (nextEnabled) => {
    if (!flowId) return;
    setIsSavingIncremental(true);
    try {
      const res = await api.patch(`/flow/${flowId}/incremental-sync`, {
        incremental_sync_enabled: nextEnabled,
      });
      if (res.data?.status === 'success') {
        await loadDetails();
        showAlert(
          'Success',
          nextEnabled
            ? 'Incremental sync enabled. Scheduled and manual runs will use watermarks after the next successful extraction.'
            : 'Incremental sync disabled.',
          'success',
        );
      } else {
        showAlert('Error', 'Failed to update incremental sync');
      }
    } catch (error) {
      const d = error.response?.data?.detail;
      showAlert(
        'Error',
        typeof d === 'string' ? d : error.message || 'Failed to update incremental sync',
      );
    } finally {
      setIsSavingIncremental(false);
    }
  };

  const handleSaveCursorField = async () => {
    if (!flowId || !flow) return;
    const trimmed = cursorFieldDraft.trim();
    const prev = (flow.incremental_cursor_field || '').trim();
    if (trimmed === prev) return;
    if (cursorFieldOptions.length > 0 && !cursorFieldChosenFromList) return;
    setIsSavingCursorField(true);
    try {
      const res = await api.patch(`/flow/${flowId}/incremental-sync`, {
        incremental_cursor_field: trimmed || null,
      });
      if (res.data?.status === 'success') {
        await loadDetails();
        showAlert('Success', 'Cursor field saved', 'success');
      } else {
        showAlert('Error', 'Failed to save cursor field');
      }
    } catch (error) {
      const d = error.response?.data?.detail;
      showAlert(
        'Error',
        typeof d === 'string' ? d : error.message || 'Failed to save cursor field',
      );
    } finally {
      setIsSavingCursorField(false);
    }
  };

  const handleSaveWatermark = async () => {
    if (!flowId || !flow) return;
    const kind = incrementalWatermarkPickerKind;
    const nextIso = inputValueToIsoWatermark(watermarkDraft.trim(), kind);
    const prevIso = flow.incremental_high_watermark
      ? new Date(flow.incremental_high_watermark).toISOString()
      : null;
    if (nextIso === prevIso || (nextIso === null && prevIso === null)) return;
    setIsSavingWatermark(true);
    try {
      const res = await api.patch(`/flow/${flowId}/incremental-sync`, {
        incremental_high_watermark: nextIso,
      });
      if (res.data?.status === 'success') {
        await loadDetails();
        setWatermarkEditOpen(false);
        showAlert(
          'Success',
          nextIso
            ? 'High watermark updated. The next run will filter rows after this instant (UTC).'
            : 'High watermark cleared for this flow.',
          'success',
        );
      } else {
        showAlert('Error', 'Failed to update watermark');
      }
    } catch (error) {
      const d = error.response?.data?.detail;
      showAlert(
        'Error',
        typeof d === 'string' ? d : error.message || 'Failed to update watermark',
      );
    } finally {
      setIsSavingWatermark(false);
    }
  };

  const handleResetIncrementalWatermark = async () => {
    if (!flowId) return;
    setIsResettingIncremental(true);
    try {
      const res = await api.post(`/flow/${flowId}/incremental-sync/reset`);
      if (res.data?.status === 'success') {
        await loadDetails();
        setWatermarkEditOpen(false);
        showAlert(
          'Success',
          'Incremental watermark cleared. The next run will fetch a full baseline for incremental sync.',
          'success',
        );
      } else {
        showAlert('Error', 'Failed to reset incremental state');
      }
    } catch (error) {
      const d = error.response?.data?.detail;
      showAlert(
        'Error',
        typeof d === 'string' ? d : error.message || 'Failed to reset incremental state',
      );
    } finally {
      setIsResettingIncremental(false);
    }
  };

  const handleCreateReverseLeg = async () => {
    if (!flowId) return;
    setIsCreatingReverseLeg(true);
    try {
      const res = await api.post(`/flow/${flowId}/bidirectional/reverse-leg`);
      if (res.data?.status === 'success') {
        await loadDetails();
        showAlert(
          'Success',
          'Reverse leg created. Review its SOQL/SQL and field mappings (link below), then enable bidirectional sync.',
          'success',
        );
      } else {
        showAlert('Error', 'Failed to create reverse leg');
      }
    } catch (error) {
      const d = error.response?.data?.detail;
      showAlert(
        'Error',
        typeof d === 'string' ? d : error.message || 'Failed to create reverse leg',
      );
    } finally {
      setIsCreatingReverseLeg(false);
    }
  };

  const handleBidirectionalToggleDetail = async (nextEnabled) => {
    if (!flowId) return;
    setIsSavingBidirectional(true);
    try {
      const res = await api.patch(`/flow/${flowId}/bidirectional-sync`, {
        bidirectional_sync_enabled: nextEnabled,
      });
      if (res.data?.status === 'success') {
        await loadDetails();
        showAlert(
          'Success',
          nextEnabled
            ? 'Bidirectional sync enabled. Each run executes the primary flow then the reverse leg. Use External IDs or stable keys to avoid duplicate records.'
            : 'Bidirectional sync disabled.',
          'success',
        );
      } else {
        showAlert('Error', 'Failed to update bidirectional sync');
      }
    } catch (error) {
      const d = error.response?.data?.detail;
      showAlert(
        'Error',
        typeof d === 'string' ? d : error.message || 'Failed to update bidirectional sync',
      );
    } finally {
      setIsSavingBidirectional(false);
    }
  };

  const validateMigrationRulesAfterSourceShapeChange = useCallback(
    async (kind) => {
      if (!flowId || !flow) return;
      if (getSourceTypeKey(flow) === 'file') return;
      const title = kind === 'mapping' ? 'Field mappings updated' : 'Connections updated';
      try {
        const res = await api.get(`/flow/${flowId}/migration-rules/validate`);
        const data = res.data;
        if (data.status !== 'success') {
          showAlert(
            title,
            'Could not verify migration rules against the current source. Use Edit rules on the flow page if you need to review them.',
            'info',
          );
          return;
        }
        if (!shouldOpenMigrationRulesReviewModal(data)) {
          showAlert('Success', title, 'success');
          return;
        }
        setMigrationRulesTrigger(kind === 'mapping' ? 'mapping' : 'connection');
        setMigrationRulesModalMode(kind === 'mapping' ? 'after_mapping' : 'after_connection');
        setMigrationRulesValidationOpen(true);
      } catch (error) {
        console.error('Migration rules validation failed:', error);
        showAlert(
          title,
          'Could not verify migration rules against the current source. Use Edit rules on the flow page if you need to review them.',
          'info',
        );
      }
    },
    [flowId, flow],
  );

  const handleMigrationRulesValidationComplete = useCallback(
    async (ev) => {
      const trigger = migrationRulesTrigger;
      await loadDetails();
      setMigrationRulesValidationOpen(false);
      setMigrationRulesModalMode('after_connection');
      setMigrationRulesTrigger('connection');
      if (ev.status === 'auto_ok') {
        showAlert(
          'Success',
          trigger === 'mapping' ? 'Field mappings updated' : 'Connections updated',
          'success',
        );
      } else if (ev.status === 'cleared') {
        showAlert(
          'Success',
          'Migration rules were removed. You can set them again when you run the flow.',
          'success',
        );
      } else if (ev.status === 'closed_with_issues') {
        showAlert(
          trigger === 'mapping' ? 'Field mappings updated' : 'Connections updated',
          'Some migration rules may need review or removal.',
          'info',
        );
      } else if (ev.status === 'closed_after_review') {
        showAlert(
          'Success',
          trigger === 'mapping' ? 'Field mappings updated' : 'Connections updated',
          'success',
        );
      }
      /* edit_closed: refresh only, no toast */
    },
    [loadDetails, migrationRulesTrigger],
  );

  const canEditMappings = !!flow && !!detailedFlow?.schema && getSourceTypeKey(flow) !== 'file';

  const renderFieldMappings = (fieldMappings, schemaRow) => {
    if (!fieldMappings || fieldMappings.length === 0) {
      return (
        <div className="field-mappings">
          <div className="field-mappings-heading-row flow-connections-section-header">
            <h4>Field Mappings</h4>
            {SHOW_FIELD_MAPPING_EDITOR && canEditMappings && schemaRow && (
              <button
                type="button"
                className="btn btn-secondary btn-sm flow-section-header-icon-btn flow-section-icon-btn"
                onClick={() => setMappingEditorOpen(true)}
                aria-label="Edit field mappings"
                title="Edit field mappings"
              >
                <FiEdit3 aria-hidden />
              </button>
            )}
          </div>
          <p className="no-mappings">No field mappings found</p>
        </div>
      );
    }

    return (
      <div className="field-mappings">
        <div className="field-mappings-heading-row flow-connections-section-header">
          <h4>Field Mappings</h4>
          {SHOW_FIELD_MAPPING_EDITOR && canEditMappings && schemaRow && (
            <button
              type="button"
              className="btn btn-secondary btn-sm flow-section-header-icon-btn flow-section-icon-btn"
              onClick={() => setMappingEditorOpen(true)}
              aria-label="Edit field mappings"
              title="Edit field mappings"
            >
              <FiEdit3 aria-hidden />
            </button>
          )}
        </div>
        <FlowFieldMappingsGroupedView fieldMappings={fieldMappings} />
      </div>
    );
  };

  const renderExtractedSchema = (schema) => {
    if (!schema || isExtractedSchemaEmpty(schema.extracted_schema)) {
      return null;
    }

    const { rows, fallbackRaw } = buildSourceDataFormatRows(schema.extracted_schema);
    const alignLeft = { textAlign: 'left' };
    /* Inline layout so field column keeps a real width (avoids squeezed “Fi…” labels if CSS load/order fights). */
    const sourceDataFormatTableStyle = {
      ...alignLeft,
      tableLayout: 'fixed',
      width: '100%',
      minWidth: '100%',
      borderCollapse: 'collapse',
    };
    const sourceDataFormatFieldColStyle = {
      ...alignLeft,
      minWidth: '16rem',
      width: '16rem',
      maxWidth: '16rem',
      boxSizing: 'border-box',
      whiteSpace: 'nowrap',
      verticalAlign: 'top',
      paddingRight: '1rem',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
    };
    const sourceDataFormatValueColStyle = {
      ...alignLeft,
      width: 'auto',
      minWidth: 0,
      verticalAlign: 'top',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
      overflowWrap: 'anywhere',
    };

    return (
      <div className="extracted-schema" style={alignLeft}>
        <h4 style={alignLeft}>Source data format</h4>
        <p className="extracted-schema-hint" style={alignLeft}>
          Sample field names and values from when this flow was saved.
        </p>
        <div className="schema-preview" style={alignLeft}>
          {fallbackRaw != null ? (
            <pre className="schema-preview-fallback" style={alignLeft}>
              {fallbackRaw}
            </pre>
          ) : rows.length === 0 ? (
            <p className="extracted-schema-empty" style={alignLeft}>
              No fields to show.
            </p>
          ) : (
            <table className="source-data-format-table" style={sourceDataFormatTableStyle}>
              <thead>
                <tr>
                  <th scope="col" style={sourceDataFormatFieldColStyle}>
                    Field
                  </th>
                  <th scope="col" style={sourceDataFormatValueColStyle}>
                    Value
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => (
                  <tr key={`${row.field}-${idx}`}>
                    <td
                      className="source-data-format-field"
                      style={sourceDataFormatFieldColStyle}
                      title={typeof row.field === 'string' ? row.field : undefined}
                    >
                      {row.field}
                    </td>
                    <td className="source-data-format-value" style={sourceDataFormatValueColStyle}>
                      {row.value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    );
  };

  const renderMigrationRules = (rawRules) => {
    const rules = normalizeMigrationRulesForDisplay(rawRules);
    const canEditRules = flow && getSourceTypeKey(flow) !== 'file';
    const openRulesEditor = () => {
      setMigrationRulesTrigger('connection');
      setMigrationRulesModalMode('edit');
      setMigrationRulesValidationOpen(true);
    };

    if (isMigrationRulesEmpty(rules)) {
      if (!canEditRules) return null;
      return (
        <div className="migration-rules-section">
          <div className="migration-rules-section-header">
            <h4>Migration rules</h4>
            <button
              type="button"
              className="btn btn-secondary btn-sm flow-section-header-icon-btn flow-section-icon-btn"
              onClick={openRulesEditor}
              aria-label="Edit migration rules"
              title="Edit migration rules"
            >
              <FiEdit3 aria-hidden />
            </button>
          </div>
          <p className="migration-rules-hint">
            No migration rules saved yet. Use the edit control to set missing-field preference or review after connecting a
            source.
          </p>
        </div>
      );
    }

    const entries = Object.entries(rules).filter(([, v]) => !isDeepEmptyValue(v));
    if (entries.length === 0) return null;

    return (
      <div className="migration-rules-section">
        <div className="migration-rules-section-header">
          <h4>Migration rules</h4>
          {canEditRules && (
            <button
              type="button"
              className="btn btn-secondary btn-sm flow-section-header-icon-btn flow-section-icon-btn"
              onClick={openRulesEditor}
              aria-label="Edit migration rules"
              title="Edit migration rules"
            >
              <FiEdit3 aria-hidden />
            </button>
          )}
        </div>
        <p className="migration-rules-hint">
          Cleanup, extraction, transformation, and deduplication settings saved with this flow.
        </p>
        {entries.map(([key, value]) => (
          <div key={key} className="migration-rule-block">
            <h5>{labelForMigrationRuleKey(key)}</h5>
            <div className="migration-rule-body">
              <MigrationRuleHumanContent ruleKey={key} value={value} />
            </div>
          </div>
        ))}
      </div>
    );
  };

  const pageTitle = loading ? 'Loading…' : flow?.migration_name || 'Flow';

  return (
    <div className="flows-container flow-detail-page">
      <div className="flow-detail-toolbar">
        <button type="button" className="flow-detail-back" onClick={() => navigate('/flows')}>
          <FiArrowLeft /> Back to flows
        </button>
      </div>

      <div className="flows-header flow-detail-header">
        <h2 className="flow-detail-page-title">
          <FiShare2 /> {pageTitle}
        </h2>
      </div>

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading flow details...</p>
        </div>
      ) : loadError ? (
        <div className="error-state flow-detail-error">
          <p>{loadError}</p>
          <button type="button" className="btn btn-primary" onClick={() => navigate('/flows')}>
            Back to flows
          </button>
        </div>
      ) : detailedFlow && flow ? (
        <div className="flow-detail-body">
          {flow.parent_flow_id && (
            <div className="schema-section flow-bidirectional-banner" role="status">
              <p className="flow-incremental-hint" style={{ marginBottom: 0 }}>
                This is the <strong>reverse leg</strong> of a bidirectional flow. Edit mappings and query here; use the
                primary flow to run or schedule both directions.{' '}
                <button
                  type="button"
                  className="btn btn-link btn-sm"
                  style={{ padding: 0, verticalAlign: 'baseline' }}
                  onClick={() => navigate(`/flows/${flow.parent_flow_id}`)}
                >
                  Open primary flow
                </button>
              </p>
            </div>
          )}
          <div className="flow-details">
            <div className="flow-info-section">
              <div className="flow-connections-section-header">
                <h4>Flow Information</h4>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm flow-section-header-icon-btn flow-section-icon-btn"
                  onClick={() => setRenameFlowOpen(true)}
                  aria-label="Edit flow name"
                  title="Edit flow name"
                >
                  <FiEdit3 aria-hidden />
                </button>
              </div>
              <div className="info-grid" style={{ marginTop: '8px' }}>
                <div className="info-item">
                  <span className="info-label">Flow name:</span>
                  <span className="info-value">{flow.migration_name || '—'}</span>
                </div>
                <div className="info-item">
                  <span className="info-label">Flow ID:</span>
                  <span className="info-value">{flow.id}</span>
                </div>
                <div className="info-item">
                  <span className="info-label">Created:</span>
                  <span className="info-value">{formatDateTimeForUser(flow.created_at, userTimezone)}</span>
                </div>
                <div className="info-item">
                  <span className="info-label">Updated:</span>
                  <span className="info-value">
                    {flow.updated_at
                      ? formatDateTimeForUser(flow.updated_at, userTimezone)
                      : formatDateTimeForUser(flow.created_at, userTimezone)}
                  </span>
                </div>
              </div>
            </div>

            {detailedFlow.schema && (
              <div className="schema-section">
                <h4>Document Schema</h4>
                <div className="info-grid">
                  <div className="info-item">
                    <span className="info-label">Document Type:</span>
                    <span className="info-value">{detailedFlow.schema.document_type}</span>
                  </div>
                  {detailedFlow.schema.user_prompt && (
                    <div className="info-item full-width">
                      <span className="info-label">Prompt:</span>
                      <span className="info-value user-prompt">{detailedFlow.schema.user_prompt}</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="schema-section flow-connections-section">
              <div className="flow-connections-section-header">
                <h4>Connections</h4>
                {flow && getSourceTypeKey(flow) !== 'file' && (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm flow-section-header-icon-btn flow-section-icon-btn"
                    onClick={() => setEditConnectionsOpen(true)}
                    aria-label="Edit connection"
                    title="Edit connection"
                  >
                    <FiEdit3 aria-hidden />
                  </button>
                )}
              </div>
              <div className="flow-meta-row" style={{ marginTop: '4px' }}>
                <span
                  className={`flow-tag source-tag source-${getSourceTypeKey(flow)}`}
                  style={{ fontSize: '15px', padding: '10px 18px' }}
                >
                  Source: {formatSourceLabel(flow)}
                </span>
                <span
                  className={`flow-tag destination-tag destination-${getDestinationTypeKey(flow)}`}
                  style={{ fontSize: '15px', padding: '10px 18px' }}
                >
                  Destination: {formatDestinationLabel(flow)}
                </span>
              </div>
              {flow.source_target && (
                <div className="info-grid" style={{ marginTop: '16px' }}>
                  <div className="info-item">
                    <span className="info-label">Source Target:</span>
                    <span className="info-value">{flow.source_target}</span>
                  </div>
                </div>
              )}
            </div>

            {flow && supportsIncrementalSync(flow) && (
              <div className="schema-section flow-incremental-section">
                <h4>Incremental sync</h4>
                <p className="flow-incremental-hint">
                  When enabled, scheduled and manual runs fetch only rows newer than the last successful extraction
                  (watermark). Salesforce defaults to <code>LastModifiedDate</code> if the cursor field is empty. SQL
                  flows should pick a date/time column (suggestions below when cached schema exists). Deletes are not
                  detected automatically.
                </p>
                <div className="flow-incremental-toggle-row">
                  <div className="flow-toggle flow-toggle--detail">
                    <span className="flow-toggle-text" id="incremental-sync-label-detail">
                      Enable incremental sync
                    </span>
                    <label className="flow-toggle-switch-wrap" htmlFor="incremental-sync-toggle-detail">
                      <input
                        id="incremental-sync-toggle-detail"
                        type="checkbox"
                        role="switch"
                        className="flow-toggle-input"
                        checked={!!flow.incremental_sync_enabled}
                        disabled={isSavingIncremental}
                        aria-checked={!!flow.incremental_sync_enabled}
                        aria-labelledby="incremental-sync-label-detail"
                        onChange={(e) => handleIncrementalToggleDetail(e.target.checked)}
                      />
                      <span className="flow-toggle-track" aria-hidden="true">
                        <span className="flow-toggle-thumb" />
                      </span>
                    </label>
                  </div>
                </div>
                {flow.incremental_sync_enabled && (
                  <>
                    <div className="flow-incremental-details-grid">
                      <div className="flow-incremental-cell flow-incremental-cell--cursor">
                        <label htmlFor="incremental-cursor-field" className="flow-incremental-cursor-label">
                          Cursor field {getSourceTypeKey(flow) === 'sql' ? '(required for SQL)' : '(optional)'}
                        </label>
                        {cursorFieldOptionsLoading && (
                          <p className="flow-incremental-hint flow-incremental-hint--tight">
                            Loading date/time field suggestions…
                          </p>
                        )}
                        {cursorFieldOptionsHint && !cursorFieldOptionsLoading && (
                          <p className="flow-incremental-hint flow-incremental-hint--tight">
                            {cursorFieldOptionsHint}
                          </p>
                        )}
                        <div className="flow-incremental-cursor-stack">
                          <div
                            className="flow-incremental-cursor-combo"
                            ref={incrementalCursorComboRef}
                            role="combobox"
                            aria-expanded={cursorSuggestionsOpen}
                            aria-haspopup="listbox"
                            aria-controls={
                              cursorFieldOptions.length > 0
                                ? `incremental-cursor-suggestions-${flowId}`
                                : undefined
                            }
                          >
                          <div className="flow-incremental-cursor-combo-field">
                            <input
                              id="incremental-cursor-field"
                              type="text"
                              className="flow-incremental-cursor-input flow-incremental-cursor-input--combo"
                              autoComplete="off"
                              placeholder={
                                getSourceTypeKey(flow) === 'salesforce'
                                  ? 'Type a field API name or pick a suggestion'
                                  : 'Type a column name or pick a suggestion'
                              }
                              value={cursorFieldDraft}
                              onChange={(e) => {
                                setCursorFieldDraft(e.target.value);
                                setCursorFieldChosenFromList(false);
                              }}
                              onFocus={() => {
                                if (cursorFieldOptions.length > 0) setCursorSuggestionsOpen(true);
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Escape') setCursorSuggestionsOpen(false);
                              }}
                              disabled={isSavingCursorField}
                            />
                            {cursorFieldOptions.length > 0 && (
                              <button
                                type="button"
                                className="flow-incremental-cursor-combo-toggle"
                                aria-label="Show date and time field suggestions"
                                disabled={isSavingCursorField}
                                onClick={() => setCursorSuggestionsOpen((o) => !o)}
                              >
                                <FiChevronDown
                                  aria-hidden
                                  className={
                                    cursorSuggestionsOpen
                                      ? 'flow-incremental-cursor-combo-chevron flow-incremental-cursor-combo-chevron--open'
                                      : 'flow-incremental-cursor-combo-chevron'
                                  }
                                />
                              </button>
                            )}
                          </div>
                          {cursorSuggestionsOpen &&
                            cursorFieldOptions.length > 0 &&
                            filteredCursorFieldOptions.length > 0 && (
                              <ul
                                id={`incremental-cursor-suggestions-${flowId}`}
                                className="flow-incremental-cursor-suggestions"
                                role="listbox"
                              >
                                {filteredCursorFieldOptions.map((opt) => (
                                  <li key={opt.value} role="presentation">
                                    <button
                                      type="button"
                                      role="option"
                                      aria-selected={cursorFieldDraft === opt.value}
                                      className="flow-incremental-cursor-suggestion-item"
                                      onMouseDown={(e) => e.preventDefault()}
                                      onClick={() => {
                                        setCursorFieldDraft(opt.value);
                                        setCursorFieldChosenFromList(true);
                                        setCursorSuggestionsOpen(false);
                                      }}
                                    >
                                      <span className="flow-incremental-cursor-suggestion-primary">
                                        {opt.label || opt.value}
                                      </span>
                                      {opt.data_type ? (
                                        <span className="flow-incremental-cursor-suggestion-meta">
                                          {opt.data_type}
                                        </span>
                                      ) : null}
                                    </button>
                                  </li>
                                ))}
                              </ul>
                            )}
                          {cursorSuggestionsOpen &&
                            cursorFieldOptions.length > 0 &&
                            filteredCursorFieldOptions.length === 0 && (
                              <div className="flow-incremental-cursor-suggestions flow-incremental-cursor-suggestions--empty">
                                No suggestions match your text. Adjust your search or pick from the list when visible.
                              </div>
                            )}
                          </div>
                          {cursorFieldOptions.length > 0 && !cursorFieldOptionsLoading && (
                            <p className="flow-incremental-hint flow-incremental-hint--above-save">
                              Pick a date/time field from the list to enable Save. Suggestions are from cached metadata
                              for <code>{flow.source_target || 'this source'}</code>.
                            </p>
                          )}
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm flow-incremental-action-wide flow-incremental-action-footer"
                            onClick={handleSaveCursorField}
                            disabled={isSavingCursorField || !cursorFieldSaveEnabled}
                          >
                            {isSavingCursorField ? 'Saving…' : 'Save'}
                          </button>
                        </div>
                      </div>

                      <div className="flow-incremental-cell flow-incremental-cell--status">
                        <div className="flow-incremental-status-block">
                          <div className="flow-incremental-status-row">
                            <span className="flow-incremental-status-label">Last extraction sync</span>
                            <span className="flow-incremental-status-value">
                              {flow.incremental_last_extraction_success_at
                                ? formatDateTimeForUser(flow.incremental_last_extraction_success_at, userTimezone)
                                : '—'}
                            </span>
                          </div>
                          <div className="flow-incremental-status-row flow-incremental-watermark-row">
                            <span className="flow-incremental-status-label">High watermark (UTC)</span>
                            {!watermarkEditOpen ? (
                              <div className="flow-incremental-watermark-display">
                                <span className="flow-incremental-status-value">
                                  {flow.incremental_high_watermark
                                    ? formatDateTimeForUser(flow.incremental_high_watermark, userTimezone)
                                    : '— (full baseline on next run)'}
                                </span>
                                <button
                                  type="button"
                                  className="flow-incremental-watermark-icon-btn"
                                  onClick={beginWatermarkEdit}
                                  aria-label="Edit high watermark"
                                  title="Edit high watermark"
                                >
                                  <FiEdit3 aria-hidden />
                                </button>
                              </div>
                            ) : (
                              <div className="flow-incremental-watermark-inline-edit">
                                <input
                                  ref={watermarkInputRef}
                                  type={incrementalWatermarkPickerKind === 'date' ? 'date' : 'datetime-local'}
                                  className="flow-incremental-watermark-input"
                                  value={watermarkDraft}
                                  onChange={(e) => setWatermarkDraft(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') {
                                      e.preventDefault();
                                      handleSaveWatermark();
                                    } else if (e.key === 'Escape') {
                                      e.preventDefault();
                                      cancelWatermarkEdit();
                                    }
                                  }}
                                  disabled={isSavingWatermark}
                                  aria-label={
                                    incrementalWatermarkPickerKind === 'date'
                                      ? 'High watermark date (UTC)'
                                      : 'High watermark date and time (UTC)'
                                  }
                                />
                                <button
                                  type="button"
                                  className="flow-incremental-watermark-icon-btn flow-incremental-watermark-icon-btn--save"
                                  onClick={handleSaveWatermark}
                                  disabled={isSavingWatermark || !watermarkApplyEnabled}
                                  aria-label={isSavingWatermark ? 'Saving' : 'Save high watermark'}
                                  title={isSavingWatermark ? 'Saving…' : 'Save'}
                                >
                                  {isSavingWatermark ? (
                                    <span className="flow-incremental-watermark-saving">…</span>
                                  ) : (
                                    <FiCheck aria-hidden />
                                  )}
                                </button>
                                <button
                                  type="button"
                                  className="flow-incremental-watermark-icon-btn"
                                  onClick={cancelWatermarkEdit}
                                  disabled={isSavingWatermark}
                                  aria-label="Cancel editing high watermark"
                                  title="Cancel"
                                >
                                  <FiX aria-hidden />
                                </button>
                              </div>
                            )}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm flow-incremental-action-wide flow-incremental-action-footer"
                          onClick={handleResetIncrementalWatermark}
                          disabled={isResettingIncremental}
                        >
                          {isResettingIncremental ? 'Resetting…' : 'Reset watermark'}
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}

            {flow && supportsBidirectionalSync(flow) && (
              <div className="schema-section flow-incremental-section">
                <h4>Bidirectional sync</h4>
                <p className="flow-incremental-hint">
                  Two-way sync for Salesforce and SQL (including Salesforce↔SQL). Creates a hidden reverse flow with
                  swapped extract/destination connectors and inverted field mappings; review the generated SOQL or SQL
                  before enabling. Each manual or scheduled run runs the primary flow, then the reverse leg. Prefer
                  External IDs or a stable correlation field so renames do not create duplicates.
                </p>
                <div className="flow-incremental-toggle-row" style={{ marginBottom: '12px' }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={handleCreateReverseLeg}
                    disabled={isCreatingReverseLeg}
                  >
                    {isCreatingReverseLeg ? 'Working…' : flow.reverse_flow_id ? 'Recreate reverse leg' : 'Create reverse leg'}
                  </button>
                  {flow.reverse_flow_id && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      style={{ marginLeft: '8px' }}
                      onClick={() => navigate(`/flows/${flow.reverse_flow_id}`)}
                    >
                      Edit reverse leg (mapping / query)
                    </button>
                  )}
                </div>
                {detailedFlow.reverse_flow && (
                  <p className="flow-incremental-hint flow-incremental-hint--tight">
                    Reverse flow: <strong>{detailedFlow.reverse_flow.migration_name}</strong>
                    {detailedFlow.reverse_flow.incremental_sync_enabled ? ' · incremental on' : ''}
                  </p>
                )}
                <div className="flow-incremental-toggle-row">
                  <div className="flow-toggle flow-toggle--detail">
                    <span className="flow-toggle-text" id="bidirectional-sync-label-detail">
                      Enable bidirectional sync
                    </span>
                    <label className="flow-toggle-switch-wrap" htmlFor="bidirectional-sync-toggle-detail">
                      <input
                        id="bidirectional-sync-toggle-detail"
                        type="checkbox"
                        role="switch"
                        className="flow-toggle-input"
                        checked={!!flow.bidirectional_sync_enabled}
                        disabled={isSavingBidirectional || !flow.reverse_flow_id}
                        aria-checked={!!flow.bidirectional_sync_enabled}
                        aria-labelledby="bidirectional-sync-label-detail"
                        onChange={(e) => handleBidirectionalToggleDetail(e.target.checked)}
                      />
                      <span className="flow-toggle-track" aria-hidden="true">
                        <span className="flow-toggle-thumb" />
                      </span>
                    </label>
                  </div>
                </div>
                {!flow.reverse_flow_id && (
                  <p className="flow-incremental-hint flow-incremental-hint--tight">
                    Create the reverse leg before enabling this toggle.
                  </p>
                )}
              </div>
            )}

            {renderFieldMappings(detailedFlow.field_mappings, detailedFlow.schema)}

            {renderMigrationRules(flow.migration_rules)}

            {detailedFlow.schema && renderExtractedSchema(detailedFlow.schema)}

            <div className="flow-actions-modal flow-detail-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleUseFlow}
                disabled={isStartingMigration}
              >
                <FiRefreshCw /> Run the flow
              </button>
              {getSourceTypeKey(flow) !== 'file' && (
                <button
                  type="button"
                  className={`btn ${flow.schedule?.schedule_enabled ? 'btn-schedule-active' : 'btn-schedule'}`}
                  onClick={handleScheduleFlowClick}
                  disabled={isStartingMigration || !!flow.parent_flow_id}
                >
                  <FiClock /> Schedule
                </button>
              )}
              <button
                type="button"
                className="btn btn-data-timeline"
                disabled={isStartingMigration}
                aria-label="Data Timeline"
                onClick={() => flowId && navigate(`/flows/${flowId}/timeline`)}
              >
                <FiActivity /> Data Timeline
              </button>
              <button
                type="button"
                className="btn btn-clone"
                onClick={() => {
                  setCloneServerError(null);
                  setCloneModalOpen(true);
                }}
                aria-label="Clone flow"
              >
                <FiCopy /> Clone
              </button>
              <button type="button" className="btn btn-danger" onClick={handleDeleteFlowClick}>
                <FiTrash2 /> Delete Flow
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <FlowMappingEditorModal
        isOpen={SHOW_FIELD_MAPPING_EDITOR && mappingEditorOpen}
        onClose={() => setMappingEditorOpen(false)}
        flowId={flowId}
        detailedFlow={detailedFlow}
        onSaved={async (fms, syncedSqlQuery) => {
          setDetailedFlow((prev) => {
            if (!prev) return prev;
            const next = { ...prev, field_mappings: fms };
            if (syncedSqlQuery != null && prev.schema) {
              next.schema = { ...prev.schema, sql_query: syncedSqlQuery };
            }
            return next;
          });
          await loadDetails();
          if (flow && getSourceTypeKey(flow) !== 'file') {
            await validateMigrationRulesAfterSourceShapeChange('mapping');
          } else {
            showAlert('Success', 'Field mappings updated', 'success');
          }
        }}
      />

      <EditFlowConnectionsModal
        isOpen={editConnectionsOpen}
        onClose={() => setEditConnectionsOpen(false)}
        flowId={flowId}
        flow={flow}
        detailedFlow={detailedFlow}
        onSaved={async (meta) => {
          await loadDetails();
          if (!meta?.connectionChanged) {
            showAlert('Success', 'Connections updated', 'success');
            return;
          }
          await validateMigrationRulesAfterSourceShapeChange('connection');
        }}
      />

      <MigrationRulesValidationModal
        isOpen={migrationRulesValidationOpen}
        flowId={flowId}
        mode={migrationRulesModalMode}
        onClose={() => {
          setMigrationRulesValidationOpen(false);
          setMigrationRulesModalMode('after_connection');
          setMigrationRulesTrigger('connection');
        }}
        onComplete={handleMigrationRulesValidationComplete}
      />

      <ResubmitFlowModal
        isOpen={showResubmitModal}
        flow={flow}
        onClose={() => setShowResubmitModal(false)}
      />

      <AlertModal
        isOpen={alertModal.isOpen}
        onClose={closeAlert}
        title={alertModal.title}
        message={alertModal.message}
        type={alertModal.type}
      />

      <DeleteFlowModal
        isOpen={deleteFlowModal.isOpen}
        onClose={handleDeleteFlowCancel}
        onConfirm={handleDeleteFlowConfirm}
        flowName={deleteFlowModal.flowName}
        isLoading={isDeletingFlow}
      />

      <RenameFlowModal
        isOpen={renameFlowOpen}
        onClose={() => !isSavingFlowName && setRenameFlowOpen(false)}
        initialName={flow?.migration_name || ''}
        onSave={handleRenameFlowSave}
        isLoading={isSavingFlowName}
      />

      <CloneFlowModal
        isOpen={cloneModalOpen}
        onClose={() => {
          if (!isCloningFlow) {
            setCloneModalOpen(false);
            setCloneServerError(null);
          }
        }}
        initialName={cloneFlowInitialName}
        onClone={handleCloneFlowSubmit}
        isLoading={isCloningFlow}
        serverError={cloneServerError}
        onClearServerError={() => setCloneServerError(null)}
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

export default FlowDetailPage;
