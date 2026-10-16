import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FiPlay } from 'react-icons/fi';
import api from '../api';
import { streamEnterpriseGraphRun } from '../assessment/assessmentSse';
import AssessmentDashboard from './AssessmentDashboard';
import EnterpriseGraphResultsTabs from './EnterpriseGraphResultsTabs';
import {
  effectiveGraphPhase,
  graphPhaseIndex,
  graphRollupDetail,
  graphRunSteps,
  isConnectorAssessmentPhase,
  memberProgressDetail,
  memberProgressPercent,
} from './enterpriseGraphProgress';
import '../EnterpriseGraphsPage.css';

const DEPTH_OPTIONS = [
  { value: 'quick', label: 'Quick scan', hint: 'High-level metrics on core entities per connector.' },
  { value: 'partial', label: 'Partial assessment', hint: 'Standard depth — recommended for most graphs.' },
  { value: 'full', label: 'Full assessment', hint: 'All assessable entities (may take longer).' },
];

function RunProgress({ run }) {
  const progress = run?.progress || {};
  const memberJobs = run?.member_jobs || [];
  const pct = progress.percent ?? 0;
  const currentPhase = effectiveGraphPhase(progress, memberJobs);
  const activePhaseIdx = graphPhaseIndex(currentPhase);
  const inRollup =
    currentPhase === 'linking'
    && (progress.phase === 'rollup' || progress.phase === 'assessment')
    && memberJobs.every((m) => {
      const st = (m.display_status || m.status || '').toLowerCase();
      return st === 'completed' || st === 'failed';
    });
  const showConnectorList = isConnectorAssessmentPhase(progress.phase);
  const rollupDetail = graphRollupDetail(progress);
  const completedCount = memberJobs.filter((m) => {
    const st = (m.display_status || m.status || '').toLowerCase();
    return st === 'completed';
  }).length;

  return (
    <div className="eg-run-progress">
      <ol className="eg-run-phase-steps" aria-label="Assessment pipeline">
        {graphRunSteps().map((phase, idx) => {
          const isActive = idx === activePhaseIdx;
          const isDone = idx < activePhaseIdx;
          return (
            <li
              key={phase.id}
              className={`eg-run-phase-step${isDone ? ' eg-run-phase-step--done' : ''}${
                isActive ? ' eg-run-phase-step--active' : ''
              }`}
            >
              {phase.label}
            </li>
          );
        })}
      </ol>

      <div
        className={`eg-run-progress-bar-wrap${
          !showConnectorList && pct >= 90 && pct < 100 ? ' eg-run-progress-bar-wrap--pulse' : ''
        }`}
      >
        <div className="eg-run-progress-bar" style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      <p className="eg-run-progress-msg">
        {inRollup && !progress.message?.includes('cross-connector')
          ? 'All connectors finished — starting cross-connector mapping…'
          : progress.message || 'Running assessment…'}{' '}
        ({pct}%)
      </p>
      {rollupDetail && !showConnectorList && (
        <p className="eg-run-progress-detail">{rollupDetail}</p>
      )}
      {!showConnectorList && memberJobs.length > 0 && (
        <p className="eg-run-connectors-done">
          {completedCount} of {memberJobs.length} connectors assessed
        </p>
      )}

      {showConnectorList && memberJobs.length > 0 && (
        <ul className="eg-run-member-status">
          {memberJobs.map((mj) => {
            const displayStatus = mj.display_status || mj.status || 'pending';
            const detail = memberProgressDetail(mj);
            const memberPct = memberProgressPercent(mj);
            const showMemberBar =
              displayStatus === 'running' || displayStatus === 'queued';

            return (
              <li key={mj.member_id}>
                <div className="eg-run-member-row">
                  <span className="eg-run-member-label">
                    <span className="eg-run-member-name">
                      {mj.display_name || mj.member_id}
                    </span>
                    {detail ? (
                      <span className="eg-run-member-detail">{detail}</span>
                    ) : null}
                  </span>
                  <span className={`eg-run-status eg-run-status--${displayStatus}`}>
                    {displayStatus}
                  </span>
                </div>
                {showMemberBar && (
                  <div className="eg-run-member-bar-wrap" aria-hidden>
                    <div
                      className="eg-run-member-bar"
                      style={{ width: `${memberPct}%` }}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default function EnterpriseGraphAssessment({
  graphId,
  memberCount,
  lakeEnabled = false,
  assessmentStale = false,
  onGraphRefresh,
}) {
  const [depth, setDepth] = useState('partial');
  const [run, setRun] = useState(null);
  const [report, setReport] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [proposalRefreshKey, setProposalRefreshKey] = useState(0);
  const streamAbortRef = useRef(null);

  const loadLatestReport = useCallback(async () => {
    try {
      const { data } = await api.get(`/api/enterprise-graphs/${graphId}/report/latest`);
      if (data?.report) setReport(data.report);
    } catch {
      /* no report yet */
    }
  }, [graphId]);

  const stopRunStream = useCallback(() => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort();
      streamAbortRef.current = null;
    }
  }, []);

  const streamRun = useCallback(
    (runId) => {
      stopRunStream();
      const controller = new AbortController();
      streamAbortRef.current = controller;

      streamEnterpriseGraphRun({
        runId,
        signal: controller.signal,
        onProgress: (evt) => {
          if (evt.run) setRun(evt.run);
        },
        onFinished: async (evt) => {
          if (streamAbortRef.current === controller) {
            streamAbortRef.current = null;
          }
          const r = evt.run;
          if (!r) {
            setRunning(false);
            setError(evt.error || 'Run not found');
            return;
          }
          setRun(r);
          setRunning(false);
          if (r.status === 'completed') {
            await loadLatestReport();
            setProposalRefreshKey((k) => k + 1);
            if (onGraphRefresh) await onGraphRefresh();
          } else if (r.status === 'failed' || r.status === 'cancelled') {
            setError(r.error_summary || `Run ${r.status}`);
          }
        },
      }).catch((e) => {
        if (e.name === 'AbortError') return;
        setRunning(false);
        setError(e.message || 'Failed to stream run progress');
      });
    },
    [loadLatestReport, onGraphRefresh, stopRunStream],
  );

  const resumeActiveRun = useCallback(async () => {
    try {
      const { data } = await api.get(`/api/enterprise-graphs/${graphId}/runs/active`);
      const r = data.run;
      if (!r?.id) return;
      const st = r.status;
      if (!['queued', 'running', 'paused'].includes(st)) return;
      setRun(r);
      setRunning(true);
      streamRun(r.id);
    } catch (e) {
      console.error('Failed to resume active enterprise graph run', e);
    }
  }, [graphId, streamRun]);

  useEffect(() => {
    loadLatestReport();
    resumeActiveRun();
    return () => {
      if (streamAbortRef.current) {
        streamAbortRef.current.abort();
        streamAbortRef.current = null;
      }
    };
  }, [loadLatestReport, resumeActiveRun]);

  const startRun = async () => {
    setError(null);
    setRunning(true);
    setReport(null);
    try {
      const { data } = await api.post(`/api/enterprise-graphs/${graphId}/runs`, { depth });
      const runId = data.run_id;
      if (!runId) throw new Error('No run id returned');
      const statusRes = await api.get(`/api/enterprise-graphs/runs/${runId}`);
      setRun(statusRes.data.run);
      streamRun(runId);
    } catch (e) {
      const detail = e.response?.data?.detail;
      const existingRunId =
        typeof detail === 'object' && detail?.run_id ? detail.run_id : null;
      if (e.response?.status === 409 && existingRunId) {
        setRunning(true);
        setError(null);
        const statusRes = await api.get(`/api/enterprise-graphs/runs/${existingRunId}`);
        setRun(statusRes.data.run);
        streamRun(existingRunId);
        return;
      }
      setRunning(false);
      setError(
        typeof detail === 'string'
          ? detail
          : detail?.message || e.response?.data?.message || e.message || 'Failed to start run',
      );
    }
  };

  const runStatus = run?.status;
  const isActiveRun =
    running && run?.id && ['queued', 'running', 'paused'].includes(runStatus);
  const canRun = memberCount >= 2 && !isActiveRun;

  const refreshRun = async (runId) => {
    const { data } = await api.get(`/api/enterprise-graphs/runs/${runId}`);
    setRun(data.run);
    return data.run;
  };

  const cancelRun = async () => {
    if (!run?.id) return;
    try {
      await api.post(`/api/enterprise-graphs/runs/${run.id}/cancel`);
      stopRunStream();
      setRunning(false);
      await refreshRun(run.id);
      setError('Assessment cancelled');
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to cancel');
    }
  };

  const pauseRun = async () => {
    if (!run?.id) return;
    try {
      await api.post(`/api/enterprise-graphs/runs/${run.id}/pause`);
      await refreshRun(run.id);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to pause');
    }
  };

  const resumeRun = async () => {
    if (!run?.id) return;
    try {
      await api.post(`/api/enterprise-graphs/runs/${run.id}/resume`);
      await refreshRun(run.id);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to resume');
    }
  };

  return (
    <div className="eg-assessment-block">
      <div className="eg-wizard-card eg-run-card">
        <h2>Data assessment</h2>
        <p className="eg-wizard-hint">
          Run assessment on all {memberCount} connectors in this graph. Results roll up into one report.
        </p>

        <div className="eg-depth-options">
          {DEPTH_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`eg-depth-option${depth === opt.value ? ' eg-depth-option--selected' : ''}`}
            >
              <input
                type="radio"
                name="eg-depth"
                value={opt.value}
                checked={depth === opt.value}
                disabled={isActiveRun}
                onChange={() => setDepth(opt.value)}
              />
              <div>
                <strong>{opt.label}</strong>
                <span>{opt.hint}</span>
              </div>
            </label>
          ))}
        </div>

        {error && <div className="eg-error">{error}</div>}

        {isActiveRun && run && <RunProgress run={run} />}

        <div className="eg-run-actions">
          <button
            type="button"
            className="eg-primary-btn"
            disabled={!canRun}
            onClick={startRun}
          >
            <FiPlay aria-hidden />
            {isActiveRun
              ? runStatus === 'paused'
                ? 'Assessment paused'
                : 'Assessment running…'
              : 'Run data assessment'}
          </button>
          {runStatus === 'running' && (
            <>
              <button type="button" className="eg-secondary-btn" onClick={pauseRun}>
                Pause
              </button>
              <button type="button" className="eg-secondary-btn" onClick={cancelRun}>
                Cancel
              </button>
            </>
          )}
          {runStatus === 'paused' && (
            <>
              <button type="button" className="eg-primary-btn" onClick={resumeRun}>
                Resume
              </button>
              <button type="button" className="eg-secondary-btn" onClick={cancelRun}>
                Cancel
              </button>
            </>
          )}
          {runStatus === 'queued' && (
            <button type="button" className="eg-secondary-btn" onClick={cancelRun}>
              Cancel
            </button>
          )}
        </div>
        {memberCount < 2 && (
          <p className="eg-wizard-hint">Add at least two connectors to run an assessment.</p>
        )}
      </div>

      {assessmentStale && !isActiveRun && (
        <p className="eg-wizard-hint eg-stale-results-hint">
          Lake, warehouse, and mapping tabs are hidden until you run a new assessment.
        </p>
      )}

      {report && !isActiveRun && !assessmentStale && (
        <>
          <EnterpriseGraphResultsTabs
            graphId={graphId}
            lakeEnabled={lakeEnabled}
            proposalRefreshKey={proposalRefreshKey}
            onMappingsChanged={loadLatestReport}
          />
          <div className="eg-report-wrap">
            <AssessmentDashboard report={report} connectorType="enterprise_graph" />
          </div>
        </>
      )}
    </div>
  );
}
