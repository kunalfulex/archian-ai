import React, { useMemo, useState } from 'react';
import api from '../api';
import ConnectorSchemaGraphView from './ConnectorSchemaGraphView';
import ConnectedEnvironmentView from './ConnectedEnvironmentView';
import EnterpriseCanonicalSchemaView from './EnterpriseCanonicalSchemaView';
import {
  DIMENSION_LABELS,
  buildEntityLabelMap,
  entityLabel,
  formatKeyFieldsLabel,
  groupGapFindings,
  resolveDataQualitySection,
} from '../utils/assessmentReportDisplay';
import {
  buildDashboardMetrics,
  pickSchemaMapPreview,
} from '../utils/assessmentDashboardMetrics';
import './AssessmentDashboard.css';

const TABS = [
  { id: 'snapshot', label: 'Snapshot' },
  { id: 'readiness', label: 'Readiness' },
  { id: 'quality', label: 'Data quality' },
  { id: 'schema', label: 'Schema map' },
  { id: 'gaps', label: 'Data gaps' },
];

function ScoreRing({ score, size = 88 }) {
  const value = score != null ? Math.round(score) : 0;
  const r = (size - 10) / 2;
  const c = 2 * Math.PI * r;
  const offset = c - (value / 100) * c;
  return (
    <svg width={size} height={size} className="assessment-score-ring" aria-hidden>
      <circle className="assessment-score-ring-bg" cx={size / 2} cy={size / 2} r={r} />
      <circle
        className="assessment-score-ring-fill"
        cx={size / 2}
        cy={size / 2}
        r={r}
        strokeDasharray={c}
        strokeDashoffset={offset}
      />
      <text x="50%" y="50%" className="assessment-score-ring-text" textAnchor="middle" dominantBaseline="middle">
        {score != null ? value : '—'}
      </text>
    </svg>
  );
}

function DimensionBars({ dimensions, compact }) {
  const entries = Object.entries(dimensions || {});
  if (!entries.length) return <p className="assessment-dash-muted">No dimension scores in this report.</p>;
  return (
    <ul className={`assessment-dimension-bars${compact ? ' assessment-dimension-bars--compact' : ''}`}>
      {entries.map(([key, val]) => {
        const num = typeof val === 'number' ? val : parseFloat(val);
        const pct = Number.isNaN(num) ? 0 : Math.min(100, Math.max(0, num));
        const barClass = pct >= 75 ? 'good' : pct >= 55 ? 'fair' : 'low';
        return (
          <li key={key}>
            <div className="assessment-dimension-bars-label">
              <span title={DIMENSION_LABELS[key]}>
                {(DIMENSION_LABELS[key] || key).split('—')[0].trim()}
              </span>
              <span className="assessment-dimension-bars-pct">{Math.round(pct)}%</span>
            </div>
            <div className="assessment-dimension-bars-track">
              <div className={`assessment-dimension-bars-fill assessment-dimension-bars-fill--${barClass}`} style={{ width: `${pct}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function SchemaMapMini({ graph }) {
  const { chain, edges: previewEdges } = useMemo(() => pickSchemaMapPreview(graph), [graph]);
  if (!chain.length) {
    return <p className="assessment-dash-muted">Run assessment with schema sync to see relationships.</p>;
  }
  return (
    <div className="assessment-schema-mini">
      <div className="assessment-schema-mini-flow">
        {chain.map((node, i) => (
          <React.Fragment key={node.id}>
            {i > 0 && <span className="assessment-schema-mini-dots" aria-hidden />}
            <div className="assessment-schema-mini-box">
              <span className="assessment-schema-mini-name">{node.label || node.id}</span>
              <span className="assessment-schema-mini-meta">
                {previewEdges.filter((e) => e.source_id === node.id || e.target_id === node.id).length}{' '}
                links
              </span>
            </div>
          </React.Fragment>
        ))}
      </div>
      <p className="assessment-schema-mini-foot">
        {graph?.summary?.node_count ?? chain.length} entities · {graph?.summary?.edge_count ?? 0} relationships
      </p>
    </div>
  );
}

function EntityQualityRow({ row, labelMap }) {
  const [open, setOpen] = useState(false);
  const name = row.label || entityLabel(row.entity_id, labelMap);
  return (
    <div className="assessment-entity-row">
      <button type="button" className="assessment-entity-row-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="assessment-entity-row-name">{name}</span>
        {row.error ? (
          <span className="assessment-badge assessment-badge--alert">Error</span>
        ) : (
          <span className="assessment-badge">{row.score != null ? `${Math.round(row.score)}/100` : '—'}</span>
        )}
        <span className="assessment-entity-row-chevron">{open ? '▾' : '▸'}</span>
      </button>
      {open && !row.error && (
        <div className="assessment-entity-row-detail">
          <p>{row.records_sampled ?? 0} records sampled</p>
          {(row.duplicates?.key_field_duplicates > 0 || row.duplicates?.key_fields_checked?.length > 0) && (
            <p className="assessment-key-fields-note">
              Key duplicate check uses:{' '}
              <strong>
                {formatKeyFieldsLabel(row.duplicates?.key_fields_checked)
                  || 'columns matching email, name, phone, or id'}
              </strong>
            </p>
          )}
          {row.recommendations?.length > 0 && (
            <ul>
              {row.recommendations.slice(0, 4).map((rec, i) => (
                <li key={i}>{rec}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default function AssessmentDashboard({
  report,
  reports = [],
  onSelectReport,
  connectorType,
  connectorDisplayName,
}) {
  const [tab, setTab] = useState('snapshot');
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState(null);

  const sections = report?.sections || {};
  const metrics = useMemo(() => buildDashboardMetrics(report), [report]);
  const dataQuality = useMemo(() => resolveDataQualitySection(report), [report]);
  const gapGroups = useMemo(() => groupGapFindings(sections.data_gaps?.findings || []), [sections]);
  const labelMap = useMemo(() => buildEntityLabelMap(report?.entities), [report?.entities]);

  const risks = Array.isArray(sections.risks) ? sections.risks : sections.risks ? [sections.risks] : [];
  const summaryText = typeof sections.summary === 'string' ? sections.summary : '';

  const isEnterpriseGraph = connectorType === 'enterprise_graph';
  const connectedEnv = sections.connected_environment?.members || [];
  const crossLinks = sections.connected_environment?.cross_links || [];
  const connectorLabel = isEnterpriseGraph
    ? `${report?.scores?.sources_included ?? connectedEnv.length ?? '—'} sources`
    : connectorType === 'salesforce'
      ? 'Salesforce org'
      : connectorType === 'sql'
        ? 'Database'
        : 'Google Sheets';

  const generatedAt = report?.created_at
    ? new Date(report.created_at).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      })
    : '';

  const goTab = (id) => {
    setTab(id);
    const el = document.getElementById(`assessment-tab-${id}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const downloadPdf = async () => {
    if (!report?.id || pdfLoading) return;
    setPdfLoading(true);
    setPdfError(null);
    try {
      const params = connectorDisplayName
        ? { connector_display_name: connectorDisplayName }
        : undefined;
      const { data, headers } = await api.get(
        `/api/assessments/reports/${report.id}/pdf`,
        { responseType: 'blob', params },
      );
      const disposition = headers['content-disposition'] || '';
      const match = disposition.match(/filename="?([^";]+)"?/i);
      const fallbackName = connectorDisplayName
        ? `${connectorDisplayName.replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-')}-data-assessment.pdf`
        : 'data-assessment-report.pdf';
      const filename = match?.[1] || fallbackName;
      const url = window.URL.createObjectURL(new Blob([data], { type: 'application/pdf' }));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setPdfError('Failed to download PDF. Please try again.');
      console.error('Assessment PDF download failed', e);
    } finally {
      setPdfLoading(false);
    }
  };

  return (
    <div className="assessment-dashboard">
      <header className="assessment-dashboard-header">
        <div>
          <h3 className="assessment-dashboard-title">
            {isEnterpriseGraph ? 'Generated data assessment report' : 'Data assessment report'}
          </h3>
          {generatedAt && (
            <p className="assessment-dashboard-meta">Generated on {generatedAt}</p>
          )}
          {report?.is_partial && (
            <span className="assessment-badge assessment-badge--fair">Partial run</span>
          )}
        </div>
        {reports.length > 1 && (
          <label className="assessment-report-select-label">
            Report
            <select
              className="assessment-report-select"
              value={report?.id || ''}
              onChange={(e) => onSelectReport?.(e.target.value)}
            >
              {reports.map((r) => (
                <option key={r.id} value={r.id}>
                  {new Date(r.created_at).toLocaleString()}
                  {r.scores?.overall != null ? ` — ${r.scores.overall}/100` : ''}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="assessment-dashboard-header-actions">
          <button
            type="button"
            className="assessment-secondary-btn assessment-download-pdf-btn"
            onClick={downloadPdf}
            disabled={pdfLoading || !report?.id}
          >
            {pdfLoading ? 'Generating PDF…' : 'Download PDF'}
          </button>
        </div>
      </header>
      {pdfError && <p className="assessment-pdf-error">{pdfError}</p>}

      <nav className="assessment-dashboard-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`assessment-dashboard-tab${tab === t.id ? ' active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === 'snapshot' && (
        <div className="assessment-dashboard-snapshot" id="assessment-tab-snapshot">
          <div className="assessment-metrics-row">
            <article className="assessment-metric-card assessment-metric-card--score">
              <h4>Data readiness score</h4>
              <div className="assessment-metric-score-body">
                <div className="assessment-metric-score-ring-wrap">
                  <ScoreRing score={metrics.overall} />
                  <span className="assessment-metric-sub assessment-metric-sub--ring">/ 100</span>
                </div>
                <span
                  className={`assessment-badge assessment-badge--tier assessment-badge--${metrics.tier.className}`}
                >
                  {metrics.tier.label}
                </span>
              </div>
            </article>

            <article className="assessment-metric-card">
              <h4>{isEnterpriseGraph ? 'Sources included' : 'Connector'}</h4>
              <p className="assessment-metric-value">{connectorLabel}</p>
              <p className="assessment-metric-foot">
                {isEnterpriseGraph && connectedEnv.length > 0
                  ? connectedEnv.map((m) => m.display_name).filter(Boolean).join(' · ')
                  : `${metrics.entitiesAssessed} entities assessed`}
              </p>
            </article>

            <article className="assessment-metric-card">
              <h4>Entities with issues</h4>
              <p className="assessment-metric-value assessment-metric-value--alert">
                {metrics.problemEntities}
              </p>
              <p className="assessment-metric-foot">Of {metrics.entitiesAssessed} assessed</p>
              {metrics.problemEntities > 0 && (
                <span className="assessment-badge assessment-badge--alert">Review needed</span>
              )}
            </article>

            <article className="assessment-metric-card">
              <h4>Mapped in schema</h4>
              <p className="assessment-metric-value assessment-metric-value--primary">
                {metrics.mappedEntities}
              </p>
              <p className="assessment-metric-foot">{metrics.relationshipCount} relationships</p>
              {metrics.mappedEntities > 0 && metrics.problemEntities === 0 && (
                <span className="assessment-badge assessment-badge--good">Good coverage</span>
              )}
            </article>
          </div>

          <div className="assessment-preview-row">
            <article className="assessment-preview-card">
              <div className="assessment-preview-card-head">
                <h4>Summary</h4>
                <button type="button" className="assessment-link-btn" onClick={() => goTab('readiness')}>
                  View details →
                </button>
              </div>
              {summaryText ? (
                <p className="assessment-preview-summary">{summaryText}</p>
              ) : (
                <p className="assessment-dash-muted">No summary narrative for this run.</p>
              )}
              {risks.length > 0 && (
                <ul className="assessment-preview-risks">
                  {(risks.slice(0, 3)).map((r, i) => (
                    <li key={i}>{typeof r === 'string' ? r : r.message}</li>
                  ))}
                </ul>
              )}
            </article>

            <article className="assessment-preview-card">
              <div className="assessment-preview-card-head">
                <h4>Readiness</h4>
                <button type="button" className="assessment-link-btn" onClick={() => goTab('readiness')}>
                  View details →
                </button>
              </div>
              <DimensionBars dimensions={metrics.dimensions} compact />
            </article>

            <article className="assessment-preview-card">
              <div className="assessment-preview-card-head">
                <h4>Schema map</h4>
                <button type="button" className="assessment-link-btn" onClick={() => goTab('schema')}>
                  View details →
                </button>
              </div>
              <SchemaMapMini
                graph={
                  isEnterpriseGraph
                    ? sections.schema_map?.canonical_graph
                    : sections.schema_graph
                }
              />
            </article>
          </div>
        </div>
      )}

      {tab === 'readiness' && (
        <section className="assessment-dashboard-panel" id="assessment-tab-readiness">
          <article className="assessment-detail-card">
            <h4>Dimension scores</h4>
            <p className="assessment-overall-line">
              Overall readiness: <strong>{metrics.overall != null ? `${Math.round(metrics.overall)}/100` : '—'}</strong>
            </p>
            <DimensionBars dimensions={metrics.dimensions} />
          </article>
          {isEnterpriseGraph && connectedEnv.length > 0 && (
            <article className="assessment-detail-card">
              <h4>Connected environment</h4>
              <ConnectedEnvironmentView members={connectedEnv} crossLinks={crossLinks} />
            </article>
          )}
          {isEnterpriseGraph && (sections.readiness?.by_member || []).length > 0 && (
            <article className="assessment-detail-card">
              <h4>Readiness by source</h4>
              <ul className="assessment-bullet-list">
                {sections.readiness.by_member.map((m) => (
                  <li key={m.member_id}>
                    <strong>{m.display_name}</strong>
                    {m.overall != null ? ` — ${Math.round(m.overall)}/100` : ''}
                  </li>
                ))}
              </ul>
            </article>
          )}
          {summaryText && (
            <article className="assessment-detail-card">
              <h4>Summary</h4>
              <p>{summaryText}</p>
            </article>
          )}
          {risks.length > 0 && (
            <article className="assessment-detail-card">
              <h4>Risks</h4>
              <ul className="assessment-bullet-list">
                {risks.map((r, i) => (
                  <li key={i}>{typeof r === 'string' ? r : r.message || JSON.stringify(r)}</li>
                ))}
              </ul>
            </article>
          )}
        </section>
      )}

      {tab === 'quality' && (
        <section className="assessment-dashboard-panel" id="assessment-tab-quality">
          <article className="assessment-detail-card">
            <h4>Data quality totals</h4>
            {dataQuality.connector_totals && (
              <div className="assessment-quality-stats">
                <div className="assessment-quality-stat-item">
                  <span className="val">{dataQuality.connector_totals.exact_duplicates ?? 0}</span>
                  <span className="lbl">Exact duplicates</span>
                </div>
                <div className="assessment-quality-stat-item">
                  <span className="val">{dataQuality.connector_totals.key_field_duplicates ?? 0}</span>
                  <span className="lbl">Key duplicates</span>
                </div>
                <div className="assessment-quality-stat-item">
                  <span className="val">{dataQuality.connector_totals.total_missing ?? 0}</span>
                  <span className="lbl">Missing values</span>
                </div>
                <div className="assessment-quality-stat-item">
                  <span className="val">{dataQuality.connector_totals.format_issue_count ?? 0}</span>
                  <span className="lbl">Format issues</span>
                </div>
              </div>
            )}
            <div className="assessment-entity-list">
              {(dataQuality.by_entity || []).map((row) => (
                <EntityQualityRow key={row.entity_id} row={row} labelMap={labelMap} />
              ))}
            </div>
          </article>
        </section>
      )}

      {tab === 'schema' && (
        <section className="assessment-dashboard-panel" id="assessment-tab-schema">
          {isEnterpriseGraph ? (
            <EnterpriseCanonicalSchemaView
              schemaMap={sections.schema_map}
              members={connectedEnv}
            />
          ) : sections.schema_graph ? (
            <ConnectorSchemaGraphView graph={sections.schema_graph} />
          ) : (
            <article className="assessment-detail-card">
              <p className="assessment-dash-muted">
                No schema graph in this report. Enable connector metadata sync and re-run assessment.
              </p>
            </article>
          )}
        </section>
      )}

      {tab === 'gaps' && (
        <section className="assessment-dashboard-panel" id="assessment-tab-gaps">
          <article className="assessment-detail-card">
            <h4>Data gaps</h4>
            {gapGroups.length === 0 ? (
              <p className="assessment-dash-muted">No structural or metadata gaps flagged.</p>
            ) : (
              gapGroups.map(({ category, items }) => (
                <div className="assessment-gap-block" key={category}>
                  <h5>{category.replace(/_/g, ' ')}</h5>
                  <ul>
                    {items.map((g, i) => (
                      <li key={`${category}-${i}`}>
                        <span className={`assessment-gap-sev assessment-gap-sev--${g.severity || 'low'}`}>
                          {g.severity}
                        </span>
                        {g.entity_id && g.entity_id !== '*'
                          ? `${entityLabel(g.entity_id, labelMap)}: `
                          : ''}
                        {g.message}
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </article>
        </section>
      )}
    </div>
  );
}
