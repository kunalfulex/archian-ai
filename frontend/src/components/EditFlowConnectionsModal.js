import React, { useState, useEffect, useMemo, useRef } from 'react';
import Select from 'react-select';
import { FiX } from 'react-icons/fi';
import api from '../api';
import { getSourceTypeKey, getDestinationTypeKey } from '../utils/flowDisplayUtils';
import '../FlowsPage.css';

/** Normalize API type strings for comparison */
function isSheetsType(k) {
  const t = (k || '').toLowerCase();
  return t === 'gsheets' || t === 'google_sheets';
}

function isSqlType(k) {
  return (k || '').toLowerCase() === 'sql';
}

function isSalesforceType(k) {
  return (k || '').toLowerCase() === 'salesforce';
}

async function fetchSqlConnectorOptions() {
  const res = await api.get('/api/sql-connectors/');
  if (res.data?.status !== 'success' || !Array.isArray(res.data.connectors)) {
    return [];
  }
  return res.data.connectors.map((c) => ({
    value: String(c.id),
    label: c.name || c.database_name || String(c.id),
  }));
}

async function fetchGoogleSheetsOptions() {
  const res = await api.get('/google-sheets/categorized-spreadsheets');
  if (res.data?.status !== 'success' || !Array.isArray(res.data.spreadsheets)) {
    return [];
  }
  return res.data.spreadsheets.map((s) => {
    const ugsRowIds = Array.isArray(s.sheets)
      ? s.sheets.map((sh) => (sh.id != null ? String(sh.id) : '')).filter(Boolean)
      : [];
    return {
      value: String(s.spreadsheet_id),
      label: s.spreadsheet_name
        ? `${s.spreadsheet_name}${s.google_account_email ? ` · ${s.google_account_email}` : ''}`
        : String(s.spreadsheet_id),
      ugsRowIds,
    };
  });
}

async function fetchSalesforceOptions() {
  const res = await api.get('/api/salesforce-connection');
  const connections = res.data?.connections;
  if (!Array.isArray(connections)) {
    return [];
  }
  return connections.map((c) => ({
    value: String(c.token_id || ''),
    label: c.org_display_name || c.connection_name || c.instance_url || String(c.org_id || c.token_id),
    org_id: c.org_id != null ? String(c.org_id) : null,
  }));
}

function optionWireValue(option) {
  if (!option || option.value == null) return null;
  return String(option.value);
}

const EditFlowConnectionsModal = ({ isOpen, onClose, flowId, flow, detailedFlow, onSaved }) => {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [sourceOptions, setSourceOptions] = useState([]);
  const [destOptions, setDestOptions] = useState([]);
  const [sourceValue, setSourceValue] = useState(null);
  const [destValue, setDestValue] = useState(null);
  const initialWireRef = useRef({ source: null, destination: null });

  const schema = detailedFlow?.schema;
  const sourceType = flow ? getSourceTypeKey(flow) : 'file';
  const destType = flow ? getDestinationTypeKey(flow) : 'salesforce';

  const sourceTypeLabel = useMemo(() => {
    if (isSqlType(sourceType)) return 'SQL database';
    if (isSheetsType(sourceType)) return 'Google Sheets';
    if (isSalesforceType(sourceType)) return 'Salesforce';
    return sourceType || '—';
  }, [sourceType]);

  const destTypeLabel = useMemo(() => {
    if (isSqlType(destType)) return 'SQL database';
    if (isSheetsType(destType)) return 'Google Sheets';
    if (isSalesforceType(destType)) return 'Salesforce';
    return destType || '—';
  }, [destType]);

  useEffect(() => {
    if (!isOpen || !flow) {
      return undefined;
    }

    let cancelled = false;
    setLoadError(null);
    setSaveError(null);
    setLoading(true);
    setSourceOptions([]);
    setDestOptions([]);
    setSourceValue(null);
    setDestValue(null);
    initialWireRef.current = { source: null, destination: null };

    const load = async () => {
      try {
        let srcOpts = [];
        if (isSqlType(sourceType)) srcOpts = await fetchSqlConnectorOptions();
        else if (isSheetsType(sourceType)) srcOpts = await fetchGoogleSheetsOptions();
        else if (isSalesforceType(sourceType)) srcOpts = await fetchSalesforceOptions();

        let dstOpts = [];
        if (isSqlType(destType)) dstOpts = await fetchSqlConnectorOptions();
        else if (isSheetsType(destType)) dstOpts = await fetchGoogleSheetsOptions();
        else if (isSalesforceType(destType)) dstOpts = await fetchSalesforceOptions();

        if (cancelled) return;

        setSourceOptions(srcOpts);
        setDestOptions(dstOpts);

        let srcMatch = null;
        const curSrc = schema?.connector_id != null ? String(schema.connector_id) : '';
        if (curSrc) {
          srcMatch = srcOpts.find((o) => o.value === curSrc);
          if (!srcMatch && isSheetsType(sourceType)) {
            srcMatch = srcOpts.find(
              (o) => Array.isArray(o.ugsRowIds) && o.ugsRowIds.includes(curSrc)
            );
          }
        }

        let dstMatch = null;
        const destFlowId =
          flow.destination_id != null
            ? String(flow.destination_id)
            : flow.org_id != null
              ? String(flow.org_id)
              : '';
        if (isSqlType(destType) && destFlowId) {
          dstMatch = dstOpts.find((o) => o.value === destFlowId);
        } else if (isSheetsType(destType) && destFlowId) {
          dstMatch = dstOpts.find((o) => o.value === destFlowId);
          if (!dstMatch) {
            dstMatch = dstOpts.find(
              (o) => Array.isArray(o.ugsRowIds) && o.ugsRowIds.includes(destFlowId)
            );
          }
        } else if (isSalesforceType(destType)) {
          const tok = flow.salesforce_token_id != null ? String(flow.salesforce_token_id) : '';
          const orgId = flow.org_id != null ? String(flow.org_id) : '';
          dstMatch =
            (tok && dstOpts.find((o) => o.value === tok)) ||
            (orgId && dstOpts.find((o) => o.org_id === orgId)) ||
            null;
        }

        initialWireRef.current = {
          source: optionWireValue(srcMatch),
          destination: optionWireValue(dstMatch),
        };
        setSourceValue(srcMatch);
        setDestValue(dstMatch);
      } catch (e) {
        if (!cancelled) {
          setLoadError(e.response?.data?.detail || e.message || 'Failed to load connections');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [isOpen, flow, schema, sourceType, destType]);

  const showSourceSelect = isSqlType(sourceType) || isSheetsType(sourceType) || isSalesforceType(sourceType);
  const showDestSelect = isSqlType(destType) || isSheetsType(destType) || isSalesforceType(destType);

  const isDirty = useMemo(() => {
    const curSrc = optionWireValue(sourceValue);
    const curDst = optionWireValue(destValue);
    const init = initialWireRef.current;
    const srcChanged = showSourceSelect ? curSrc !== init.source : false;
    const dstChanged = showDestSelect ? curDst !== init.destination : false;
    return srcChanged || dstChanged;
  }, [sourceValue, destValue, showSourceSelect, showDestSelect]);

  if (!isOpen) return null;

  const selectStyles = {
    menuPortal: (base) => ({ ...base, zIndex: 10002 }),
    menu: (base) => ({ ...base, zIndex: 10002 }),
  };

  const handleClose = () => {
    if (!saving) onClose();
  };

  const handleSave = async () => {
    setSaveError(null);
    const curSrc = optionWireValue(sourceValue);
    const curDst = optionWireValue(destValue);
    const init = initialWireRef.current;

    if (showSourceSelect && !curSrc) {
      setSaveError('Select a source connection.');
      return;
    }
    if (showDestSelect && !curDst) {
      setSaveError('Select a destination connection.');
      return;
    }

    const payload = {};
    if (curSrc !== init.source) payload.source_id = curSrc;
    if (curDst !== init.destination) payload.destination_id = curDst;

    if (Object.keys(payload).length === 0) {
      onClose();
      return;
    }

    const hadChanges = Object.keys(payload).length > 0;
    setSaving(true);
    try {
      const res = await api.patch(`/flow/${flowId}/connections`, payload);
      if (res.data?.status === 'success') {
        if (onSaved) await onSaved({ connectionChanged: hadChanges });
        onClose();
      } else {
        setSaveError('Failed to save connections');
      }
    } catch (e) {
      const detail = e.response?.data?.detail;
      if (detail && typeof detail === 'object' && Array.isArray(detail.errors)) {
        const blocks = [];
        if (Array.isArray(detail.error_details) && detail.error_details.length > 0) {
          detail.error_details.forEach((b) => {
            if (!b || typeof b !== 'object') return;
            if (b.explanation) blocks.push(`Explanation: ${b.explanation}`);
            if (b.solution) blocks.push(`What to try: ${b.solution}`);
            if (b.explanation || b.solution) blocks.push('');
          });
        }
        const primary = detail.errors.join('\n');
        const extra = blocks.filter(Boolean).join('\n').trim();
        setSaveError(extra ? `${primary}\n\n${extra}` : primary);
      } else if (typeof detail === 'string') {
        setSaveError(detail);
      } else if (Array.isArray(detail)) {
        setSaveError(detail.map((d) => d.msg || d).join(' '));
      } else {
        setSaveError(e.message || 'Failed to save connections');
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flows-page modal-overlay edit-flow-connections-overlay" onClick={handleClose}>
      <div className="modal-content edit-flow-connections-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Edit connections</h3>
          <button type="button" className="modal-close" onClick={handleClose} disabled={saving} aria-label="Close">
            <FiX />
          </button>
        </div>
        <div className="modal-body edit-flow-connections-body">
          <p className="edit-flow-connections-intro">
            Source and destination <strong>types</strong> stay the same. Choose a connection for each side, then save.
            Changing connections may require reviewing field mappings.
          </p>

          {loading && <p className="edit-flow-connections-status">Loading…</p>}
          {loadError && (
            <p className="edit-flow-connections-error" role="alert">
              {loadError}
            </p>
          )}
          {saveError && (
            <div className="edit-flow-connections-error" role="alert">
              {saveError.split('\n').map((line, i) => (
                <p key={i}>{line}</p>
              ))}
            </div>
          )}

          {!loading && !loadError && (
            <>
              <div className="edit-flow-connections-row">
                <div className="edit-flow-connections-row-head">
                  <span className="edit-flow-connections-label">Source</span>
                  <span className="edit-flow-connections-type">{sourceTypeLabel}</span>
                </div>
                {showSourceSelect ? (
                  <Select
                    className="edit-flow-connections-select-wrap"
                    classNamePrefix="edit-flow-conn-select"
                    options={sourceOptions}
                    value={sourceValue}
                    onChange={setSourceValue}
                    placeholder="Select source connection…"
                    isClearable={false}
                    isSearchable
                    isDisabled={saving}
                    menuPortalTarget={document.body}
                    menuPosition="fixed"
                    styles={selectStyles}
                  />
                ) : (
                  <p className="edit-flow-connections-na">—</p>
                )}
              </div>

              <div className="edit-flow-connections-row">
                <div className="edit-flow-connections-row-head">
                  <span className="edit-flow-connections-label">Destination</span>
                  <span className="edit-flow-connections-type">{destTypeLabel}</span>
                </div>
                {showDestSelect ? (
                  <Select
                    className="edit-flow-connections-select-wrap"
                    classNamePrefix="edit-flow-conn-select"
                    options={destOptions}
                    value={destValue}
                    onChange={setDestValue}
                    placeholder="Select destination connection…"
                    isClearable={false}
                    isSearchable
                    isDisabled={saving}
                    menuPortalTarget={document.body}
                    menuPosition="fixed"
                    styles={selectStyles}
                  />
                ) : (
                  <p className="edit-flow-connections-na">—</p>
                )}
              </div>
            </>
          )}

          <div className="modal-actions edit-flow-connections-actions">
            <button type="button" className="btn btn-secondary" onClick={handleClose} disabled={saving || loading}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSave}
              disabled={saving || loading || !isDirty}
            >
              {saving ? 'Validating connection…' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default EditFlowConnectionsModal;
