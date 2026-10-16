/**
 * SSE clients for connector assessment jobs and enterprise graph runs.
 */
import { parseSSEDataObjects } from '../architect/architectSse';
import { buildAuthHeaders } from '../utils/authStorage';

function authHeaders(method = 'GET') {
  return buildAuthHeaders({}, method);
}

async function readErrorBody(response) {
  try {
    const text = await response.text();
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch {
      return { detail: text };
    }
  } catch {
    return null;
  }
}

async function consumeSseResponse(response, { onEvent }) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';

    for (const part of parts) {
      const events = parseSSEDataObjects(part);
      for (const evt of events) {
        onEvent?.(evt);
      }
    }
  }

  if (buffer.trim()) {
    const events = parseSSEDataObjects(buffer);
    for (const evt of events) {
      onEvent?.(evt);
    }
  }
}

/**
 * Stream progress for a single connector assessment job.
 */
export async function streamAssessmentJob({
  jobId,
  signal,
  onProgress,
  onFinished,
}) {
  const url = `${process.env.REACT_APP_API_URL}/api/assessments/${encodeURIComponent(jobId)}/stream`;
  const response = await fetch(url, { method: 'GET', headers: authHeaders('GET'), credentials: 'include', signal });

  if (!response.ok) {
    const body = await readErrorBody(response);
    const err = new Error(
      typeof body?.detail === 'string'
        ? body.detail
        : body?.detail?.message || body?.message || response.statusText,
    );
    err.response = { data: body, status: response.status };
    throw err;
  }

  await consumeSseResponse(response, {
    onEvent(evt) {
      if (evt.event_type === 'assessment_progress') {
        onProgress?.(evt);
      } else if (evt.event_type === 'assessment_job_finished') {
        onFinished?.(evt);
      }
    },
  });
}

/**
 * Stream progress for an enterprise graph assessment / remap / warehouse run.
 */
export async function streamEnterpriseGraphRun({
  runId,
  signal,
  onProgress,
  onFinished,
}) {
  const url = `${process.env.REACT_APP_API_URL}/api/enterprise-graphs/runs/${encodeURIComponent(runId)}/stream`;
  const response = await fetch(url, { method: 'GET', headers: authHeaders('GET'), credentials: 'include', signal });

  if (!response.ok) {
    const body = await readErrorBody(response);
    const err = new Error(
      typeof body?.detail === 'string'
        ? body.detail
        : body?.detail?.message || body?.message || response.statusText,
    );
    err.response = { data: body, status: response.status };
    throw err;
  }

  await consumeSseResponse(response, {
    onEvent(evt) {
      if (evt.event_type === 'enterprise_graph_run_progress') {
        onProgress?.(evt);
      } else if (evt.event_type === 'enterprise_graph_run_finished') {
        onFinished?.(evt);
      }
    },
  });
}

/**
 * Promise wrapper for graph runs (remap, warehouse redesign, etc.).
 */
export function watchEnterpriseGraphRun(runId, { signal, onProgress } = {}) {
  return new Promise((resolve, reject) => {
    streamEnterpriseGraphRun({
      runId,
      signal,
      onProgress: (evt) => onProgress?.(evt.run),
      onFinished: (evt) => {
        const run = evt.run;
        if (!run) {
          reject(new Error(evt.error || 'Run not found'));
          return;
        }
        if (run.status === 'completed') {
          resolve(run);
          return;
        }
        reject(new Error(run.error_summary || `Run ${run.status}`));
      },
    }).catch(reject);
  });
}
