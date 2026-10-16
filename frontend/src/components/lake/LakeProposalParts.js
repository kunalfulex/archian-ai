import React, { useMemo, useState } from 'react';
import { FiCheck, FiDownload, FiRefreshCw } from 'react-icons/fi';

export function LakeBronzeTableList({ tables }) {
  const rows = tables || [];
  if (!rows.length) {
    return <p className="eg-wizard-hint">No bronze tables in this proposal.</p>;
  }
  return (
    <ul className="eg-warehouse-table-list">
      {rows.map((t) => (
        <li key={t.name} className="eg-warehouse-table">
          <div className="eg-warehouse-table-head">
            <strong>{t.name}</strong>
            <span className="eg-mapping-badge">bronze</span>
          </div>
          <span className="eg-wizard-hint">
            {t.label || t.source_entity_id} · {t.source_connector_type || 'source'}
          </span>
          <ul className="eg-warehouse-columns">
            {(t.columns || []).slice(0, 8).map((c) => (
              <li key={c.name}>
                <code>{c.name}</code>
                <span>{c.type}</span>
              </li>
            ))}
            {(t.columns || []).length > 8 && (
              <li className="eg-wizard-hint">+{(t.columns || []).length - 8} more columns</li>
            )}
          </ul>
        </li>
      ))}
    </ul>
  );
}

export function LakeSilverViewList({ views }) {
  const rows = views || [];
  if (!rows.length) {
    return <p className="eg-wizard-hint">No silver views in this proposal.</p>;
  }
  return (
    <ul className="eg-warehouse-table-list">
      {rows.map((v) => (
        <li key={v.name} className="eg-warehouse-table eg-warehouse-table--dimension">
          <div className="eg-warehouse-table-head">
            <strong>{v.name}</strong>
            <span className="eg-mapping-badge">silver</span>
          </div>
          <span className="eg-wizard-hint">
            {v.canonical_name || v.canonical_entity_id} · {v.branch_count || 0} source branch(es)
          </span>
        </li>
      ))}
    </ul>
  );
}

export function LakeGapsList({ gaps }) {
  if (!gaps?.length) {
    return <p className="eg-wizard-hint">No gaps flagged for this lake proposal.</p>;
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
        </li>
      ))}
    </ul>
  );
}

export function LakeLineageTable({ sourceMap, pageSize = 50 }) {
  const [page, setPage] = useState(0);
  const rows = useMemo(() => {
    const out = [];
    (sourceMap?.mappings || []).forEach((m) => {
      const colMap = m.column_map || {};
      Object.entries(colMap).forEach(([srcCol, bronzeCol]) => {
        out.push({
          bronze_table: m.bronze_table,
          bronze_column: bronzeCol,
          source_entity_id: m.source_entity_id,
          source_column: srcCol,
        });
      });
    });
    return out;
  }, [sourceMap]);
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
              <th>Bronze table</th>
              <th>Bronze column</th>
              <th>Source entity</th>
              <th>Source column</th>
            </tr>
          </thead>
          <tbody>
            {slice.map((row, i) => (
              <tr key={`${page}-${i}`}>
                <td><code>{row.bronze_table}</code></td>
                <td><code>{row.bronze_column}</code></td>
                <td>{row.source_entity_id || '—'}</td>
                <td>{row.source_column || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > pageSize && (
        <div className="eg-lineage-pagination">
          <button type="button" className="eg-secondary-btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
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

export function LakeProposalToolbar({
  proposal,
  approving,
  redesigning,
  executing,
  onRedesign,
  onExportLineage,
  onApprove,
  onExecuteDdl,
}) {
  return (
    <div className="eg-mapping-actions eg-warehouse-toolbar">
      <button type="button" className="eg-secondary-btn" disabled={redesigning} onClick={onRedesign}>
        <FiRefreshCw aria-hidden />
        {redesigning ? 'Regenerating…' : 'Regenerate'}
      </button>
      {proposal && (
        <button type="button" className="eg-secondary-btn" onClick={onExportLineage}>
          <FiDownload aria-hidden />
          Export lineage
        </button>
      )}
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

export function LakeProposalOverview({ proposal, showDdl, onToggleDdl }) {
  if (!proposal) {
    return (
      <p className="eg-wizard-hint">
        No lake proposal yet. Enable lake on the graph and run a data assessment to generate bronze
        and silver layout DDL.
      </p>
    );
  }

  const summary = proposal.logical_model?.summary || {};
  const zones = proposal.logical_model?.zones || {};

  return (
    <>
      <p className="eg-wizard-hint eg-lake-flows-hint">
        <strong>Raw data:</strong> After you run DDL, use chat flows to extract from each source and
        migrate into the bronze tables below. Save flows to repeat loads on a schedule.
      </p>
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
          <strong>{proposal.status}</strong>
        </div>
        <div>
          <span className="eg-meta-label">Bronze tables</span>
          <strong>{summary.bronze_table_count ?? 0}</strong>
        </div>
        <div>
          <span className="eg-meta-label">Silver views</span>
          <strong>{summary.silver_view_count ?? 0}</strong>
        </div>
        <div>
          <span className="eg-meta-label">Schemas</span>
          <strong>
            {zones.bronze_schema || '—'} / {zones.silver_schema || '—'}
          </strong>
        </div>
      </div>
      {proposal.ddl_text && (
        <div className="eg-warehouse-ddl-block">
          <button type="button" className="eg-secondary-btn" onClick={onToggleDdl}>
            {showDdl ? 'Hide DDL' : 'Show DDL preview'}
          </button>
          {showDdl && <pre className="eg-warehouse-ddl">{proposal.ddl_text}</pre>}
        </div>
      )}
    </>
  );
}
