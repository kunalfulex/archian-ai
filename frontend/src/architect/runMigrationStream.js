/**
 * POST conversation migrate action with SSE response (held open until terminal).
 */
import { parseSSEDataObjects } from './architectSse';
import { buildAuthHeaders } from '../utils/authStorage';
import { isPersistedChatMessagePayload } from './applyMigrationStreamToChat';
import {
  clearRecoveryLoaderUi,
  completeRecoveryActivity,
  createEmptyRecoveryActivity,
  hasRunningRecoveryStep,
  latestRecoverySpinnerLabel,
  reduceRecoveryActivity,
} from './reduceRecoveryActivity';

function authHeaders(method = 'GET', extra = {}) {
  return buildAuthHeaders({ 'Content-Type': 'application/json', ...extra }, method);
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

function applyRecoveryStreamEvent(evt, handlers) {
  if (evt.event_type === 'recovery_analysis_started') {
    handlers.onRecoveryAnalysisStarted?.(evt);
    return;
  }
  if (evt.event_type === 'recovery_activity') {
    handlers.onRecoveryActivity?.(evt);
    return;
  }
  if (
    evt.event_type === 'planner_recovery_required' &&
    isPersistedChatMessagePayload(evt.message)
  ) {
    handlers.onRecoveryComplete?.();
    handlers.onRecoveryChat?.(evt);
    return;
  }
  if (evt.event_type === 'migration_started') {
    handlers.onStarted?.(evt);
  } else if (evt.event_type === 'migration_progress') {
    handlers.onProgress?.(evt);
  } else if (evt.event_type === 'migration_completed' && isPersistedChatMessagePayload(evt.message)) {
    handlers.onCompletedChat?.(evt);
  } else if (evt.event_type === 'migration_job_finished') {
    handlers.onFinished?.(evt);
  }
}

export function createDefaultRecoveryStreamHandlers(deps) {
  const {
    setRecoveryActivity,
    setLoadingMessage,
    setIsLoading,
    stopLoadingMessages,
  } = deps;

  return {
    onRecoveryAnalysisStarted(evt) {
      setRecoveryActivity?.(
        createEmptyRecoveryActivity({
          active: true,
          jobId: evt.job_id || null,
          batchId: evt.batch_id || null,
          items: [],
        }),
      );
      stopLoadingMessages?.();
      setIsLoading?.(true);
      setLoadingMessage?.('Analyzing migration failures…');
    },
    onRecoveryActivity(evt) {
      let nextPreview = null;
      setRecoveryActivity?.((prev) => {
        nextPreview = reduceRecoveryActivity(prev, evt);
        return nextPreview;
      });
      if (!nextPreview) return;
      const spinner = latestRecoverySpinnerLabel(nextPreview);
      if (spinner) {
        setLoadingMessage?.(spinner);
        setIsLoading?.(true);
        return;
      }
      setIsLoading?.(false);
      if (!hasRunningRecoveryStep(nextPreview)) {
        setRecoveryActivity?.((prev) => completeRecoveryActivity(prev));
      }
    },
    onRecoveryComplete() {
      clearRecoveryLoaderUi({ setRecoveryActivity, setIsLoading, stopLoadingMessages });
    },
  };
}

/**
 * @returns {Promise<'stream'|'json'>} mode used
 */
export async function runMigrationStream({
  endpoint,
  payload,
  signal,
  onStarted,
  onProgress,
  onFinished,
  onCompletedChat,
  onRecoveryAnalysisStarted,
  onRecoveryActivity,
  onRecoveryChat,
  onRecoveryComplete,
  onJsonResponse,
}) {
  const streamHandlers = {
    onStarted,
    onProgress,
    onFinished,
    onCompletedChat,
    onRecoveryAnalysisStarted,
    onRecoveryActivity,
    onRecoveryChat,
    onRecoveryComplete,
  };
  const response = await fetch(`${process.env.REACT_APP_API_URL}${endpoint}`, {
    method: 'POST',
    headers: authHeaders('POST'),
    credentials: 'include',
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    const body = await readErrorBody(response);
    const err = new Error(body?.message || body?.detail || response.statusText);
    err.response = { data: body, status: response.status };
    throw err;
  }

  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('text/event-stream')) {
    const data = await response.json();
    onJsonResponse?.(data);
    return 'json';
  }

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
        applyRecoveryStreamEvent(evt, streamHandlers);
      }
    }
  }

  if (buffer.trim()) {
    const events = parseSSEDataObjects(buffer);
    for (const evt of events) {
      applyRecoveryStreamEvent(evt, streamHandlers);
    }
  }

  return 'stream';
}
