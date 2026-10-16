import React, { useState, useEffect, useCallback } from 'react';
import api from '../api';
import './SuperAdminLearningTab.css';

const DAYS_OPTIONS = [
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
];

const FUNNEL_LABELS = {
  plan_proposed: 'Plans proposed',
  plan_approved: 'Plans approved',
  plan_rejected: 'Plans rejected',
  mapping_template: 'Mapping templates',
  migration_outcome: 'Migration OK (≥95%)',
  migration_partial: 'Migration partial',
  migration_failed: 'Migration failed',
  recovery_attempt: 'Recovery offered',
  recovery_approved: 'Recovery approved',
  recovery_dismissed: 'Recovery dismissed',
  recovery_success: 'Recovery success',
};

function SuperAdminLearningTab() {
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [retrieval, setRetrieval] = useState(null);
  const [effectiveness, setEffectiveness] = useState(null);
  const [toolAudit, setToolAudit] = useState(null);
  const [chunks, setChunks] = useState(null);
  const [userIdFilter, setUserIdFilter] = useState('');
  const [timelineRunId, setTimelineRunId] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [timelineLoading, setTimelineLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { days };
      const chunkParams = { limit: 25, offset: 0 };
      if (userIdFilter.trim()) {
        params.user_id = userIdFilter.trim();
        chunkParams.user_id = userIdFilter.trim();
      }
      const [summaryRes, funnelRes, retrievalRes, effectivenessRes, toolAuditRes, chunksRes] = await Promise.all([
        api.get('/admin/learning/summary', { params }),
        api.get('/admin/learning/funnel', { params }),
        api.get('/admin/learning/retrieval', { params }),
        api.get('/admin/learning/effectiveness', { params }),
        api.get('/admin/learning/tool-audit', { params }),
        api.get('/admin/learning/chunks', { params: chunkParams }),
      ]);
      setSummary(summaryRes.data || null);
      setFunnel(funnelRes.data || null);
      setRetrieval(retrievalRes.data || null);
      setEffectiveness(effectivenessRes.data || null);
      setToolAudit(toolAuditRes.data || null);
      setChunks(chunksRes.data || null);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to load learning metrics');
    } finally {
      setLoading(false);
    }
  }, [days, userIdFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const openTimeline = async (runId) => {
    if (!runId) return;
    setTimelineRunId(runId);
    setTimeline(null);
    setTimelineLoading(true);
    try {
      const res = await api.get(`/admin/learning/runs/${runId}/timeline`);
      setTimeline(res.data || null);
    } catch (e) {
      setTimeline({ error: e.response?.data?.detail || e.message || 'Failed to load timeline' });
    } finally {
      setTimelineLoading(false);
    }
  };

  const closeTimeline = () => {
    setTimelineRunId(null);
    setTimeline(null);
  };

  if (loading && !summary) {
    return <p className="super-admin-learning-loading">Loading learning metrics…</p>;
  }

  return (
    <div className="super-admin-learning">
      <div className="super-admin-learning-toolbar">
        <label>
          Period
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {DAYS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          User ID (optional)
          <input
            type="text"
            value={userIdFilter}
            onChange={(e) => setUserIdFilter(e.target.value)}
            placeholder="Filter by user UUID"
          />
        </label>
        <button type="button" className="super-admin-btn" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      {error ? <p className="super-admin-error">{error}</p> : null}

      {summary ? (
        <section className="super-admin-learning-section">
          <h3>Corpus overview {userIdFilter.trim() ? '(user)' : '(platform)'}</h3>
          <div className="super-admin-learning-cards">
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">{summary.total_chunks ?? 0}</span>
              <span className="super-admin-learning-card-label">Total chunks</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">{summary.chunks_in_period ?? 0}</span>
              <span className="super-admin-learning-card-label">New in period</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {summary.chunks_missing_embedding ?? 0}
              </span>
              <span className="super-admin-learning-card-label">Missing embeddings</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {summary.active_pipeline_runs ?? 0}
              </span>
              <span className="super-admin-learning-card-label">Active runs</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {summary.retrievals_in_period ?? 0}
              </span>
              <span className="super-admin-learning-card-label">RAG retrievals</span>
            </div>
            {summary.label_evidence_pending != null ? (
              <div className="super-admin-learning-card">
                <span className="super-admin-learning-card-value">
                  {summary.label_evidence_pending}
                </span>
                <span className="super-admin-learning-card-label">Label evidence pending</span>
              </div>
            ) : null}
          </div>
        </section>
      ) : null}

      {funnel?.funnel_counts ? (
        <section className="super-admin-learning-section">
          <h3>Learning funnel</h3>
          <table className="super-admin-table super-admin-learning-table">
            <thead>
              <tr>
                <th>Event / chunk type</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(funnel.funnel_counts).map(([key, count]) => (
                <tr key={key}>
                  <td>{FUNNEL_LABELS[key] || key}</td>
                  <td>{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {funnel?.by_stage ? (
        <section className="super-admin-learning-section">
          <h3>Chunks by stage</h3>
          {Object.entries(funnel.by_stage).map(([stage, items]) => (
            <div key={stage} className="super-admin-learning-stage-block">
              <h4>{stage}</h4>
              <ul>
                {(items || []).map((item) => (
                  <li key={item.chunk_type}>
                    <code>{item.chunk_type}</code>: {item.count}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      ) : null}

      {retrieval?.by_caller?.length ? (
        <section className="super-admin-learning-section">
          <h3>RAG retrieval health</h3>
          <table className="super-admin-table super-admin-learning-table">
            <thead>
              <tr>
                <th>Caller</th>
                <th>Retrievals</th>
                <th>Avg results</th>
                <th>Avg top score</th>
              </tr>
            </thead>
            <tbody>
              {retrieval.by_caller.map((row) => (
                <tr key={row.caller}>
                  <td>
                    <code>{row.caller}</code>
                  </td>
                  <td>{row.retrieval_count}</td>
                  <td>{row.avg_result_count != null ? row.avg_result_count.toFixed(2) : '—'}</td>
                  <td>{row.avg_top_score != null ? row.avg_top_score.toFixed(3) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {retrieval?.top_retrieved_chunk_types?.length ? (
        <section className="super-admin-learning-section">
          <h3>Most retrieved chunk types</h3>
          <ul className="super-admin-learning-type-list">
            {retrieval.top_retrieved_chunk_types.map((row) => (
              <li key={row.chunk_type}>
                <code>{row.chunk_type}</code> — {row.hit_count} hits
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {effectiveness ? (
        <section className="super-admin-learning-section">
          <h3>Retrieval effectiveness (agent runtime)</h3>
            <div className="super-admin-learning-cards">
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.trace_replay?.hit_rate != null
                  ? `${(effectiveness.trace_replay.hit_rate * 100).toFixed(1)}%`
                  : '—'}
              </span>
              <span className="super-admin-learning-card-label">Trace replay hit rate</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.trace_replay?.hits ?? 0}
                <span className="super-admin-learning-card-sub">
                  {' '}
                  / {effectiveness.trace_replay?.attempts ?? 0}
                </span>
              </span>
              <span className="super-admin-learning-card-label">Replay hits / attempts</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.trace_replay?.llm_pick_estimate ?? '—'}
              </span>
              <span className="super-admin-learning-card-label">Est. LLM picks (period)</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.agent_retrieval?.hit_rate != null
                  ? `${(effectiveness.agent_retrieval.hit_rate * 100).toFixed(1)}%`
                  : '—'}
              </span>
              <span className="super-admin-learning-card-label">Agent RAG hit rate</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.tool_pick_shadow?.first_tool_agreement_rate != null
                  ? `${(effectiveness.tool_pick_shadow.first_tool_agreement_rate * 100).toFixed(1)}%`
                  : '—'}
              </span>
              <span className="super-admin-learning-card-label">Shadow first-tool match</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.migration_success_lift_delta != null
                  ? `${(effectiveness.migration_success_lift_delta * 100).toFixed(1)} pts`
                  : '—'}
              </span>
              <span className="super-admin-learning-card-label">Migration lift (with RAG)</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.tool_traces_captured ?? 0}
              </span>
              <span className="super-admin-learning-card-label">Tool traces captured</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.trace_retrieval?.platform_patterns_captured ?? 0}
              </span>
              <span className="super-admin-learning-card-label">Platform patterns</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                <code>{effectiveness.trace_retrieval?.policy ?? '—'}</code>
              </span>
              <span className="super-admin-learning-card-label">Retrieval policy</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {effectiveness.trace_retrieval?.platform_retrieval?.hit_rate != null
                  ? `${(effectiveness.trace_retrieval.platform_retrieval.hit_rate * 100).toFixed(1)}%`
                  : '—'}
              </span>
              <span className="super-admin-learning-card-label">Platform RAG hit rate</span>
            </div>
          </div>
          {effectiveness.migration_lift &&
          Object.keys(effectiveness.migration_lift).length > 0 ? (
            <table className="super-admin-table super-admin-learning-table">
              <thead>
                <tr>
                  <th>Migration bucket</th>
                  <th>Count</th>
                  <th>Avg success rate</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(effectiveness.migration_lift).map(([bucket, row]) => (
                  <tr key={bucket}>
                    <td>{bucket.replace(/_/g, ' ')}</td>
                    <td>{row.count ?? 0}</td>
                    <td>
                      {row.avg_success_rate != null
                        ? `${(row.avg_success_rate * 100).toFixed(1)}%`
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>
      ) : null}

      {toolAudit ? (
        <section className="super-admin-learning-section">
          <h3>Tool usage audit</h3>
          <div className="super-admin-learning-cards">
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {toolAudit.registered_count ?? 0}
              </span>
              <span className="super-admin-learning-card-label">Registered tools</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {toolAudit.never_picked_count ?? 0}
              </span>
              <span className="super-admin-learning-card-label">Never picked</span>
            </div>
            <div className="super-admin-learning-card">
              <span className="super-admin-learning-card-value">
                {toolAudit.low_usage_count ?? 0}
              </span>
              <span className="super-admin-learning-card-label">Low usage (&lt;3)</span>
            </div>
          </div>
          {toolAudit.never_picked?.length ? (
            <>
              <h4>Deprecation candidates (registered, zero picks)</h4>
              <ul className="super-admin-learning-list">
                {toolAudit.never_picked.map((tid) => (
                  <li key={tid}>
                    <code>{tid}</code>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="super-admin-learning-muted">All registered tools have been picked at least once.</p>
          )}
          {toolAudit.active_tools?.length ? (
            <>
              <h4>Most active tools</h4>
              <table className="super-admin-table super-admin-learning-table">
                <thead>
                  <tr>
                    <th>Tool</th>
                    <th>Usage count</th>
                  </tr>
                </thead>
                <tbody>
                  {toolAudit.active_tools.slice(0, 10).map((row) => (
                    <tr key={row.tool_id}>
                      <td>
                        <code>{row.tool_id}</code>
                      </td>
                      <td>{row.usage_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </section>
      ) : null}

      {chunks?.items?.length ? (
        <section className="super-admin-learning-section">
          <h3>Recent learning chunks</h3>
          <table className="super-admin-table super-admin-learning-table">
            <thead>
              <tr>
                <th>Stage</th>
                <th>Type</th>
                <th>Preview</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {chunks.items.map((row) => (
                <tr key={row.id}>
                  <td>{row.stage}</td>
                  <td>
                    <code>{row.chunk_type}</code>
                  </td>
                  <td className="super-admin-learning-preview">
                    {(row.content_preview || '').slice(0, 80)}
                    {(row.content_preview || '').length > 80 ? '…' : ''}
                  </td>
                  <td>
                    {row.pipeline_run_id ? (
                      <button
                        type="button"
                        className="super-admin-link-btn"
                        onClick={() => openTimeline(row.pipeline_run_id)}
                      >
                        Timeline
                      </button>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {timelineRunId ? (
        <div className="super-admin-learning-modal-backdrop" role="presentation" onClick={closeTimeline}>
          <div
            className="super-admin-learning-modal"
            role="dialog"
            aria-labelledby="learning-timeline-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="super-admin-learning-modal-header">
              <h3 id="learning-timeline-title">Pipeline run timeline</h3>
              <button type="button" className="super-admin-btn" onClick={closeTimeline}>
                Close
              </button>
            </div>
            {timelineLoading ? (
              <p className="super-admin-learning-loading">Loading timeline…</p>
            ) : timeline?.error ? (
              <p className="super-admin-error">{timeline.error}</p>
            ) : timeline?.run ? (
              <div className="super-admin-learning-timeline">
                <p>
                  <strong>Run</strong> <code>{timeline.run.id}</code> — {timeline.run.status}
                  {timeline.run.source_type ? ` · ${timeline.run.source_type}` : ''}
                  {timeline.run.destination_type ? ` → ${timeline.run.destination_type}` : ''}
                </p>
                {timeline.chunks?.length ? (
                  <div className="super-admin-learning-timeline-block">
                    <h4>Chunks ({timeline.chunks.length})</h4>
                    <ul>
                      {timeline.chunks.map((c) => (
                        <li key={c.id}>
                          <code>{c.stage}/{c.chunk_type}</code>
                          {c.content_preview ? ` — ${c.content_preview.slice(0, 100)}` : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {timeline.corrections?.length ? (
                  <div className="super-admin-learning-timeline-block">
                    <h4>Corrections ({timeline.corrections.length})</h4>
                    <ul>
                      {timeline.corrections.map((c) => (
                        <li key={c.id}>
                          <code>{c.stage}</code> v{c.stage_version}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {timeline.retrievals?.length ? (
                  <div className="super-admin-learning-timeline-block">
                    <h4>Retrievals ({timeline.retrievals.length})</h4>
                    <ul>
                      {timeline.retrievals.map((r) => (
                        <li key={r.id}>
                          <code>{r.caller}</code> — {r.result_count} results
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default SuperAdminLearningTab;
