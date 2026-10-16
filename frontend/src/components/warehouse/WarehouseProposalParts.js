import React, { useMemo, useState } from 'react';
import { FiCheck, FiDownload, FiRefreshCw } from 'react-icons/fi';

export function WarehouseTableList({ tables, filter = 'all' }) {
  const filtered = useMemo(() => {
    if (filter === 'fact') return (tables || []).filter((t) => t.table_type === 'fact');
    if (filter === 'dimension') return (tables || []).filter((t) => t.table_type === 'dimension');
    return tables || [];
  }, [tables, filter]);

  if (!filtered.length) {
    return <p className="eg-wizard-hint">No tables in this view.</p>;
  }

  return (
    <ul className="eg-warehouse-table-list">
      {filtered.map((t) => (
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
              FKs: {t.foreign_keys.map((fk) => `${fk.column} → ${fk.references_table}`).join(', ')}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export function WarehouseGapsList({ gaps }) {
  if (!gaps?.length) {
    return <p className="eg-wizard-hint">No gaps flagged for this proposal.</p>;
  }
  return (
    <ul className="eg-warehouse-gaps-list">
      {gaps.map((g, i) => (
        <li key={i} className="eg-warehouse-gap-item">
          <span
            className={`eg-mapping-badge eg-mapping-badge--${
              g.severity === 'high' ? 'rejected' : 'suggested'
            }`}
          >
            {g.severity}
          </span>
          <span>{g.message}</span>
          {g.warehouse_table && (
            <span className="eg-wizard-hint"> — {g.warehouse_table}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function WarehouseLineageTable({ lineage, pageSize = 50 }) {
  const [page, setPage] = useState(0);
  const rows = lineage || [];
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  const slice = rows.slice(page * pageSize, page * pageSize + pageSize);

  if (!rows.length) {
    return <p className="eg-wizard-hint">No column lineage mappings yet.</p>;
  }

  return (
    <>
      <div className="eg-warehouse-lineage-wrap">
        <table className="eg-warehouse-lineage-table">
          <thead>
            <tr>
              <th>Warehouse table</th>
              <th>Column</th>
              <th>Source entity</th>
              <th>Source column</th>
            </tr>
          </thead>
          <tbody>
            {slice.map((row, i) => (
              <tr key={`${page}-${i}`}>
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
      </div>
      {rows.length > pageSize && (
        <div className="eg-lineage-pagination">
          <button
            type="button"
            className="eg-secondary-btn"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </button>
          <span className="eg-wizard-hint">
            Page {page + 1} of {totalPages} ({rows.length} rows)
          </span>
          <button
            type="button"
            className="eg-secondary-btn"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </>
  );
}

export function WarehouseProposalToolbar({
  proposal,
  approving,
  redesigning,
  executing,
  onRedesign,
  onExport,
  onApprove,
  onExecuteDdl,
}) {
  return (
    <div className="eg-mapping-actions eg-warehouse-toolbar">
      <button type="button" className="eg-secondary-btn" disabled={redesigning} onClick={onRedesign}>
        <FiRefreshCw aria-hidden />
        {redesigning ? 'Regenerating…' : 'Regenerate'}
      </button>
      <button type="button" className="eg-secondary-btn" onClick={onExport}>
        <FiDownload aria-hidden />
        Export JSON
      </button>
      {proposal?.status === 'draft' && (
        <button type="button" className="eg-confirm-btn" disabled={approving} onClick={onApprove}>
          <FiCheck aria-hidden />
          {approving ? 'Approving…' : 'Approve proposal'}
        </button>
      )}
      {proposal && (
        <button
          type="button"
          className="eg-primary-btn"
          disabled={executing}
          onClick={onExecuteDdl}
        >
          {executing ? 'Running DDL…' : 'Run DDL'}
        </button>
      )}
    </div>
  );
}

export function WarehouseProposalOverview({ proposal, showDdl, onToggleDdl }) {
  if (!proposal) {
    return (
      <p className="eg-wizard-hint">
        No warehouse proposal yet. Run a data assessment to generate a logical model and DDL preview.
      </p>
    );
  }

  const summary = proposal.logical_model?.summary || {};

  return (
    <>
      <p className="eg-warehouse-rationale">{proposal.rationale}</p>
      <div className="eg-warehouse-meta-grid">
        <div>
          <span className="eg-meta-label">Dialect</span>
          <strong>{proposal.dialect}</strong>
        </div>
        <div>
          <span className="eg-meta-label">Version</span>
          <strong>{proposal.version}</strong>
        </div>
        <div>
          <span className="eg-meta-label">Status</span>
          <span className={`eg-mapping-badge eg-mapping-badge--${proposal.status}`}>
            {proposal.status}
          </span>
        </div>
        <div>
          <span className="eg-meta-label">Tables</span>
          <strong>{summary.table_count ?? '—'}</strong>
        </div>
        <div>
          <span className="eg-meta-label">Facts / dims</span>
          <strong>
            {summary.fact_count ?? 0} / {summary.dimension_count ?? 0}
          </strong>
        </div>
      </div>
      {proposal.status === 'approved' && (
        <p className="eg-wizard-hint">Approved for execution. Use Run DDL to create/update warehouse tables.</p>
      )}

      <h3 className="eg-mappings-subhead">DDL preview</h3>
      <button type="button" className="eg-secondary-btn" onClick={onToggleDdl}>
        {showDdl ? 'Hide DDL' : 'Show DDL'}
      </button>
      {showDdl && (
        <pre className="eg-warehouse-ddl">{proposal.ddl_text || '-- No DDL generated'}</pre>
      )}
    </>
  );
}
