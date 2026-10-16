import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FiCheck, FiEdit2, FiRefreshCw, FiX } from 'react-icons/fi';
import api from '../api';
import { watchEnterpriseGraphRun } from '../assessment/assessmentSse';
import '../EnterpriseGraphsPage.css';

function groupMappingsByCanonical(rows) {
  const groups = new Map();
  for (const row of rows || []) {
    const cid = row.canonical_entity_id || row.id;
    if (!groups.has(cid)) {
      groups.set(cid, {
        canonical_entity_id: cid,
        rows: [],
        confidence: row.confidence,
        status: row.status,
        mapping_kind: row.mapping_kind,
      });
    }
    const g = groups.get(cid);
    g.rows.push(row);
    if (row.confidence != null) g.confidence = Math.max(g.confidence ?? 0, row.confidence);
    if (row.mapping_kind === 'flow_prior') g.mapping_kind = 'flow_prior';
    if (row.status === 'confirmed') g.status = 'confirmed';
    else if (row.status === 'rejected' && g.status !== 'confirmed') g.status = 'rejected';
    else if ((row.status || 'suggested') === 'suggested' && g.status !== 'confirmed' && g.status !== 'rejected') {
      g.status = 'suggested';
    }
  }
  return [...groups.values()].filter((g) => g.rows.length >= 2);
}

function entityDisplayLabel(row, memberEntities) {
  const mid = row.source_member_id;
  const eid = row.source_entity_id;
  if (row.source_entity_label) return row.source_entity_label;
  const options = memberEntities[mid] || [];
  const match = options.find((o) => o.entity_id === eid);
  return match?.label || eid;
}

function canonicalNameFromGroup(group) {
  for (const row of group.rows || []) {
    const ev = row.evidence;
    if (!ev) continue;
    if (typeof ev === 'object' && ev.canonical_name) return ev.canonical_name;
  }
  return null;
}

function formatPair(group, memberNames, memberEntities = {}) {
  return group.rows
    .map((r) => {
      const name = memberNames[r.source_member_id] || r.source_member_id;
      return `${name}: ${entityDisplayLabel(r, memberEntities)}`;
    })
    .join(' ↔ ');
}

function MappingClusterBody({ group, memberNames, memberEntities, compact }) {
  const canonical = canonicalNameFromGroup(group);
  const useList = !compact && group.rows.length > 2;

  if (!useList) {
    return <strong className="eg-mapping-pair-line">{formatPair(group, memberNames, memberEntities)}</strong>;
  }

  return (
    <div className="eg-mapping-cluster">
      {canonical && <div className="eg-mapping-canonical">{canonical}</div>}
      <ul className="eg-mapping-entity-list">
        {group.rows.map((r) => (
          <li key={`${r.source_member_id}:${r.source_entity_id}`} className="eg-mapping-entity-row">
            <span className="eg-mapping-entity-member">
              {memberNames[r.source_member_id] || r.source_member_id}
            </span>
            <span className="eg-mapping-entity-label">
              {entityDisplayLabel(r, memberEntities)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function mappingKindLabel(kind) {
  if (kind === 'flow_prior') return 'From saved flow';
  if (kind === 'llm') return 'AI-matched';
  if (kind === 'heuristic') return 'Auto-matched';
  return null;
}

function buildDraftFromGroup(group) {
  const draft = {};
  for (const r of group.rows) {
    draft[r.source_member_id] = r.source_entity_id;
  }
  return draft;
}

export default function SemanticMappingsEditor({ graphId, onMappingsChanged, embedded = false }) {
  const [mappings, setMappings] = useState([]);
  const [memberNames, setMemberNames] = useState({});
  const [memberEntities, setMemberEntities] = useState({});
  const [loading, setLoading] = useState(true);
  const [remapping, setRemapping] = useState(false);
  const [reopening, setReopening] = useState(false);
  const [savingCanonicalId, setSavingCanonicalId] = useState(null);
  const [editingCanonicalId, setEditingCanonicalId] = useState(null);
  const [draftEntities, setDraftEntities] = useState({});
  const [infoMessage, setInfoMessage] = useState(null);
  const [error, setError] = useState(null);

  const loadMappings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [mapRes, graphRes, entRes] = await Promise.all([
        api.get(`/api/enterprise-graphs/${graphId}/mappings`),
        api.get(`/api/enterprise-graphs/${graphId}`),
        api.get(`/api/enterprise-graphs/${graphId}/member-entities`),
      ]);
      const rows = mapRes.data?.mappings || [];
      setMappings(rows);
      const names = {};
      for (const m of graphRes.data?.graph?.members || []) {
        names[m.id] = m.display_name || m.connector_type;
      }
      setMemberNames(names);
      const entByMember = {};
      for (const m of entRes.data?.members || []) {
        entByMember[m.member_id] = m.entities || [];
      }
      setMemberEntities(entByMember);
      return rows;
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to load mappings');
      return [];
    } finally {
      setLoading(false);
    }
  }, [graphId]);

  useEffect(() => {
    loadMappings();
  }, [loadMappings]);

  const groups = useMemo(() => groupMappingsByCanonical(mappings), [mappings]);
  const suggested = groups.filter((g) => (g.status || 'suggested') === 'suggested');
  const decided = groups.filter((g) => g.status === 'confirmed' || g.status === 'rejected');

  const patchGroup = async (group, { status, entities, closeEdit = true }) => {
    const mappingId = group.rows[0]?.id;
    if (!mappingId) {
      setError('This mapping is missing an id — refresh the page and try again.');
      return;
    }
    setError(null);
    setSavingCanonicalId(group.canonical_entity_id);
    try {
      const body = {};
      if (status) body.status = status;
      if (entities?.length) body.entities = entities;
      const { data } = await api.patch(
        `/api/enterprise-graphs/${graphId}/mappings/${mappingId}`,
        body,
      );
      setMappings(data.mappings || []);
      if (closeEdit) {
        setEditingCanonicalId(null);
        setDraftEntities({});
      }
      onMappingsChanged?.();
    } catch (e) {
      const detail = e.response?.data?.detail;
      setError(
        typeof detail === 'string'
          ? detail
          : detail?.message || e.message || 'Update failed',
      );
    } finally {
      setSavingCanonicalId(null);
    }
  };

  const startEdit = (group) => {
    setEditingCanonicalId(group.canonical_entity_id);
    setDraftEntities(buildDraftFromGroup(group));
    setInfoMessage(null);
  };

  const cancelEdit = () => {
    setEditingCanonicalId(null);
    setDraftEntities({});
  };

  const saveEdit = async (group) => {
    const entities = group.rows.map((r) => ({
      source_member_id: r.source_member_id,
      source_entity_id: draftEntities[r.source_member_id] || r.source_entity_id,
    }));
    await patchGroup(group, { entities });
  };

  const setStatus = async (group, status) => {
    await patchGroup(group, { status });
  };

  const runRemap = async (reopenForReview = false) => {
    setRemapping(true);
    setError(null);
    setInfoMessage(null);
    try {
      const { data } = await api.post(`/api/enterprise-graphs/${graphId}/runs`, {
        depth: 'partial',
        run_type: 'remap',
        reopen_for_review: reopenForReview,
      });
      await watchEnterpriseGraphRun(data.run_id);
      setRemapping(false);
      const rows = await loadMappings();
      onMappingsChanged?.();
      const pending = groupMappingsByCanonical(rows).filter(
        (g) => (g.status || 'suggested') === 'suggested',
      );
      if (pending.length === 0) {
        setInfoMessage(
          reopenForReview
            ? 'Mappings are open for review. Confirm or reject each pair below.'
            : 'Re-linking finished. No new pairings to review — confirmed clusters match the linker output. Use Edit to change entities, or Re-open confirmed for review.',
        );
      } else {
        setInfoMessage(
          `${pending.length} mapping${pending.length === 1 ? '' : 's'} ready for review.`,
        );
      }
    } catch (e) {
      setRemapping(false);
      setError(e.response?.data?.detail?.message || e.message || 'Remap failed');
    }
  };

  const reopenConfirmed = async () => {
    setReopening(true);
    setError(null);
    setInfoMessage(null);
    try {
      const { data } = await api.post(`/api/enterprise-graphs/${graphId}/mappings/reopen`);
      setMappings(data.mappings || []);
      setInfoMessage(
        data.reopened_count
          ? `${data.reopened_count} confirmed mapping(s) moved back to suggested for review.`
          : 'No confirmed mappings to re-open.',
      );
      onMappingsChanged?.();
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Re-open failed');
    } finally {
      setReopening(false);
    }
  };

  const renderMappingRow = (group, { compact = false } = {}) => {
    const isEditing = editingCanonicalId === group.canonical_entity_id;
    const isSaving = savingCanonicalId === group.canonical_entity_id;
    const status = group.status || 'suggested';

    return (
      <li
        key={group.canonical_entity_id}
        className={`eg-mapping-item eg-mapping-item--${status}${compact ? ' eg-mapping-item--compact' : ''}`}
      >
        <div className="eg-mapping-item-main">
          {!isEditing ? (
            <div className="eg-mapping-item-body">
              <MappingClusterBody
                group={group}
                memberNames={memberNames}
                memberEntities={memberEntities}
                compact={compact}
              />
              {!compact && status === 'suggested' && (
                <span className="eg-mapping-confidence">
                  {mappingKindLabel(group.mapping_kind) && (
                    <span className="eg-mapping-kind-badge">{mappingKindLabel(group.mapping_kind)}</span>
                  )}
                  {isSaving
                    ? 'Saving…'
                    : `${Math.round((group.confidence || 0) * 100)}% confidence`}
                </span>
              )}
            </div>
          ) : (
            <div className="eg-mapping-edit-fields">
              {group.rows.map((r) => {
                const mid = r.source_member_id;
                const options = memberEntities[mid] || [];
                return (
                  <label key={mid} className="eg-mapping-edit-row">
                    <span className="eg-mapping-edit-label">
                      {memberNames[mid] || mid}
                    </span>
                    <select
                      className="eg-mapping-edit-select"
                      value={draftEntities[mid] ?? r.source_entity_id}
                      disabled={isSaving}
                      onChange={(e) =>
                        setDraftEntities((prev) => ({
                          ...prev,
                          [mid]: e.target.value,
                        }))
                      }
                    >
                      {options.length === 0 ? (
                        <option value={r.source_entity_id}>{r.source_entity_id}</option>
                      ) : (
                        options.map((opt) => (
                          <option key={opt.entity_id} value={opt.entity_id}>
                            {opt.label || opt.entity_id}
                          </option>
                        ))
                      )}
                    </select>
                  </label>
                );
              })}
            </div>
          )}

          <div className="eg-mapping-actions">
            {isEditing ? (
              <>
                <button
                  type="button"
                  className="eg-confirm-btn"
                  disabled={isSaving}
                  onClick={() => saveEdit(group)}
                >
                  Save
                </button>
                <button
                  type="button"
                  className="eg-secondary-btn"
                  disabled={isSaving}
                  onClick={cancelEdit}
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="eg-secondary-btn eg-mapping-edit-btn"
                  disabled={isSaving || remapping}
                  onClick={() => startEdit(group)}
                  title="Change which objects are linked"
                >
                  <FiEdit2 aria-hidden /> Edit
                </button>
                {status === 'suggested' && (
                  <>
                    <button
                      type="button"
                      className="eg-confirm-btn"
                      disabled={isSaving || remapping}
                      onClick={() => setStatus(group, 'confirmed')}
                    >
                      <FiCheck aria-hidden /> Confirm
                    </button>
                    <button
                      type="button"
                      className="eg-reject-btn"
                      disabled={isSaving || remapping}
                      onClick={() => setStatus(group, 'rejected')}
                    >
                      <FiX aria-hidden /> Reject
                    </button>
                  </>
                )}
              </>
            )}
          </div>
        </div>

        {compact && !isEditing && (
          <span className={`eg-mapping-badge eg-mapping-badge--${status}`}>{status}</span>
        )}
      </li>
    );
  };

  if (loading) {
    return <p className="eg-wizard-hint">Loading entity mappings…</p>;
  }

  const inner = (
    <>
      <div className="eg-mappings-head">
        {!embedded && <h2>Cross-connector mappings</h2>}
        {embedded && (
          <p className="eg-wizard-hint" style={{ margin: 0, flex: 1 }}>
            Link entities across connectors. Edit object choices, then confirm or reject each
            cluster.
          </p>
        )}
        <div className="eg-mappings-head-actions">
          <button
            type="button"
            className="eg-secondary-btn"
            disabled={remapping || reopening}
            onClick={() => runRemap(false)}
            title="Refresh AI suggestions; keeps confirmed clusters that match"
          >
            <FiRefreshCw aria-hidden />
            {remapping ? 'Re-linking…' : 'Re-run linking'}
          </button>
          <button
            type="button"
            className="eg-secondary-btn"
            disabled={remapping || reopening}
            onClick={reopenConfirmed}
            title="Move confirmed mappings back to suggested"
          >
            {reopening ? 'Re-opening…' : 'Re-open confirmed'}
          </button>
        </div>
      </div>
      {!embedded && (
        <p className="eg-wizard-hint">
          Use Edit to pick objects from each connector, then Confirm or Reject. Re-run linking
          adds new suggestions; Re-open confirmed sends existing confirmations back to review.
        </p>
      )}
      {error && <div className="eg-error">{typeof error === 'string' ? error : JSON.stringify(error)}</div>}
      {infoMessage && <p className="eg-mapping-info">{infoMessage}</p>}

      {suggested.length === 0 && decided.length === 0 && (
        <p className="eg-wizard-hint">No cross-connector mappings yet. Run a data assessment first.</p>
      )}

      {suggested.length > 0 && (
        <>
          <h3 className="eg-mappings-subhead">To review</h3>
          <ul className="eg-mapping-list">{suggested.map((g) => renderMappingRow(g))}</ul>
        </>
      )}

      {decided.length > 0 && (
        <>
          <h3 className="eg-mappings-subhead">Reviewed</h3>
          <ul className="eg-mapping-list eg-mapping-list--compact">
            {decided.map((g) => renderMappingRow(g, { compact: true }))}
          </ul>
        </>
      )}
    </>
  );

  if (embedded) {
    return inner;
  }

  return (
    <div className="eg-mappings-block">
      <div className="eg-wizard-card">{inner}</div>
    </div>
  );
}
