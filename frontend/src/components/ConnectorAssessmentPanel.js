import React, { useCallback, useEffect, useRef, useState } from 'react';
import api from '../api';
import { streamAssessmentJob } from '../assessment/assessmentSse';
import AssessmentEntityMultiSelect from './AssessmentEntityMultiSelect';
import AssessmentDashboard from './AssessmentDashboard';
import './ConnectorAssessmentPanel.css';

/** Stable default — avoids re-fetch loop from `availableEntities = []` on every render. */
const EMPTY_ENTITIES = [];

/** In-memory cache: one DB-backed entity list per connector. */
const entityOptionsCache = new Map();

function normalizeParentEntities(list) {
  return (list || []).map((name) => ({
    value: typeof name === 'string' ? name : name.value,
    label: typeof name === 'string' ? name : name.label || name.value,
  }));
}

/**
 * Data assessment UI for connector detail pages.
 *
 * @param {object} props
 * @param {'salesforce'|'sql'|'google_sheets'} props.connectorType
 * @param {string} props.connectorId - salesforce_tokens.id, sql connector id, or gs connection id
 * @param {string[]} [props.availableEntities] - optional object/table names for standard scope
 */
export default function ConnectorAssessmentPanel({
  connectorType,
  connectorId,
  availableEntities: availableEntitiesProp,
  connectorDisplayName,
  embedded = false,
  onJobActivityChange,
}) {
  const availableEntities = availableEntitiesProp ?? EMPTY_ENTITIES;
  const parentEntityCount = availableEntities.length;
  const [scope, setScope] = useState('quick');
  const [selectedEntities, setSelectedEntities] = useState([]);
  const [entityOptions, setEntityOptions] = useState([]);
  const [entitiesLoading, setEntitiesLoading] = useState(false);
  const [entitiesLoadError, setEntitiesLoadError] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [reports, setReports] = useState([]);
  const [activeReport, setActiveReport] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const streamAbortRef = useRef(null);
  const entitiesFetchRef = useRef(null);
  const initialReportLoadedRef = useRef(false);

  const entityNoun =
    connectorType === 'sql' ? 'tables' : connectorType === 'google_sheets' ? 'sheets' : 'objects';

  const basePath =
    connectorType === 'salesforce'
      ? `/api/connectors/salesforce/${encodeURIComponent(connectorId)}`
      : connectorType === 'sql'
        ? `/api/connectors/sql/${encodeURIComponent(connectorId)}`
        : `/api/connectors/google-sheets/${encodeURIComponent(connectorId)}`;

  const loadReports = useCallback(async () => {
    try {
      const { data } = await api.get(`${basePath}/assessments/reports`);
      setReports(data.reports || []);
      if ((data.reports || []).length > 0 && !initialReportLoadedRef.current) {
        initialReportLoadedRef.current = true;
        const latest = data.reports[0];
        const rep = await api.get(`/api/assessments/reports/${latest.id}`);
        setActiveReport({ ...rep.data.report, id: latest.id });
      }
    } catch (e) {
      console.error('Failed to load assessment reports', e);
    }
  }, [basePath]);

  useEffect(() => {
    if (scope !== 'standard') return undefined;

    if (parentEntityCount > 0) {
      setEntityOptions(normalizeParentEntities(availableEntitiesProp));
      setEntitiesLoadError(null);
      setEntitiesLoading(false);
      return undefined;
    }

    if (!connectorId) return undefined;

    const cacheKey = `${connectorType}:${connectorId}`;
    const cached = entityOptionsCache.get(cacheKey);
    if (cached) {
      setEntityOptions(cached);
      setEntitiesLoadError(null);
      setEntitiesLoading(false);
      return undefined;
    }

    if (entitiesFetchRef.current) {
      entitiesFetchRef.current
        .then((options) => {
          if (options) setEntityOptions(options);
        })
        .catch(() => {});
      return undefined;
    }

    let cancelled = false;
    setEntitiesLoading(true);
    setEntitiesLoadError(null);

    const fetchPromise = api
      .get(`${basePath}/assessments/entities`)
      .then(({ data }) => {
        const options = data.entities || [];
        entityOptionsCache.set(cacheKey, options);
        return options;
      })
      .finally(() => {
        entitiesFetchRef.current = null;
      });

    entitiesFetchRef.current = fetchPromise;

    fetchPromise
      .then((options) => {
        if (!cancelled) {
          setEntityOptions(options);
          setEntitiesLoadError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setEntityOptions([]);
          const status = e.response?.status;
          setEntitiesLoadError(
            status === 429
              ? 'Too many requests. Wait a minute, then click Selected again.'
              : e.response?.data?.detail || e.message || `Failed to load ${entityNoun}`,
          );
        }
      })
      .finally(() => {
        if (!cancelled) setEntitiesLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [scope, connectorId, connectorType, basePath, parentEntityCount, entityNoun, availableEntitiesProp]);

  const notifyJobActivity = useCallback(
    (status, progress, job) => {
      if (!onJobActivityChange) return;
      if (!status) {
        onJobActivityChange(null);
        return;
      }
      onJobActivityChange({ status, progress: progress || {}, job });
    },
    [onJobActivityChange],
  );

  const stopJobStream = useCallback(() => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort();
      streamAbortRef.current = null;
    }
  }, []);

  const applyJobPayload = useCallback(
    (job) => {
      const st = job?.status || job?.redis?.status;
      const progress = job?.progress || {};
      setJobStatus({ status: st, progress, job });
      notifyJobActivity(st, progress, job);
      return st;
    },
    [notifyJobActivity],
  );

  const streamJob = useCallback(
    (jid) => {
      stopJobStream();
      const controller = new AbortController();
      streamAbortRef.current = controller;

      streamAssessmentJob({
        jobId: jid,
        signal: controller.signal,
        onProgress: (evt) => {
          if (evt.job) applyJobPayload(evt.job);
        },
        onFinished: async (evt) => {
          if (streamAbortRef.current === controller) {
            streamAbortRef.current = null;
          }
          const job = evt.job;
          if (!job) {
            setLoading(false);
            setError(evt.error || 'Assessment job not found');
            notifyJobActivity(null);
            return;
          }
          const st = applyJobPayload(job);
          setLoading(false);
          notifyJobActivity(null);
          if (st === 'completed' && job.report_id) {
            const rep = await api.get(`/api/assessments/reports/${job.report_id}`);
            setActiveReport({ ...rep.data.report, id: job.report_id });
          }
          loadReports();
        },
      }).catch((e) => {
        if (e.name === 'AbortError') return;
        console.error('Assessment SSE failed', e);
        setLoading(false);
        setError(e.message || 'Failed to stream assessment progress');
        notifyJobActivity(null);
      });
    },
    [applyJobPayload, loadReports, notifyJobActivity, stopJobStream],
  );

  const resumeActiveJob = useCallback(async () => {
    try {
      const { data } = await api.get(`${basePath}/assessments/active-job`);
      const job = data.job;
      if (!job?.id) return;
      const st = job.status || job.redis?.status;
      if (!['queued', 'running', 'paused'].includes(st)) return;
      setJobId(job.id);
      const progress = job.progress || {};
      setJobStatus({ status: st, progress, job });
      setLoading(st === 'running' || st === 'queued');
      notifyJobActivity(st, progress, job);
      streamJob(job.id);
    } catch (e) {
      console.error('Failed to resume active assessment job', e);
    }
  }, [basePath, streamJob, notifyJobActivity]);

  useEffect(() => {
    initialReportLoadedRef.current = false;
    setJobId(null);
    setJobStatus(null);
    setLoading(false);
    loadReports();
    resumeActiveJob();
    return () => {
      if (streamAbortRef.current) {
        streamAbortRef.current.abort();
        streamAbortRef.current = null;
      }
    };
  }, [loadReports, resumeActiveJob, connectorId, connectorType]);

  const startAssessment = async () => {
    setError(null);
    if (scope === 'standard') {
      if (entitiesLoading) {
        setError(`Still loading ${entityNoun}. Please wait a moment.`);
        return;
      }
      if (entityOptions.length > 0 && selectedEntities.length === 0) {
        setError(`Select at least one ${entityNoun.slice(0, -1)} to assess.`);
        return;
      }
      if (entityOptions.length === 0 && !entitiesLoadError) {
        setError(
          connectorType === 'salesforce'
            ? 'No objects found. Sync your Salesforce schema using the object list above, then try again.'
            : `No ${entityNoun} available for this connection.`,
        );
        return;
      }
    }
    setLoading(true);
    try {
      const body = {
        scope,
        entities: scope === 'standard' && selectedEntities.length ? selectedEntities : null,
        options: {},
      };
      const { data } = await api.post(`${basePath}/assessments`, body);
      setJobId(data.job_id);
      const initial = { status: 'running', progress: {} };
      setJobStatus(initial);
      notifyJobActivity('running', {}, { id: data.job_id });
      streamJob(data.job_id);
    } catch (e) {
      if (e.response?.status === 409) {
        const detail = e.response?.data?.detail;
        const existingId =
          typeof detail === 'object' && detail?.job_id ? detail.job_id : null;
        if (existingId) {
          setJobId(existingId);
          setJobStatus({ status: detail.status || 'running', progress: {} });
          notifyJobActivity(detail.status || 'running', {}, { id: existingId });
          streamJob(existingId);
          return;
        }
      }
      const detail = e.response?.data?.detail;
      const message =
        typeof detail === 'object' && detail?.message
          ? detail.message
          : typeof detail === 'string'
            ? detail
            : e.message || 'Failed to start assessment';
      setError(message);
      setLoading(false);
    }
  };

  const pauseJob = async () => {
    if (!jobId) return;
    await api.post(`/api/assessments/${jobId}/pause`);
    setJobStatus((s) => {
      const next = { ...s, status: 'paused' };
      notifyJobActivity('paused', next.progress, next.job);
      return next;
    });
    setLoading(false);
  };

  const resumeJob = async () => {
    if (!jobId) return;
    setLoading(true);
    await api.post(`/api/assessments/${jobId}/resume`);
    streamJob(jobId);
  };

  const cancelJob = async () => {
    if (!jobId) return;
    // Abort SSE before cancel so the server is not closing a nested stream mid-flight.
    stopJobStream();
    await api.post(`/api/assessments/${jobId}/cancel`);
    setLoading(false);
    setJobStatus((s) => ({ ...s, status: 'cancelled' }));
    notifyJobActivity(null);
  };

  const loadReport = async (reportId) => {
    const { data } = await api.get(`/api/assessments/reports/${reportId}`);
    setActiveReport({ ...data.report, id: reportId });
  };

  const progress = jobStatus?.progress || {};
  const pct = progress.percent ?? 0;

  return (
    <div
      className={`connector-assessment-panel${embedded ? ' connector-assessment-panel--embedded' : ''}`}
    >
      <article className="connector-assessment-run-card">
        <h3 className="connector-assessment-title">Run data assessment</h3>
        <p className="connector-assessment-subtitle">
          Profile data quality without storing rows. Results appear in the report dashboard below.
        </p>

      <div className="connector-assessment-scope">
        {['quick', 'standard', 'full'].map((s) => (
          <button
            key={s}
            type="button"
            className={`assessment-scope-btn ${scope === s ? 'active' : ''}`}
            onClick={() => setScope(s)}
            disabled={loading}
          >
            {s === 'quick' ? 'Quick' : s === 'standard' ? 'Selected' : 'Full'}
          </button>
        ))}
      </div>

      {scope === 'standard' && (
        <div className="connector-assessment-entities">
          <label className="connector-assessment-entities-label">
            Select {entityNoun} to assess
          </label>
          {entitiesLoading && (
            <p className="connector-assessment-entities-hint">Loading {entityNoun}…</p>
          )}
          {entitiesLoadError && (
            <p className="connector-assessment-entities-error">{entitiesLoadError}</p>
          )}
          {!entitiesLoading && !entitiesLoadError && entityOptions.length === 0 && (
            <p className="connector-assessment-entities-hint">
              {connectorType === 'salesforce'
                ? 'No objects in synced schema. Open the object list above and run a schema sync first.'
                : `No ${entityNoun} found for this connection.`}
            </p>
          )}
          {entityOptions.length > 0 && (
            <AssessmentEntityMultiSelect
              options={entityOptions}
              selected={selectedEntities}
              onChange={setSelectedEntities}
              disabled={loading || entitiesLoading}
              entityNoun={entityNoun}
            />
          )}
        </div>
      )}

      <div className="connector-assessment-actions">
        <button type="button" className="assessment-primary-btn" onClick={startAssessment} disabled={loading}>
          Run assessment
        </button>
        {jobId && jobStatus?.status === 'running' && (
          <>
            <button type="button" className="assessment-secondary-btn" onClick={pauseJob}>
              Pause
            </button>
            <button type="button" className="assessment-secondary-btn" onClick={cancelJob}>
              Cancel
            </button>
          </>
        )}
        {jobId && jobStatus?.status === 'paused' && (
          <>
            <button type="button" className="assessment-primary-btn" onClick={resumeJob}>
              Resume
            </button>
            <button type="button" className="assessment-secondary-btn" onClick={cancelJob}>
              Cancel
            </button>
          </>
        )}
      </div>

      {error && <div className="connector-assessment-error">{error}</div>}

      {jobStatus && (
        <div className="connector-assessment-progress">
          <div className="assessment-progress-bar">
            <div className="assessment-progress-fill" style={{ width: `${pct}%` }} />
          </div>
          <span className="assessment-progress-text">
            {jobStatus.status}
            {progress.sub_phase
              ? ` — ${progress.message || progress.sub_phase.replace(/_/g, ' ')}`
              : ''}
            {!progress.sub_phase && progress.current_entity ? ` — ${progress.current_entity}` : ''}
            {!progress.sub_phase &&
            progress.completed_entities != null &&
            progress.total_entities
              ? ` (${progress.completed_entities}/${progress.total_entities})`
              : ''}
          </span>
        </div>
      )}

      </article>

      {activeReport ? (
        <AssessmentDashboard
          report={activeReport}
          reports={reports}
          onSelectReport={(id) => loadReport(id)}
          connectorType={connectorType}
          connectorDisplayName={connectorDisplayName}
        />
      ) : (
        !loading &&
        !jobStatus && (
          <p className="connector-assessment-empty-hint">
            Run an assessment to generate your snapshot dashboard.
          </p>
        )
      )}
    </div>
  );
}
