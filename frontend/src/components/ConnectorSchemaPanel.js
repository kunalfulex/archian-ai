import React, { useCallback, useEffect, useRef, useState } from 'react';
import api from '../api';
import { useUserTimezone } from '../hooks/useUserTimezone';
import { buildScheduleConfig, parseScheduleConfig } from '../utils/schemaMapLayout';
import ConnectorSchemaGraphView from './ConnectorSchemaGraphView';
import './ConnectorSchemaPanel.css';

const FREQUENCIES = [
  { value: '', label: 'Off' },
  { value: 'every_15_minutes', label: 'Every 15 minutes' },
  { value: 'hourly', label: 'Hourly' },
  { value: 'daily', label: 'Daily' },
  { value: 'yearly', label: 'Yearly' },
];

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function apiBase(connectorType, connectorId) {
  return `/api/connectors/${connectorType}/${connectorId}`;
}

export default function ConnectorSchemaPanel({
  connectorType,
  connectorId,
  embedded,
  showSyncControls = true,
  showManualSync,
  showScheduleAndStatus,
}) {
  const showManual = showManualSync ?? showSyncControls;
  const showScheduleStatus = showScheduleAndStatus ?? showSyncControls;
  const schemaSubtitle =
    connectorType === 'google_sheets'
      ? 'Auto-sync sheet headers, detect drift, and view tab relationships. Removed tabs are soft-deleted so drift stays visible.'
      : 'Auto-sync metadata, detect drift, and view object relationships. Removed objects are soft-deleted so drift stays visible.';
  const { userTimezone } = useUserTimezone();
  const [schedule, setSchedule] = useState(null);
  const [graph, setGraph] = useState(null);
  const [driftReport, setDriftReport] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [error, setError] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const pollRef = useRef(null);

  const [draftFreq, setDraftFreq] = useState('');
  const [timeOfDay, setTimeOfDay] = useState('02:00');
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [month, setMonth] = useState(1);
  const [yearlyDate, setYearlyDate] = useState('');

  const base = apiBase(connectorType, connectorId);

  const applyScheduleToForm = useCallback((data) => {
    const parsed = parseScheduleConfig(data);
    const freq = parsed.frequency === 'monthly' ? 'daily' : parsed.frequency;
    setDraftFreq(freq);
    setTimeOfDay(parsed.timeOfDay);
    setDayOfMonth(parsed.dayOfMonth);
    setMonth(parsed.month);
    const y = new Date().getFullYear();
    setYearlyDate(
      `${y}-${String(parsed.month).padStart(2, '0')}-${String(parsed.dayOfMonth).padStart(2, '0')}`,
    );
  }, []);

  const loadSchedule = useCallback(async () => {
    try {
      const { data } = await api.get(`${base}/schema-schedule`);
      setSchedule(data);
      applyScheduleToForm(data);
    } catch (e) {
      if (e.response?.status !== 503) {
        setError(e.response?.data?.detail || 'Failed to load schedule');
      }
    }
  }, [base, applyScheduleToForm]);

  const loadGraph = useCallback(async () => {
    try {
      const { data } = await api.get(`${base}/schema-graph`, { params: { scope: 'neighborhood' } });
      setGraph(data.graph);
    } catch (e) {
      if (e.response?.status !== 503) {
        console.error('Schema graph load failed', e);
      }
    }
  }, [base]);

  useEffect(() => {
    loadSchedule();
    loadGraph();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [loadSchedule, loadGraph]);

  useEffect(() => {
    if (draftFreq === 'yearly' && yearlyDate) {
      const [, m, d] = yearlyDate.split('-');
      if (m) setMonth(parseInt(m, 10));
      if (d) setDayOfMonth(parseInt(d, 10));
    }
  }, [yearlyDate, draftFreq]);

  const pollJob = (jobId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await api.get(`/api/schema-sync/jobs/${jobId}`);
        setJobStatus(data);
        if (data.status === 'completed') {
          clearInterval(pollRef.current);
          setSyncing(false);
          setDriftReport(data.drift_report);
          await loadGraph();
          await loadSchedule();
        } else if (data.status === 'failed') {
          clearInterval(pollRef.current);
          setSyncing(false);
          setError(data.error_summary || 'Schema sync failed');
        }
      } catch (e) {
        console.error(e);
      }
    }, 2000);
  };

  const runSync = async () => {
    setError(null);
    setSyncing(true);
    try {
      const { data } = await api.post(`${base}/schema-sync`, {
        sync_mode: 'schema_and_categorize',
        build_graph: true,
        graph_scope: 'neighborhood',
      });
      pollJob(data.job_id);
    } catch (e) {
      setSyncing(false);
      const detail = e.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : detail?.message || e.message);
    }
  };

  const saveSchedule = async () => {
    setError(null);
    setSavingSchedule(true);
    const enabled = Boolean(draftFreq);
    try {
      const scheduleConfig = enabled
        ? buildScheduleConfig({
            frequency: draftFreq,
            timeOfDay,
            dayOfMonth,
            month,
          })
        : {};

      const { data } = await api.put(`${base}/schema-schedule`, {
        schedule_enabled: enabled,
        schedule_frequency: enabled ? draftFreq : null,
        schedule_config: scheduleConfig,
        schedule_timezone: userTimezone || 'UTC',
        sync_mode: 'schema_and_categorize',
        graph_scope: 'neighborhood',
      });
      setSchedule((s) => ({
        ...s,
        ...data,
        schedule_enabled: enabled,
        schedule_frequency: draftFreq,
        schedule_config: scheduleConfig,
      }));
    } catch (e) {
      const detail = e.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : detail || 'Failed to save schedule');
    } finally {
      setSavingSchedule(false);
    }
  };

  const needsTime = draftFreq && !['hourly', 'every_15_minutes', ''].includes(draftFreq);
  const enabled = Boolean(schedule?.schedule_enabled);

  const graphSummary = graph?.summary;
  const nodeCount = graphSummary?.node_count ?? graph?.nodes?.length;
  const edgeCount = graphSummary?.edge_count ?? graph?.edges?.length;
  const hasStatusDetails =
    schedule?.last_success_at ||
    graphSummary?.as_of ||
    nodeCount != null ||
    edgeCount != null ||
    graphSummary?.scope;

  return (
    <div className={`connector-schema-panel${embedded ? ' connector-schema-panel--embedded' : ''}`}>
      {!embedded && <h3 className="connector-schema-title">Schema & relationships</h3>}
      <p className="connector-schema-subtitle">{schemaSubtitle}</p>

      {showManual || showScheduleStatus ? (
      <div className="connector-schema-control-block">
        <div
          className={`connector-schema-control-top${
            showManual && !showScheduleStatus ? ' connector-schema-control-top--manual-only' : ''
          }`}
        >
          {showManual ? (
          <div className="connector-schema-sync-col">
            <span className="connector-schema-col-label">Manual sync</span>
            <button
              type="button"
              className="schema-primary-btn"
              onClick={runSync}
              disabled={syncing}
            >
              {syncing ? 'Syncing…' : 'Sync schema now'}
            </button>
            {showManual && !showScheduleStatus && error ? (
              <p className="connector-schema-status-error">{error}</p>
            ) : null}
            {showManual && !showScheduleStatus && jobStatus?.progress && syncing ? (
              <p className="connector-schema-status-progress">
                {jobStatus.progress.message || jobStatus.status}
                {jobStatus.progress.percent != null ? ` (${jobStatus.progress.percent}%)` : ''}
              </p>
            ) : null}
          </div>
          ) : null}

          {showScheduleStatus ? (
          <div className="connector-schema-schedule-col">
            <span className="connector-schema-col-label">Auto-sync schedule</span>
            <div className="connector-schema-schedule-row">
            <label className="connector-schema-schedule-label">
              Frequency
              <select
                value={draftFreq}
                disabled={syncing || savingSchedule}
                onChange={(e) => setDraftFreq(e.target.value)}
              >
                {FREQUENCIES.map((f) => (
                  <option key={f.value || 'off'} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>

              {needsTime && (
                <label className="connector-schema-schedule-label">
                  Time of day
                  <input
                    type="time"
                    value={timeOfDay}
                    onChange={(e) => setTimeOfDay(e.target.value)}
                    disabled={savingSchedule}
                  />
                </label>
              )}

              {draftFreq === 'yearly' && (
                <>
                  <label className="connector-schema-schedule-label">
                    Date
                    <input
                      type="date"
                      value={yearlyDate}
                      onChange={(e) => setYearlyDate(e.target.value)}
                      disabled={savingSchedule}
                    />
                  </label>
                  <span className="connector-schema-schedule-hint connector-schema-schedule-hint--inline">
                    Runs each year on {MONTHS[month - 1]} {dayOfMonth} at {timeOfDay}
                  </span>
                </>
              )}

              <button
                type="button"
                className="schema-secondary-btn"
                onClick={saveSchedule}
                disabled={savingSchedule || syncing}
              >
                {savingSchedule ? 'Saving…' : 'Save schedule'}
              </button>
            </div>
            {draftFreq === 'hourly' && (
              <p className="connector-schema-schedule-hint">Runs at the start of each hour.</p>
            )}
            {draftFreq === 'every_15_minutes' && (
              <p className="connector-schema-schedule-hint">
                Runs every 15 minutes (schema-only recommended).
              </p>
            )}
          </div>
          ) : null}

          {showScheduleStatus ? (
          <div className="connector-schema-status-col">
            <span className="connector-schema-col-label">Sync status</span>
            <div className="connector-schema-status-body">
              {error && <p className="connector-schema-status-error">{error}</p>}
              {jobStatus?.progress && syncing && (
                <p className="connector-schema-status-progress">
                  {jobStatus.progress.message || jobStatus.status}
                  {jobStatus.progress.percent != null ? ` (${jobStatus.progress.percent}%)` : ''}
                </p>
              )}
              {hasStatusDetails ? (
                <ul className="connector-schema-status-list">
                  {schedule?.last_success_at && (
                    <li>
                      <span className="connector-schema-status-key">Last success</span>
                      <span className="connector-schema-status-val">
                        {new Date(schedule.last_success_at).toLocaleString()}
                      </span>
                    </li>
                  )}
                  {schedule?.next_run_at && enabled && (
                    <li>
                      <span className="connector-schema-status-key">Next</span>
                      <span className="connector-schema-status-val">
                        {new Date(schedule.next_run_at).toLocaleString()}
                      </span>
                    </li>
                  )}
                  {graphSummary?.as_of && (
                    <li>
                      <span className="connector-schema-status-key">As of</span>
                      <span className="connector-schema-status-val">
                        {new Date(graphSummary.as_of).toLocaleString()}
                      </span>
                    </li>
                  )}
                  {nodeCount != null && (
                    <li>
                      <span className="connector-schema-status-stat">{nodeCount} objects</span>
                    </li>
                  )}
                  {edgeCount != null && (
                    <li>
                      <span className="connector-schema-status-stat">{edgeCount} relationships</span>
                    </li>
                  )}
                  {graphSummary?.scope && (
                    <li>
                      <span className="connector-schema-status-scope">Scope: {graphSummary.scope}</span>
                    </li>
                  )}
                </ul>
              ) : (
                !error &&
                !(jobStatus?.progress && syncing) && (
                  <p className="connector-schema-status-empty">Run a schema sync to see status.</p>
                )
              )}
            </div>
          </div>
          ) : null}
        </div>
      </div>
      ) : null}

      <ConnectorSchemaGraphView graph={graph} driftReport={driftReport} hideSummary />
    </div>
  );
}
