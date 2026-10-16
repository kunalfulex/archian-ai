import React, { useEffect, useState, useRef, useCallback } from 'react';
import { FiX, FiAlertCircle } from 'react-icons/fi';
import api from '../api';
import {
  formatTransformationActionLabel,
  formatOperationTypeLabel,
  formatSourceFieldDisplay,
  formatOutputFieldDisplay,
  formatTransformationDetailLine,
  sortTransformationFieldsByOrder,
  formatFilterConditionLine,
  formatFilterLogicDescription,
  formatDeduplicationRulesBlock,
  formatDataCleanupRulesLines,
} from '../utils/migrationRulesDisplay';
import { snapshotHasEditorContent } from '../utils/migrationRulesValidationUtils';
import '../FlowsPage.css';

/**
 * @param {'after_connection' | 'after_mapping' | 'edit'} mode
 */
const MigrationRulesValidationModal = ({ isOpen, flowId, mode = 'after_connection', onClose, onComplete }) => {
  const [phase, setPhase] = useState('loading');
  const [issues, setIssues] = useState([]);
  const [snapshot, setSnapshot] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);
  const [removingKey, setRemovingKey] = useState(null);
  const [bulkWorking, setBulkWorking] = useState(false);
  const [prefSaving, setPrefSaving] = useState(false);
  const [prefOption, setPrefOption] = useState('skip');
  const [prefPlaceholder, setPrefPlaceholder] = useState('Unknown');
  const [validateSkipped, setValidateSkipped] = useState(null);
  const handledRef = useRef(false);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;
  const modeRef = useRef(mode);
  modeRef.current = mode;

  const finishAutoOk = useCallback(() => {
    if (handledRef.current) return;
    handledRef.current = true;
    onCompleteRef.current?.({ status: 'auto_ok' });
  }, []);

  const applyValidatePayload = useCallback(
    (data) => {
      if (data.status !== 'success') {
        setPhase('error');
        setErrorMessage('Unexpected response from server.');
        return;
      }
      const snap = data.snapshot || {};
      setSnapshot(snap);
      setIssues(Array.isArray(data.issues) ? data.issues : []);
      setValidateSkipped(data.skipped || null);

      const m = modeRef.current;
      if (data.skipped === 'file_source' && (m === 'after_connection' || m === 'after_mapping')) {
        setPhase('ok');
        window.setTimeout(() => finishAutoOk(), 200);
        return;
      }

      const valid = data.valid !== false && (data.issues || []).length === 0;
      const hasContent = snapshotHasEditorContent(snap);
      const isPostShapeChange = m === 'after_connection' || m === 'after_mapping';
      if (isPostShapeChange && valid && !hasContent) {
        setPhase('ok');
        window.setTimeout(() => finishAutoOk(), 350);
        return;
      }

      setPhase('editor');
    },
    [finishAutoOk],
  );

  const refetchValidation = useCallback(async () => {
    const res = await api.get(`/flow/${flowId}/migration-rules/validate`);
    applyValidatePayload(res.data);
  }, [flowId, applyValidatePayload]);

  useEffect(() => {
    if (!snapshot) return;
    const p = snapshot.missing_field_preference;
    if (p && typeof p === 'object') {
      setPrefOption(p.option === 'placeholder' ? 'placeholder' : 'skip');
      setPrefPlaceholder(p.placeholderValue != null ? String(p.placeholderValue) : 'Unknown');
    } else {
      setPrefOption('skip');
      setPrefPlaceholder('Unknown');
    }
  }, [snapshot]);

  useEffect(() => {
    if (!isOpen || !flowId) {
      return undefined;
    }
    handledRef.current = false;
    setPhase('loading');
    setIssues([]);
    setSnapshot(null);
    setValidateSkipped(null);
    setErrorMessage(null);
    let cancelled = false;
    let timer;

    (async () => {
      try {
        const res = await api.get(`/flow/${flowId}/migration-rules/validate`);
        if (cancelled) return;
        const data = res.data;
        if (data.status !== 'success') {
          setPhase('error');
          setErrorMessage('Unexpected response from server.');
          return;
        }
        if (
          (modeRef.current === 'after_connection' || modeRef.current === 'after_mapping') &&
          data.skipped === 'file_source'
        ) {
          setPhase('ok');
          timer = window.setTimeout(() => {
            if (cancelled) return;
            finishAutoOk();
          }, 200);
          return;
        }
        applyValidatePayload(data);
      } catch (e) {
        if (cancelled) return;
        const d = e.response?.data?.detail;
        setErrorMessage(typeof d === 'string' ? d : e.message || 'Validation request failed');
        setPhase('error');
      }
    })();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [isOpen, flowId, finishAutoOk, applyValidatePayload]);

  const brokenExtractionRuleIds = new Set(
    issues.filter((i) => i.issue_kind === 'extraction_transformation' && i.rule_id).map((i) => String(i.rule_id)),
  );
  const brokenMappingRuleIds = new Set(
    issues.filter((i) => i.issue_kind === 'mapping_transformation' && i.rule_id).map((i) => String(i.rule_id)),
  );
  const brokenFilterIndices = new Set(
    issues.filter((i) => i.issue_kind === 'filter' && i.condition_index != null).map((i) => Number(i.condition_index)),
  );
  const brokenDedupFields = new Set(
    issues.filter((i) => i.issue_kind === 'deduplication' && i.field).map((i) => String(i.field).trim()),
  );
  const brokenDataCleanupFields = new Set(
    issues.filter((i) => i.issue_kind === 'data_cleanup' && i.field).map((i) => String(i.field).trim()),
  );

  const handleEditorClose = () => {
    if (removingKey || bulkWorking || prefSaving) return;
    if (phase !== 'editor') return;
    if (!handledRef.current) {
      handledRef.current = true;
      const m = modeRef.current;
      let st = 'edit_closed';
      if (m === 'after_connection' || m === 'after_mapping') {
        st = issues.length > 0 ? 'closed_with_issues' : 'closed_after_review';
      }
      onCompleteRef.current?.({ status: st });
    }
  };

  const removeOne = async (key, payload) => {
    setRemovingKey(key);
    try {
      await api.patch(`/flow/${flowId}/migration-rules`, payload);
      await refetchValidation();
    } catch (e) {
      const d = e.response?.data?.detail;
      setErrorMessage(typeof d === 'string' ? d : e.message || 'Update failed');
      setPhase('error');
    } finally {
      setRemovingKey(null);
    }
  };

  const handleSavePreference = async () => {
    if (!flowId || prefSaving) return;
    if (prefOption === 'placeholder' && !String(prefPlaceholder || '').trim()) {
      setErrorMessage('Enter a placeholder value when using “fill with placeholder”.');
      setPhase('error');
      return;
    }
    setPrefSaving(true);
    try {
      await api.patch(`/flow/${flowId}/migration-rules`, {
        missing_field_preference: {
          option: prefOption,
          placeholderValue: prefOption === 'placeholder' ? String(prefPlaceholder).trim() : prefPlaceholder || 'Unknown',
        },
      });
      await refetchValidation();
    } catch (e) {
      const d = e.response?.data?.detail;
      setErrorMessage(typeof d === 'string' ? d : e.message || 'Failed to save preference');
      setPhase('error');
    } finally {
      setPrefSaving(false);
    }
  };

  const handleRemoveExtractionRow = (ruleId) => {
    if (!ruleId) return;
    removeOne(`ex-${ruleId}`, { remove_extraction_transformation_rule_ids: [ruleId] });
  };

  const handleRemoveMappingRow = (ruleId) => {
    if (!ruleId) return;
    removeOne(`map-${ruleId}`, { remove_mapping_transformation_rule_ids: [ruleId] });
  };

  const handleRemoveFilterCondition = (conditionIndex) => {
    if (conditionIndex == null) return;
    removeOne(`fl-${conditionIndex}`, { remove_filter_condition_indices: [conditionIndex] });
  };

  const handleRemoveDedupField = (fieldName) => {
    if (!fieldName) return;
    removeOne(`dd-${fieldName}`, { remove_deduplication_fields: [fieldName] });
  };

  const handleRemoveDataCleanupField = (fieldName) => {
    if (!fieldName) return;
    removeOne(`dc-${fieldName}`, { remove_data_cleanup_fields: [fieldName] });
  };

  const handleClearAllIncludingPreference = async () => {
    if (!flowId || bulkWorking) return;
    setBulkWorking(true);
    try {
      await api.patch(`/flow/${flowId}/migration-rules`, { clear: true });
      if (!handledRef.current) {
        handledRef.current = true;
        onCompleteRef.current?.({ status: 'cleared' });
      }
    } catch (e) {
      const d = e.response?.data?.detail;
      setErrorMessage(typeof d === 'string' ? d : e.message || 'Failed to clear rules');
      setPhase('error');
    } finally {
      setBulkWorking(false);
    }
  };

  const extractionRows = sortTransformationFieldsByOrder(snapshot?.extraction_transformation_fields || []);
  const mappingRows = sortTransformationFieldsByOrder(snapshot?.mapping_transformation_fields || []);
  const filterConds = Array.isArray(snapshot?.filter_rules?.conditions) ? snapshot.filter_rules.conditions : [];
  const filterLogic = snapshot?.filter_rules?.logic || 'AND';
  const dedupFields = Array.isArray(snapshot?.deduplication_rules?.fields)
    ? snapshot.deduplication_rules.fields
    : [];
  const dedupBlock = formatDeduplicationRulesBlock(snapshot?.deduplication_rules);
  const dataCleanupSnapshot = snapshot?.data_cleanup_rules;
  const dataCleanupEntries =
    dataCleanupSnapshot && typeof dataCleanupSnapshot === 'object' && !Array.isArray(dataCleanupSnapshot)
      ? Object.entries(dataCleanupSnapshot)
      : [];

  const renderFieldRulesTable = (rows, layer, brokenIds) => {
    if (!rows.length) return null;
    return (
      <div className="migration-rules-table-wrap migration-rules-validate-table-wrap">
        <table className="migration-rules-table migration-rules-validate-table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Source field</th>
              <th scope="col">Output</th>
              <th scope="col">Action</th>
              <th scope="col">Stage</th>
              <th scope="col" className="migration-rules-validate-remove-col">
                Remove
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => {
              const rid = row.rule_id != null ? String(row.rule_id) : '';
              const detail = formatTransformationDetailLine(row);
              const ruleNumber =
                row.order != null && row.order !== '' ? Number(row.order) + 1 : idx + 1;
              const busy =
                layer === 'extraction'
                  ? removingKey === `ex-${rid}`
                  : removingKey === `map-${rid}`;
              const isBroken = rid && brokenIds.has(rid);
              return (
                <tr
                  key={rid || `row-${idx}`}
                  className={isBroken ? 'migration-rules-validate-row-broken' : undefined}
                >
                  <td>{ruleNumber}</td>
                  <td>{formatSourceFieldDisplay(row)}</td>
                  <td>
                    <div>{formatOutputFieldDisplay(row)}</div>
                    {detail ? <div className="migration-rule-cell-detail">{detail}</div> : null}
                  </td>
                  <td>{formatTransformationActionLabel(row.action)}</td>
                  <td>{formatOperationTypeLabel(row.operation_type)}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-sm migration-rules-validate-btn-inline migration-rules-validate-remove-btn"
                      disabled={busy || bulkWorking}
                      onClick={() =>
                        layer === 'extraction'
                          ? handleRemoveExtractionRow(rid)
                          : handleRemoveMappingRow(rid)
                      }
                    >
                      {busy ? '…' : 'Remove'}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  if (!isOpen) return null;

  const showEditor = phase === 'editor';
  const fileSkippedInEditor = showEditor && validateSkipped === 'file_source';

  return (
    <div className="flows-page modal-overlay migration-rules-validate-overlay" role="presentation">
      <div
        className="modal-content migration-rules-validate-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="migration-rules-validate-title"
        aria-busy={phase === 'loading' || !!removingKey || bulkWorking || prefSaving}
      >
        <div className="modal-header">
          <h3 id="migration-rules-validate-title">
            {mode === 'edit'
              ? 'Edit migration rules'
              : mode === 'after_mapping'
                ? 'Migration rules after mapping update'
                : 'Migration rules'}
          </h3>
          <button
            type="button"
            className="modal-close"
            onClick={() => {
              if (phase === 'loading' || removingKey || bulkWorking || prefSaving) return;
              if (phase === 'editor') handleEditorClose();
              else onClose();
            }}
            disabled={phase === 'loading' || !!removingKey || bulkWorking || prefSaving}
            aria-label="Close"
          >
            <FiX />
          </button>
        </div>

        <div className="modal-body migration-rules-validate-body">
          {phase === 'loading' && (
            <p className="migration-rules-validate-status">Loading migration rules…</p>
          )}

          {phase === 'ok' && (
            <p className="migration-rules-validate-status">Migration rules look fine for this source.</p>
          )}

          {phase === 'error' && (
            <div className="edit-flow-connections-error migration-rules-validate-error" role="alert">
              <FiAlertCircle aria-hidden className="migration-rules-validate-error-icon" />
              <p>{errorMessage}</p>
            </div>
          )}

          {showEditor && (
            <>
              {mode === 'edit' && (
                <p className="migration-rules-validate-intro migration-rules-validate-intro-muted">
                  Update field rules, filters, deduplication, data cleanup, or missing-field preference. Changes save
                  immediately.
                </p>
              )}
              {mode === 'after_connection' && (
                <p className="migration-rules-validate-intro migration-rules-validate-intro-muted">
                  Connections were updated. Review migration rules that reference source fields below.
                </p>
              )}
              {mode === 'after_mapping' && (
                <p className="migration-rules-validate-intro migration-rules-validate-intro-muted">
                  Field mappings or the source query were updated. Review rules that reference source fields below.
                </p>
              )}
              {issues.length > 0 && (
                <p className="migration-rules-validate-intro migration-rules-validate-intro-warn">
                  Some rules reference source fields that are not on the current source. Those rows are highlighted;
                  remove or adjust them.
                </p>
              )}
              {fileSkippedInEditor && (
                <p className="migration-rules-validate-intro migration-rules-validate-intro-muted">
                  File flows are not validated against a connector schema; you can still edit saved rules below.
                </p>
              )}

              {extractionRows.length > 0 && (
                <div className="migration-rules-validate-block">
                  <p className="migration-rule-subhead">Field rules (extraction)</p>
                  {renderFieldRulesTable(extractionRows, 'extraction', brokenExtractionRuleIds)}
                </div>
              )}

              {mappingRows.length > 0 && (
                <div className="migration-rules-validate-block">
                  <p className="migration-rule-subhead">Field rules (mapping)</p>
                  {renderFieldRulesTable(mappingRows, 'mapping', brokenMappingRuleIds)}
                </div>
              )}

              {filterConds.length > 0 && (
                <div className="migration-rules-validate-block">
                  <h6 className="migration-rule-nested-title">Filter rules</h6>
                  <p className="migration-rule-filter-lead">{formatFilterLogicDescription(filterLogic)}</p>
                  <p className="migration-rule-filter-logic">
                    Conditions are combined with <strong>{String(filterLogic).toUpperCase()}</strong>.
                  </p>
                  <ul className="migration-rules-validate-filter-list">
                    {filterConds.map((cond, idx) => {
                      const line = formatFilterConditionLine(cond);
                      const busy = removingKey === `fl-${idx}`;
                      const isBroken = brokenFilterIndices.has(idx);
                      return (
                        <li
                          key={`fl-${idx}`}
                          className={`migration-rules-validate-filter-item${isBroken ? ' migration-rules-validate-filter-broken' : ''}`}
                        >
                          <span className="migration-rules-validate-filter-line">{line}</span>
                          <button
                            type="button"
                            className="btn btn-sm migration-rules-validate-btn-inline migration-rules-validate-remove-btn"
                            disabled={busy || bulkWorking}
                            onClick={() => handleRemoveFilterCondition(idx)}
                          >
                            {busy ? '…' : 'Remove condition'}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {dedupFields.length > 0 && (
                <div className="migration-rules-validate-block">
                  <h6 className="migration-rule-nested-title">Deduplication</h6>
                  {dedupBlock?.summaryLines?.length ? (
                    <ul className="migration-rule-bullet-list migration-rules-validate-dedup-summary">
                      {dedupBlock.summaryLines.map((line, i) => (
                        <li key={i}>{line}</li>
                      ))}
                    </ul>
                  ) : null}
                  <ul className="migration-rules-validate-filter-list">
                    {dedupFields.map((fnRaw, idx) => {
                      const fn = String(fnRaw).trim();
                      const busy = removingKey === `dd-${fn}`;
                      const isBroken = brokenDedupFields.has(fn);
                      return (
                        <li
                          key={`dd-${fn}-${idx}`}
                          className={`migration-rules-validate-filter-item${isBroken ? ' migration-rules-validate-filter-broken' : ''}`}
                        >
                          <span className="migration-rules-validate-filter-line">
                            Compare records using: <strong>{fn}</strong>
                          </span>
                          <button
                            type="button"
                            className="btn btn-sm migration-rules-validate-btn-inline migration-rules-validate-remove-btn"
                            disabled={busy || bulkWorking}
                            onClick={() => handleRemoveDedupField(fn)}
                          >
                            {busy ? '…' : 'Remove field'}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {dataCleanupEntries.length > 0 && (
                <div className="migration-rules-validate-block">
                  <h6 className="migration-rule-nested-title">Data cleanup (extraction)</h6>
                  <p className="migration-rules-validate-intro migration-rules-validate-intro-muted">
                    Per-field format and validation applied during extraction.
                  </p>
                  <ul className="migration-rules-validate-filter-list">
                    {dataCleanupEntries.map(([fieldName, spec]) => {
                      const fn = String(fieldName).trim();
                      const line =
                        formatDataCleanupRulesLines({ [fieldName]: spec })?.[0] ??
                        `${fn}: ${typeof spec === 'object' ? JSON.stringify(spec) : String(spec)}`;
                      const busy = removingKey === `dc-${fn}`;
                      const isBroken = brokenDataCleanupFields.has(fn);
                      return (
                        <li
                          key={`dc-${String(fieldName)}`}
                          className={`migration-rules-validate-filter-item${isBroken ? ' migration-rules-validate-filter-broken' : ''}`}
                        >
                          <span className="migration-rules-validate-filter-line">{line}</span>
                          <button
                            type="button"
                            className="btn btn-sm migration-rules-validate-btn-inline migration-rules-validate-remove-btn"
                            disabled={busy || bulkWorking}
                            onClick={() => handleRemoveDataCleanupField(fn)}
                          >
                            {busy ? '…' : 'Remove rule'}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              <div className="migration-rules-validate-block migration-rules-validate-preference-block">
                <p className="migration-rule-subhead">Missing field preference</p>
                <p className="migration-rules-validate-pref-lead">Choose how to handle records missing required mapped fields:</p>
                <label className="migration-rules-validate-radio">
                  <input
                    type="radio"
                    name="mr-pref"
                    checked={prefOption === 'placeholder'}
                    onChange={() => setPrefOption('placeholder')}
                    disabled={prefSaving || bulkWorking}
                  />
                  <span>
                    Fill missing required fields with placeholder:{' '}
                    <input
                      type="text"
                      className="migration-rules-validate-placeholder-input"
                      value={prefPlaceholder}
                      onChange={(e) => setPrefPlaceholder(e.target.value)}
                      disabled={prefOption !== 'placeholder' || prefSaving || bulkWorking}
                      aria-label="Placeholder value"
                    />
                  </span>
                </label>
                <label className="migration-rules-validate-radio">
                  <input
                    type="radio"
                    name="mr-pref"
                    checked={prefOption === 'skip'}
                    onChange={() => setPrefOption('skip')}
                    disabled={prefSaving || bulkWorking}
                  />
                  <span>Skip records with missing required fields</span>
                </label>
                <div className="migration-rules-validate-pref-actions">
                  <button
                    type="button"
                    className="btn btn-sm migration-rules-validate-save-pref"
                    onClick={handleSavePreference}
                    disabled={prefSaving || bulkWorking}
                  >
                    {prefSaving ? 'Saving…' : 'Save preference'}
                  </button>
                </div>
              </div>

              {!snapshotHasEditorContent(snapshot) && (
                <p className="migration-rules-validate-status migration-rules-validate-intro-muted">
                  No extraction or mapping field rules, filters, deduplication, or data cleanup blocks saved yet.
                </p>
              )}
            </>
          )}
        </div>

        <div className="modal-actions migration-rules-validate-actions migration-rules-validate-actions-footer">
          {showEditor && (
            <>
              <button
                type="button"
                className="btn migration-rules-validate-btn-remove-all"
                onClick={handleClearAllIncludingPreference}
                disabled={bulkWorking || !!removingKey || prefSaving}
              >
                Remove everything
              </button>
              <button
                type="button"
                className="btn migration-rules-validate-btn-close"
                onClick={handleEditorClose}
                disabled={bulkWorking || !!removingKey || prefSaving}
              >
                Close
              </button>
            </>
          )}
          {phase === 'error' && (
            <button type="button" className="btn migration-rules-validate-btn-close" onClick={() => onClose()}>
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default MigrationRulesValidationModal;
