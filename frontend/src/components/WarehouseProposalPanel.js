import React, { useCallback, useEffect, useState } from 'react';
import { FiCheck, FiDownload, FiRefreshCw } from 'react-icons/fi';
import api from '../api';
import { watchEnterpriseGraphRun } from '../assessment/assessmentSse';
import '../EnterpriseGraphsPage.css';

function TableList({ tables }) {
  if (!tables?.length) {
    return <p className="eg-wizard-hint">No tables in this proposal.</p>;
  }
  return (
    <ul className="eg-warehouse-table-list">
      {tables.map((t) => (
        <li key={t.id || t.name} className={`eg-warehouse-table eg-warehouse-table--${t.table_type}`}>
          <div className="eg-warehouse-table-head">
            <strong>{t.name}</strong>
            <span className="eg-mapping-badge">{t.table_type}</span>
          </div>
          <span className="eg-wizard-hint">{t.grain}</span>
          <ul className="eg-warehouse-columns">
            {(t.columns || []).slice(0, 8).map((c) => (
              <li key={c.name}>
                <code>{c.name}</code>
                <span>{c.type}</span>
                {c.role === 'surrogate_key' && <span className="eg-mapping-badge">PK</span>}
              </li>
            ))}
            {(t.columns || []).length > 8 && (
              <li className="eg-wizard-hint">+{(t.columns || []).length - 8} more columns</li>
            )}
          </ul>
          {(t.foreign_keys || []).length > 0 && (
            <p className="eg-wizard-hint">
              FKs:{' '}
              {t.foreign_keys.map((fk) => `${fk.column} → ${fk.references_table}`).join(', ')}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function WarehouseProposalPanel({ graphId }) {
  const [proposal, setProposal] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [approving, setApproving] = useState(false);
  const [redesigning, setRedesigning] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [showDdl, setShowDdl] = useState(false);

  const loadProposal = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get(`/api/enterprise-graphs/${graphId}/warehouse-proposals/latest`);
      setProposal(data.proposal || null);
    } catch (e) {
      if (e.response?.status === 404) {
        setProposal(null);
      } else {
        setError(e.response?.data?.detail || e.message || 'Failed to load proposal');
      }
    } finally {
      setLoading(false);
    }
  }, [graphId]);

  useEffect(() => {
    loadProposal();
  }, [loadProposal]);

  const approve = async () => {
    if (!proposal?.id) return;
    setApproving(true);
    setError(null);
    try {
      const { data } = await api.post(
        `/api/enterprise-graphs/${graphId}/warehouse-proposals/${proposal.id}/approve`,
      );
      setProposal(data.proposal);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Approve failed');
    } finally {
      setApproving(false);
    }
  };

  const redesign = async () => {
    setRedesigning(true);
    setError(null);
    try {
      const { data } = await api.post(`/api/enterprise-graphs/${graphId}/runs`, {
        depth: 'partial',
        run_type: 'warehouse_redesign',
      });
      await watchEnterpriseGraphRun(data.run_id);
      setRedesigning(false);
      await loadProposal();
    } catch (e) {
      setRedesigning(false);
      setError(e.response?.data?.detail?.message || e.message || 'Redesign failed');
    }
  };

  const exportJson = async () => {
    try {
      const { data } = await api.get(`/api/enterprise-graphs/${graphId}/export`, {
        params: { format: 'json' },
      });
      const blob = new Blob([JSON.stringify(data.export, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `enterprise-graph-${graphId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Export failed');
    }
  };

  const executeDdl = async () => {
    if (!proposal?.id) return;
    setExecuting(true);
    setError(null);
    try {
      await api.post(`/api/enterprise-graphs/${graphId}/warehouse-proposals/${proposal.id}/execute`);
      await loadProposal();
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'DDL execution failed');
    } finally {
      setExecuting(false);
    }
  };

  if (loading) {
    return <p className="eg-wizard-hint">Loading warehouse proposal…</p>;
  }

  const logical = proposal?.logical_model || {};
  const tables = logical.tables || [];
  const lineage = proposal?.source_to_warehouse_map?.mappings || [];
  const designMethod = proposal?.design_method || logical.design_method || 'rules';
  const excludedEntities = logical.excluded_entities || [];
  const designMethodLabel =
    designMethod === 'llm'
      ? 'AI-designed'
      : designMethod === 'rules_fallback'
        ? 'Rules (LLM fallback)'
        : 'Rules-based';

  return (
    <div className="eg-warehouse-block">
      <div className="eg-wizard-card">
        <div className="eg-mappings-head">
          <h2>Warehouse schema proposal</h2>
          <div className="eg-mapping-actions">
            <button type="button" className="eg-secondary-btn" disabled={redesigning} onClick={redesign}>
              <FiRefreshCw aria-hidden />
              {redesigning ? 'Regenerating…' : 'Regenerate'}
            </button>
            <button type="button" className="eg-secondary-btn" onClick={exportJson}>
              <FiDownload aria-hidden />
              Export JSON
            </button>
            {proposal?.status === 'draft' && (
              <button type="button" className="eg-confirm-btn" disabled={approving} onClick={approve}>
                <FiCheck aria-hidden />
                {approving ? 'Approving…' : 'Approve proposal'}
              </button>
            )}
            {proposal && (
              <button
                type="button"
                className="eg-primary-btn"
                disabled={executing}
                onClick={executeDdl}
              >
                {executing ? 'Running DDL…' : 'Run DDL'}
              </button>
            )}
          </div>
        </div>

        {!proposal && (
          <p className="eg-wizard-hint">
            No warehouse proposal yet. Run a data assessment to generate a logical model and DDL preview.
          </p>
        )}

        {error && <div className="eg-error">{error}</div>}

        {proposal && (
          <>
            <p className="eg-warehouse-rationale">{proposal.rationale}</p>
            <p className="eg-wizard-hint">
              Dialect: <strong>{proposal.dialect}</strong>
              {' · '}
              Version {proposal.version}
              {' · '}
              Design:{' '}
              <span className={`eg-mapping-badge eg-mapping-badge--${designMethod === 'llm' ? 'suggested' : 'confirmed'}`}>
                {designMethodLabel}
              </span>
              {' · '}
              Status:{' '}
              <span className={`eg-mapping-badge eg-mapping-badge--${proposal.status}`}>
                {proposal.status}
              </span>
              {proposal.status === 'approved' && (
                <span> (stored only — no tables created)</span>
              )}
            </p>

            {excludedEntities.length > 0 && (
              <div className="eg-warehouse-gaps">
                <h3>Excluded from warehouse ({excludedEntities.length})</h3>
                <p className="eg-wizard-hint">
                  Platform, log, or low-value entities omitted from the logical model.
                </p>
                <ul>
                  {excludedEntities.slice(0, 12).map((ex, i) => (
                    <li key={ex.entity_id || ex.canonical_id || i}>
                      <strong>{ex.label || ex.entity_id || ex.canonical_id}</strong>
                      {ex.reason && <span className="eg-wizard-hint"> — {ex.reason}</span>}
                    </li>
                  ))}
                </ul>
                {excludedEntities.length > 12 && (
                  <p className="eg-wizard-hint">+{excludedEntities.length - 12} more</p>
                )}
              </div>
            )}

            {(proposal.gaps || []).length > 0 && (
              <div className="eg-warehouse-gaps">
                <h3>Gaps ({proposal.gaps.length})</h3>
                <ul>
                  {proposal.gaps.map((g, i) => (
                    <li key={i}>
                      <span className={`eg-mapping-badge eg-mapping-badge--${g.severity === 'high' ? 'rejected' : 'suggested'}`}>
                        {g.severity}
                      </span>
                      {g.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <h3 className="eg-mappings-subhead">Logical model</h3>
            <TableList tables={tables} />

            {lineage.length > 0 && (
              <>
                <h3 className="eg-mappings-subhead">Column lineage</h3>
                <div className="eg-warehouse-lineage-wrap">
                  <table className="eg-warehouse-lineage-table">
                    <thead>
                      <tr>
                        <th>Warehouse</th>
                        <th>Column</th>
                        <th>Source entity</th>
                        <th>Source column</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lineage.slice(0, 50).map((row, i) => (
                        <tr key={i}>
                          <td>{row.warehouse_table}</td>
                          <td>
                            <code>{row.warehouse_column}</code>
                            {row.generated && ' (generated)'}
                          </td>
                          <td>{row.source_entity_id || '—'}</td>
                          <td>{row.source_column || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {lineage.length > 50 && (
                    <p className="eg-wizard-hint">Showing 50 of {lineage.length} mappings</p>
                  )}
                </div>
              </>
            )}

            <h3 className="eg-mappings-subhead">DDL preview</h3>
            <button
              type="button"
              className="eg-secondary-btn"
              style={{ marginBottom: 8 }}
              onClick={() => setShowDdl((v) => !v)}
            >
              {showDdl ? 'Hide DDL' : 'Show DDL'}
            </button>
            {showDdl && (
              <pre className="eg-warehouse-ddl">{proposal.ddl_text || '-- No DDL generated'}</pre>
            )}
          </>
        )}
      </div>
    </div>
  );
}
