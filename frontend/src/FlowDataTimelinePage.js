import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { FiArrowLeft, FiBarChart2, FiSearch } from 'react-icons/fi';
import api from './api';
import './FlowsPage.css';
import './FlowDataTimelinePage.css';

function formatAxiosError(err) {
  const d = err?.response?.data;
  if (d?.detail != null) {
    return typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail);
  }
  if (d?.message) return d.message;
  return err?.message || 'Request failed';
}

function formatDateTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function statusBadgeClass(status) {
  const s = (status || '').toLowerCase();
  if (s === 'succeeded' || s === 'completed' || s === 'success') {
    return 'flow-timeline-status flow-timeline-status--success';
  }
  if (s === 'failed' || s === 'error') {
    return 'flow-timeline-status flow-timeline-status--failed';
  }
  if (s === 'running' || s === 'in_progress' || s === 'pending') {
    return 'flow-timeline-status flow-timeline-status--running';
  }
  return 'flow-timeline-status flow-timeline-status--neutral';
}

/** Omit extraction deep-link when server reports zero extracted rows (unknown/null count keeps link for older rows). */
function showExtractionResultLink(row) {
  if (row.extraction_result_id == null) return false;
  const n = row.extraction_record_count;
  if (n === 0 || n === '0') return false;
  return true;
}

export default function FlowDataTimelinePage() {
  const { flowId } = useParams();
  const navigate = useNavigate();
  const [flow, setFlow] = useState(null);
  const [executions, setExecutions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');

  const load = useCallback(async () => {
    if (!flowId) return;
    setLoading(true);
    setError(null);
    try {
      const flowRes = await api.get(`/flow/${flowId}`);
      if (flowRes.data?.status !== 'success') {
        throw new Error(flowRes.data?.message || 'Failed to load flow');
      }
      setFlow(flowRes.data.flow);

      const exRes = await api.get(`/flow/${flowId}/executions`, { params: { limit: 200 } });
      const rows = Array.isArray(exRes.data?.executions) ? exRes.data.executions : [];
      setExecutions(rows);
    } catch (e) {
      setError(formatAxiosError(e));
    } finally {
      setLoading(false);
    }
  }, [flowId]);

  useEffect(() => {
    load();
  }, [load]);

  const filteredExecutions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return executions;
    return executions.filter((row) => {
      const hay = [
        row.trigger_type,
        row.status,
        row.sync_outcome,
        row.extraction_record_count != null ? String(row.extraction_record_count) : '',
        row.started_at,
        row.completed_at,
        row.extraction_result_id != null ? String(row.extraction_result_id) : '',
        row.migration_batch_id != null ? String(row.migration_batch_id) : '',
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [executions, searchQuery]);

  const runCount = executions.length;
  const filteredCount = filteredExecutions.length;

  return (
    <div className="flows-container flow-detail-page flow-full-width-tables">
      <div className="flow-detail-toolbar">
        <button type="button" className="flow-detail-back" onClick={() => navigate(`/flows/${flowId}`)}>
          <FiArrowLeft /> Back to flow
        </button>
      </div>

      <div className="flows-header flow-detail-header">
        <h2 className="flow-detail-page-title">
          Data timeline
          {flow?.migration_name ? (
            <>
              : <span className="flow-name-highlight">{flow.migration_name}</span>
            </>
          ) : null}
        </h2>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="flow-detail-body">
        {loading ? (
          <div className="loading-state">Loading run history…</div>
        ) : (
          <div className="flow-timeline-preview-card">
            <div className="flow-timeline-card-header">
              <div className="flow-timeline-card-title">
                <FiBarChart2 className="flow-timeline-card-title-icon" aria-hidden />
                <span>
                  Run history
                  <span className="flow-timeline-card-count">
                    {' '}
                    ({runCount} {runCount === 1 ? 'run' : 'runs'})
                  </span>
                </span>
              </div>
            </div>
            <div className="flow-timeline-card-body">
              <div className="flow-timeline-toolbar">
                <FiSearch className="flow-timeline-search-icon" aria-hidden />
                <input
                  type="search"
                  className="flow-timeline-search-input"
                  placeholder="Filter by trigger, status, time, or IDs…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  aria-label="Filter runs"
                />
              </div>
              {runCount === 0 ? (
                <div className="flow-timeline-empty">No runs recorded for this flow yet.</div>
              ) : filteredCount === 0 ? (
                <div className="flow-timeline-empty">No runs match your search.</div>
              ) : (
                <div className="flow-timeline-table-wrapper">
                  <div className="flow-timeline-table-scroll">
                    <table className="flow-timeline-table">
                      <thead>
                        <tr>
                          <th>Trigger</th>
                          <th>Started</th>
                          <th>Completed</th>
                          <th>Status</th>
                          <th>Results</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredExecutions.map((row) => {
                          const extractionOk = showExtractionResultLink(row);
                          const hasMigration = row.migration_batch_id != null;
                          return (
                          <tr key={row.id}>
                            <td>{row.trigger_type || '—'}</td>
                            <td>{formatDateTime(row.started_at)}</td>
                            <td>{formatDateTime(row.completed_at)}</td>
                            <td>
                              <span className={statusBadgeClass(row.status)}>{row.status || '—'}</span>
                            </td>
                            <td>
                              {row.sync_outcome === 'no_changes' ? (
                                <>
                                  <span className="flow-timeline-no-change" title="No new or updated source rows in this run">
                                    No change
                                  </span>
                                  {extractionOk ? (
                                    <>
                                      <span> · </span>
                                      <Link
                                        className="view-link"
                                        to={`/flows/${flowId}/extraction/${row.extraction_result_id}`}
                                      >
                                        Extraction
                                      </Link>
                                    </>
                                  ) : null}
                                </>
                              ) : (
                                <>
                                  {extractionOk ? (
                                    <Link
                                      className="view-link"
                                      to={`/flows/${flowId}/extraction/${row.extraction_result_id}`}
                                    >
                                      Extraction
                                    </Link>
                                  ) : null}
                                  {extractionOk && hasMigration ? <span> · </span> : null}
                                  {hasMigration ? (
                                    <Link
                                      className="view-link"
                                      to={`/flows/${flowId}/migration/${row.migration_batch_id}`}
                                    >
                                      Migration
                                    </Link>
                                  ) : null}
                                  {!extractionOk && !hasMigration ? '—' : null}
                                </>
                              )}
                            </td>
                          </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
