import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import Select from 'react-select';
import CreatableSelect from 'react-select/creatable';
import { FiX, FiPlus, FiTrash2 } from 'react-icons/fi';
import api from '../api';
import {
  collectSourceFieldOptions,
  lookupFlattenTooltipBySourceField,
  mergedDestinationSchemaFromCatalog,
  rowsFromFieldMappings,
  rowsToPatchPayload,
  primarySecondaryLabels,
  getPrimaryOptions,
  getSecondaryOptions,
  coalesceSourceSqlQuery,
} from '../utils/flowMappingEditorUtils';
import { validateMappingSelectQuery } from '../utils/querySyntaxValidation';
import {
  formatDestinationTypeForDisplay,
  formatSourceTypeForDisplay,
  getFlowDestinationDisplayName,
  getFlowSourceDisplayName,
  getSourceTypeKey,
} from '../utils/flowDisplayUtils';
import '../FlowsPage.css';

/** FastAPI may return detail as string or { message, technical, solution } from query smoke tests */
function formatMappingSaveError(detail) {
  if (detail == null || detail === '') {
    return 'Failed to save mappings';
  }
  if (typeof detail === 'string') {
    return detail;
  }
  if (typeof detail === 'object' && !Array.isArray(detail)) {
    const main = detail.message || detail.msg || 'Validation failed';
    const parts = [main];
    if (detail.solution) {
      parts.push(typeof detail.solution === 'string' ? detail.solution : String(detail.solution));
    }
    if (detail.technical && detail.message && detail.technical !== detail.message) {
      parts.push(`Technical: ${detail.technical}`);
    }
    return parts.filter(Boolean).join('\n\n');
  }
  return String(detail);
}

const emptyRow = () => ({
  extracted_field_name: '',
  salesforce_object: '',
  salesforce_field: '',
  lookup_of: '',
  is_duplicate_check: false,
  is_required: false,
});

/** Stable shape for dirty-check JSON compare */
function normalizeMappingRow(r) {
  return {
    extracted_field_name: (r.extracted_field_name || '').trim(),
    salesforce_object: (r.salesforce_object || '').trim(),
    salesforce_field: (r.salesforce_field || '').trim(),
    lookup_of: (r.lookup_of || '').trim(),
    is_duplicate_check: !!r.is_duplicate_check,
    is_required: !!r.is_required,
  };
}

const FlowMappingEditorModal = ({ isOpen, onClose, flowId, detailedFlow, onSaved }) => {
  const [rows, setRows] = useState([emptyRow()]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [catalog, setCatalog] = useState(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState(null);
  const [sqlQueryDraft, setSqlQueryDraft] = useState('');
  /** Snapshot when modal opened (after catalog load) for dirty detection */
  const [savedBaseline, setSavedBaseline] = useState(null);
  const baselineCapturedRef = useRef(false);

  const flow = detailedFlow?.flow;
  const schema = detailedFlow?.schema;
  const destination = (flow?.destination_type || 'salesforce').toLowerCase();
  const sourceTypeKey = getSourceTypeKey(flow);
  const labels = primarySecondaryLabels(destination);

  /** Full destination shape from GET /flow/:id/mapping-catalog merged with current rows. */
  const destinationSchema = useMemo(
    () => mergedDestinationSchemaFromCatalog(catalog, rows, destination),
    [catalog, rows, destination]
  );

  /** Full pool of candidate source names (schema + catalog + any row value). */
  const allSourceOptions = useMemo(() => {
    const fromSchema = collectSourceFieldOptions(schema?.extracted_schema || {});
    const fromCatalog = catalog?.source_fields || [];
    const fromRows = rows.map((r) => r.extracted_field_name).filter(Boolean);
    return Array.from(new Set([...fromSchema, ...fromCatalog, ...fromRows])).sort((a, b) =>
      a.localeCompare(b)
    );
  }, [schema, rows, catalog]);

  const syncRowsFromFlow = useCallback(() => {
    if (!detailedFlow) return;
    setRows(rowsFromFieldMappings(detailedFlow.field_mappings));
    setSaveError(null);
  }, [detailedFlow]);

  useEffect(() => {
    if (isOpen) {
      syncRowsFromFlow();
    }
  }, [isOpen, syncRowsFromFlow]);

  useEffect(() => {
    if (!isOpen || !flowId) return undefined;
    let cancelled = false;
    setCatalogLoading(true);
    setCatalogError(null);
    setCatalog(null);
    api
      .get(`/flow/${flowId}/mapping-catalog`)
      .then((res) => {
        if (!cancelled && res.data?.status === 'success') {
          setCatalog(res.data);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          const msg = e.response?.data?.detail;
          setCatalogError(typeof msg === 'string' ? msg : e.message || 'Failed to load field catalog');
          setCatalog(null);
        }
      })
      .finally(() => {
        if (!cancelled) setCatalogLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, flowId]);

  useEffect(() => {
    if (!isOpen) return;
    setSqlQueryDraft(coalesceSourceSqlQuery(schema, catalog));
  }, [isOpen, schema, catalog]);

  useEffect(() => {
    if (!isOpen) {
      baselineCapturedRef.current = false;
      setSavedBaseline(null);
      return;
    }
    if (catalogLoading || !detailedFlow) return;
    if (baselineCapturedRef.current) return;
    baselineCapturedRef.current = true;
    const initialRows = rowsFromFieldMappings(detailedFlow.field_mappings);
    const initialSql = coalesceSourceSqlQuery(schema, catalog) || '';
    setSavedBaseline({
      rowsJson: JSON.stringify(initialRows.map(normalizeMappingRow)),
      sql: initialSql,
    });
  }, [isOpen, catalogLoading, detailedFlow, schema, catalog]);

  const usedSourceFields = useMemo(
    () => new Set(rows.map((r) => (r.extracted_field_name || '').trim()).filter(Boolean)),
    [rows]
  );

  /** Tooltips for source dropdown: flattened names for lookup-backed mappings */
  const lookupSourceTooltips = useMemo(
    () => lookupFlattenTooltipBySourceField(rows),
    [rows]
  );

  const canAddMapping = useMemo(() => {
    if (!allSourceOptions.length) return false;
    return allSourceOptions.some((name) => !usedSourceFields.has(name));
  }, [allSourceOptions, usedSourceFields]);

  /** SQL source relationship hints: referenced_table -> "source_table.fk_column" */
  const sqlLookupOfByReferencedTable = useMemo(() => {
    if (sourceTypeKey !== 'sql') return {};
    const srcTable = (catalog?.source_object_or_table || schema?.table_name || '').trim();
    const sourceForeignKeys = Array.isArray(catalog?.source_foreign_keys) ? catalog.source_foreign_keys : [];
    const out = {};
    sourceForeignKeys.forEach((fk) => {
      const refTable = (fk?.referenced_table || '').trim().toLowerCase();
      const fkCol = (fk?.column_name || '').trim();
      if (!refTable || !fkCol || out[refTable]) return;
      if (srcTable) out[refTable] = `${srcTable}.${fkCol}`;
    });
    return out;
  }, [catalog, schema, sourceTypeKey]);

  /** Salesforce source: lookup_object_api_name (lowercase) -> "BaseObject.LookupFieldApiName" */
  const sfLookupOfByReferencedObject = useMemo(() => {
    if (sourceTypeKey !== 'salesforce') return {};
    const baseObj = (catalog?.source_object_or_table || schema?.table_name || '').trim();
    const list = Array.isArray(catalog?.source_salesforce_lookups) ? catalog.source_salesforce_lookups : [];
    if (!baseObj || !list.length) return {};
    const byRef = new Map();
    list.forEach((row) => {
      const refObj = (row?.lookup_object_api_name || '').trim();
      const fld = (row?.field_api_name || '').trim();
      if (!refObj || !fld) return;
      const k = refObj.toLowerCase();
      if (!byRef.has(k)) byRef.set(k, []);
      byRef.get(k).push(fld);
    });
    const pickField = (names) => {
      const uniq = Array.from(new Set(names.filter(Boolean)));
      if (uniq.length === 0) return '';
      if (uniq.length === 1) return uniq[0];
      const endingId = uniq.filter((f) => f.endsWith('Id'));
      if (endingId.length === 1) return endingId[0];
      if (endingId.length > 1) {
        return endingId.sort((a, b) => a.length - b.length || a.localeCompare(b))[0];
      }
      return [...uniq].sort((a, b) => a.localeCompare(b))[0];
    };
    const out = {};
    byRef.forEach((fields, refKey) => {
      const pick = pickField(fields);
      if (pick) out[refKey] = `${baseObj}.${pick}`;
    });
    return out;
  }, [catalog, schema, sourceTypeKey]);

  const lookupOfPlaceholder = useMemo(() => {
    if (sourceTypeKey === 'sql') return 'orders.customer_id';
    if (sourceTypeKey === 'salesforce') {
      const base = (catalog?.source_object_or_table || schema?.table_name || 'Contact').trim() || 'Contact';
      const list = Array.isArray(catalog?.source_salesforce_lookups) ? catalog.source_salesforce_lookups : [];
      if (list.length) {
        const fld = (list[0].field_api_name || 'AccountId').trim() || 'AccountId';
        return `${base}.${fld}`;
      }
      return 'Contact.AccountId';
    }
    return 'Contact.AccountId';
  }, [sourceTypeKey, catalog, schema]);

  const isDirty = useMemo(() => {
    if (!savedBaseline) return false;
    const rowsJson = JSON.stringify(rows.map(normalizeMappingRow));
    const sql = sqlQueryDraft || '';
    return rowsJson !== savedBaseline.rowsJson || sql !== savedBaseline.sql;
  }, [savedBaseline, rows, sqlQueryDraft]);

  const hasSaveableMappings = useMemo(() => {
    const { mappings } = rowsToPatchPayload(rows);
    if (!mappings.length) return false;
    return mappings.every(
      (m) =>
        (m.extracted_field_name || '').trim() &&
        (m.salesforce_object || '').trim() &&
        (m.salesforce_field || '').trim()
    );
  }, [rows]);

  const canSaveMappings = !!savedBaseline && isDirty && hasSaveableMappings && !saving;

  const handleRowChange = (index, field, value) => {
    setRows((prev) => {
      const next = [...prev];
      const row = { ...next[index], [field]: value };
      if (field === 'salesforce_object') {
        row.salesforce_field = '';
        // Auto-suggest lookup_of from FK (SQL) or lookup field metadata (Salesforce); editable, only when empty.
        if (!(row.lookup_of || '').trim()) {
          const destKey = (value || '').trim().toLowerCase();
          if (sourceTypeKey === 'sql') {
            const hint = sqlLookupOfByReferencedTable[destKey];
            if (hint) row.lookup_of = hint;
          } else if (sourceTypeKey === 'salesforce') {
            const hint = sfLookupOfByReferencedObject[destKey];
            if (hint) row.lookup_of = hint;
          }
        }
      }
      next[index] = row;
      return next;
    });
    setSaveError(null);
  };

  const addRow = () => {
    setRows((prev) => [...prev, emptyRow()]);
  };

  const removeRow = (index) => {
    setRows((prev) => (prev.length <= 1 ? [emptyRow()] : prev.filter((_, i) => i !== index)));
  };

  const handleSave = async () => {
    setSaveError(null);
    const { mappings } = rowsToPatchPayload(rows);
    if (!mappings.length) {
      setSaveError('Add at least one complete mapping (source field, destination target, destination field).');
      return;
    }
    const seenSources = new Set();
    for (let i = 0; i < mappings.length; i++) {
      const m = mappings[i];
      if (!m.extracted_field_name || !m.salesforce_object || !m.salesforce_field) {
        setSaveError(`Row ${i + 1}: fill source field, ${labels.primary.toLowerCase()}, and ${labels.secondary.toLowerCase()}.`);
        return;
      }
      const srcKey = m.extracted_field_name.trim();
      if (seenSources.has(srcKey)) {
        setSaveError(`Duplicate source field "${srcKey}". Each source field can only be mapped once.`);
        return;
      }
      seenSources.add(srcKey);
    }

    const sourceKey = getSourceTypeKey(detailedFlow.flow);
    if (sourceKey === 'sql' || sourceKey === 'salesforce') {
      const qErr = validateMappingSelectQuery(sqlQueryDraft, {
        strictSoqlFrom: sourceKey === 'salesforce',
      });
      if (qErr) {
        setSaveError(qErr);
        return;
      }
    }

    setSaving(true);
    try {
      const payload = { mappings };
      if (sourceKey === 'sql' || sourceKey === 'salesforce') {
        payload.sql_query = sqlQueryDraft;
      }
      const res = await api.patch(`/flow/${flowId}/field-mappings`, payload);
      if (res.data?.status === 'success') {
        const syncedSql = res.data.sql_query;
        onSaved(res.data.field_mappings, syncedSql !== undefined ? syncedSql : null);
        if (syncedSql != null && typeof syncedSql === 'string') {
          setSqlQueryDraft(syncedSql);
        }
        onClose();
      } else {
        setSaveError('Unexpected response from server');
      }
    } catch (e) {
      const raw = e.response?.data?.detail;
      setSaveError(formatMappingSaveError(raw ?? e.message));
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;
  if (!detailedFlow?.flow || !detailedFlow?.schema) return null;

  const primaryOpts = getPrimaryOptions(destination, destinationSchema);
  const selectStyles = {
    menuPortal: (base) => ({ ...base, zIndex: 10002 }),
    menu: (base) => ({ ...base, zIndex: 10002 }),
  };

  const sourceTypeDisplay = formatSourceTypeForDisplay(flow);
  const destTypeDisplay = formatDestinationTypeForDisplay(flow);
  const sourceNameDisplay = getFlowSourceDisplayName(flow, schema);
  const destinationNameDisplay = getFlowDestinationDisplayName(flow);
  const showLookupColumn = destination === 'salesforce' || sourceTypeKey === 'salesforce' || sourceTypeKey === 'sql';
  const sourceFks = Array.isArray(catalog?.source_foreign_keys) ? catalog.source_foreign_keys : [];
  const sampleSourceFk = sourceFks.length ? sourceFks[0] : null;
  const sourceSfLkups = Array.isArray(catalog?.source_salesforce_lookups) ? catalog.source_salesforce_lookups : [];
  const sampleSourceSfLookup = sourceSfLkups.length ? sourceSfLkups[0] : null;
  const sfBaseForHint =
    (schema?.table_name || catalog?.source_object_or_table || 'Contact').trim() || 'Contact';

  return (
    <div className="flows-page modal-overlay flow-mapping-editor-overlay" onClick={onClose}>
      <div className="modal-content flow-mapping-editor-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Edit field mappings</h3>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            <FiX />
          </button>
        </div>
        <div className="modal-body flow-mapping-editor-body">
          <p className="flow-mapping-editor-intro">
            Source and destination pick lists are loaded from your stored schema plus live catalog metadata (connectors
            and synced fields). Each source field can only be mapped once; the source dropdown lists fields not yet used on
            another row. For SQL and Salesforce sources, saving first checks query shape, syncs the SELECT list to your
            mappings, then runs the same LIMIT 1 execution check against your SQL database or Salesforce source org as
            when you validate a connection—only then is the query stored. Saved <strong>field_mappings</strong> stay
            editable below.
          </p>
          {catalogLoading && (
            <p className="flow-mapping-catalog-status flow-mapping-catalog-loading">Loading field lists from connectors…</p>
          )}
          {catalogError && (
            <p className="flow-mapping-catalog-status flow-mapping-catalog-error" role="alert">
              {catalogError} You can still edit using stored schema and current mappings.
            </p>
          )}
          <dl className="flow-mapping-editor-flow-meta">
            <div className="flow-mapping-editor-flow-meta-row">
              <dt>Source type</dt>
              <dd>{sourceTypeDisplay}</dd>
            </div>
            <div className="flow-mapping-editor-flow-meta-row">
              <dt>Source name</dt>
              <dd>{sourceNameDisplay || '—'}</dd>
            </div>
            <div className="flow-mapping-editor-flow-meta-row">
              <dt>Destination type</dt>
              <dd>{destTypeDisplay}</dd>
            </div>
            <div className="flow-mapping-editor-flow-meta-row">
              <dt>Destination name</dt>
              <dd>{destinationNameDisplay || '—'}</dd>
            </div>
          </dl>

          {(sourceTypeKey === 'sql' || sourceTypeKey === 'salesforce') && (
            <div className="flow-mapping-editor-sql-query-wrap">
              <label className="flow-mapping-editor-sql-query-label" htmlFor="flow-mapping-sql-query">
                {sourceTypeKey === 'sql' ? 'Source SQL query' : 'Source SOQL query'}
              </label>
              <textarea
                id="flow-mapping-sql-query"
                className="flow-mapping-editor-sql-query-textarea"
                value={sqlQueryDraft}
                onChange={(e) => setSqlQueryDraft(e.target.value)}
                rows={6}
                spellCheck={false}
                placeholder={sourceTypeKey === 'sql' ? 'SELECT …' : 'SELECT … FROM …'}
              />
            </div>
          )}

          {showLookupColumn && (
            <p className="flow-mapping-editor-intro" style={{ marginTop: 8 }}>
              Relationship hint: use <code className="migration-inline-code">lookup_of</code> to preserve related-record semantics.
              {' '}
              {sourceTypeKey === 'salesforce'
                ? sampleSourceSfLookup &&
                  sampleSourceSfLookup.field_api_name &&
                  sampleSourceSfLookup.lookup_object_api_name
                  ? `Format: BaseObject.LookupFieldApiName (for example ${sfBaseForHint}.${sampleSourceSfLookup.field_api_name} points at ${sampleSourceSfLookup.lookup_object_api_name}).`
                  : 'Format: BaseObject.LookupFieldApiName (for example Contact.AccountId).'
                : sourceTypeKey === 'sql'
                  ? sampleSourceFk
                    ? `Format: source_table.fk_column (for example ${catalog?.source_object_or_table || 'orders'}.${sampleSourceFk.column_name}).`
                    : 'Format: source_table.fk_column (for example orders.customer_id).'
                  : 'Format: base.lookup_field.'}
            </p>
          )}

          {sourceTypeKey === 'salesforce' && (schema?.table_name || catalog?.source_object_or_table) && (
            <p className="flow-mapping-editor-sf-source-object">
              <strong>Source Salesforce object:</strong>{' '}
              <code className="migration-inline-code">{schema?.table_name || catalog?.source_object_or_table}</code>
            </p>
          )}

          <div className="flow-mapping-editor-table-wrap">
            <table className="flow-mapping-editor-table">
              <colgroup>
                <col className="flow-mapping-col-source" />
                <col className="flow-mapping-col-primary" />
                <col className="flow-mapping-col-secondary" />
                {showLookupColumn && <col className="flow-mapping-col-lookup" />}
                <col className="flow-mapping-col-flag" />
                <col className="flow-mapping-col-flag" />
                <col className="flow-mapping-col-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th className="flow-mapping-th-source">Source field</th>
                  <th>{labels.primary}</th>
                  <th>{labels.secondary}</th>
                  {showLookupColumn && <th>Lookup / relationship</th>}
                  <th className="flow-mapping-th-center">Dup check</th>
                  <th className="flow-mapping-th-center">Required</th>
                  <th className="flow-mapping-th-actions" aria-label="Remove" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => {
                  let secondaryOpts = getSecondaryOptions(destination, destinationSchema, row.salesforce_object);
                  if (row.salesforce_field && !secondaryOpts.find((o) => o.value === row.salesforce_field)) {
                    secondaryOpts = [
                      ...secondaryOpts,
                      { label: row.salesforce_field, value: row.salesforce_field },
                    ];
                  }
                  const currentSrc = (row.extracted_field_name || '').trim();
                  const usedOnOtherRows = new Set(
                    rows
                      .map((r, i) => (i !== idx ? (r.extracted_field_name || '').trim() : ''))
                      .filter(Boolean)
                  );
                  let sourceNamesForRow = allSourceOptions.filter(
                    (name) => !usedOnOtherRows.has(name) || name === currentSrc
                  );
                  if (currentSrc && !sourceNamesForRow.includes(currentSrc)) {
                    sourceNamesForRow = [...sourceNamesForRow, currentSrc];
                  }
                  sourceNamesForRow.sort((a, b) => a.localeCompare(b));
                  const srcOpts = sourceNamesForRow.map((v) => ({ label: v, value: v }));
                  return (
                    <tr key={idx}>
                      <td>
                        <Select
                          className="flow-mapping-editor-select-wrap"
                          value={srcOpts.find((o) => o.value === row.extracted_field_name) || null}
                          onChange={(opt) => handleRowChange(idx, 'extracted_field_name', opt?.value || '')}
                          options={srcOpts}
                          placeholder="Source field…"
                          isClearable
                          menuPortalTarget={document.body}
                          menuPosition="fixed"
                          styles={selectStyles}
                          classNamePrefix="inline-mapping-select"
                          formatOptionLabel={
                            sourceTypeKey === 'salesforce'
                              ? (option) => {
                                  const hint = lookupSourceTooltips[option.value];
                                  return (
                                    <span
                                      title={hint || undefined}
                                      className={
                                        hint ? 'flow-mapping-source-option flow-mapping-source-option--lookup' : undefined
                                      }
                                    >
                                      {option.label}
                                    </span>
                                  );
                                }
                              : undefined
                          }
                        />
                      </td>
                      <td>
                        <CreatableSelect
                          className="flow-mapping-editor-select-wrap"
                          value={
                            row.salesforce_object
                              ? { label: row.salesforce_object, value: row.salesforce_object }
                              : null
                          }
                          onChange={(opt) => handleRowChange(idx, 'salesforce_object', opt?.value || '')}
                          options={primaryOpts}
                          placeholder={labels.primary}
                          isClearable
                          menuPortalTarget={document.body}
                          menuPosition="fixed"
                          styles={selectStyles}
                          classNamePrefix="inline-mapping-select"
                          formatCreateLabel={(input) => `Use "${input}"`}
                        />
                      </td>
                      <td>
                        <CreatableSelect
                          className="flow-mapping-editor-select-wrap"
                          value={
                            row.salesforce_field
                              ? { label: row.salesforce_field, value: row.salesforce_field }
                              : null
                          }
                          onChange={(opt) => handleRowChange(idx, 'salesforce_field', opt?.value || '')}
                          options={secondaryOpts}
                          placeholder={labels.secondary}
                          isClearable
                          isDisabled={!row.salesforce_object}
                          menuPortalTarget={document.body}
                          menuPosition="fixed"
                          styles={selectStyles}
                          classNamePrefix="inline-mapping-select"
                          formatCreateLabel={(input) => `Use "${input}"`}
                        />
                      </td>
                      {showLookupColumn && (
                        <td>
                          <input
                            type="text"
                            className="flow-mapping-lookup-input"
                            value={row.lookup_of || ''}
                            onChange={(e) => handleRowChange(idx, 'lookup_of', e.target.value)}
                            placeholder={lookupOfPlaceholder}
                          />
                        </td>
                      )}
                      <td className="flow-mapping-td-flag">
                        <input
                          type="checkbox"
                          checked={!!row.is_duplicate_check}
                          onChange={(e) => handleRowChange(idx, 'is_duplicate_check', e.target.checked)}
                        />
                      </td>
                      <td className="flow-mapping-td-flag">
                        <input
                          type="checkbox"
                          checked={!!row.is_required}
                          onChange={(e) => handleRowChange(idx, 'is_required', e.target.checked)}
                        />
                      </td>
                      <td className="flow-mapping-td-actions">
                        <button
                          type="button"
                          className="btn btn-ghost-icon flow-mapping-remove-btn"
                          onClick={() => removeRow(idx)}
                          title="Remove row"
                        >
                          <FiTrash2 />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <button
            type="button"
            className="btn btn-secondary flow-mapping-add-row"
            onClick={addRow}
            disabled={!canAddMapping}
            title={
              canAddMapping
                ? 'Add another row'
                : 'All available source fields are already mapped'
            }
          >
            <FiPlus /> Add mapping
          </button>

          {saveError && <p className="flow-mapping-save-error">{saveError}</p>}

          <div className="modal-actions flow-mapping-editor-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSave}
              disabled={!canSaveMappings}
              title={
                !savedBaseline
                  ? 'Loading…'
                  : !isDirty
                    ? 'No changes to save'
                    : !hasSaveableMappings
                      ? 'Complete at least one mapping (source, target, field)'
                      : ''
              }
            >
              {saving
                ? sourceTypeKey === 'sql' || sourceTypeKey === 'salesforce'
                  ? 'Validating query…'
                  : 'Saving…'
                : 'Save mappings'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={onClose} disabled={saving}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default FlowMappingEditorModal;
