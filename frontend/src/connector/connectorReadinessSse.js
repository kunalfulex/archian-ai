/**
 * SSE client for connector agent readiness (metadata + labeling + profiling).
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

/** Normalize legacy pending_sync sessionStorage shapes. */
export function normalizePendingSync(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const typeRaw = raw.connectorType || raw.type || '';
  const connectorType =
    typeRaw === 'gsheets' ? 'google_sheets' : typeRaw;
  const connectorInstanceId =
    raw.connectorInstanceId || raw.connectorId || raw.orgId;
  if (!connectorType || !connectorInstanceId) return null;
  const trigger = raw.trigger === 'resync' || raw.trigger === 'schema_sync'
    ? raw.trigger
    : 'create';
  return {
    connectorType,
    connectorInstanceId: String(connectorInstanceId),
    displayName: raw.displayName || raw.name || 'Connection',
    trigger,
  };
}

export async function streamConnectorReadiness({
  connectorType,
  connectorInstanceId,
  signal,
  onProgress,
  onFinished,
}) {
  const base = process.env.REACT_APP_API_URL || '';
  const url = `${base}/api/labeling/connectors/${encodeURIComponent(connectorType)}/${encodeURIComponent(connectorInstanceId)}/readiness/stream`;
  const response = await fetch(url, {
    method: 'GET',
    headers: authHeaders('GET'),
    credentials: 'include',
    signal,
  });

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
      if (evt.event_type === 'connector_readiness') {
        onProgress?.(evt);
      } else if (evt.event_type === 'connector_readiness_finished') {
        onFinished?.(evt);
      }
    },
  });
}

export function watchConnectorReadiness(connectorType, connectorInstanceId, { signal, onProgress } = {}) {
  return new Promise((resolve, reject) => {
    streamConnectorReadiness({
      connectorType,
      connectorInstanceId,
      signal,
      onProgress,
      onFinished: (evt) => resolve(evt),
    }).catch(reject);
  });
}

export function connectorTypeBannerLabel(connectorType) {
  if (connectorType === 'salesforce') return 'Salesforce';
  if (connectorType === 'sql') return 'database';
  if (connectorType === 'google_sheets') return 'Google Sheets';
  return 'connection';
}

export function readinessPhaseMessage(phase, stats, { trigger = 'create', name = '', metadataProgress } = {}) {
  return readinessInProgressMessage({ trigger, phase, name, stats, metadataProgress });
}

function formatMetadataProgressSuffix(metadataProgress) {
  if (!metadataProgress || typeof metadataProgress !== 'object') return '';
  const total = Number(metadataProgress.total) || 0;
  const processed = Number(metadataProgress.processed) || 0;
  if (total > 0) {
    return ` (${processed}/${total})`;
  }
  const percent = metadataProgress.percent;
  if (percent != null && percent !== '') {
    return ` (${Number(percent)}%)`;
  }
  return '';
}

function formatEntityProgressSuffix(stats) {
  const ready = stats?.ready ?? 0;
  const total = stats?.total ?? 0;
  if (total > 0) {
    return ` (${ready}/${total})`;
  }
  return '';
}

export function readinessInProgressMessage({
  trigger = 'create',
  phase,
  name,
  stats,
  metadataProgress,
}) {
  const label = (name || 'your connection').trim();

  if (phase === 'metadata_sync') {
    return `Archian AI is reading ${label}${formatMetadataProgressSuffix(metadataProgress)}`;
  }
  if (phase === 'entity_labeling') {
    const suffix = formatEntityProgressSuffix(stats);
    if (suffix) {
      return `Archian AI is reading entities for ${label}${suffix}`;
    }
    return `Archian AI is reading entities for ${label}`;
  }
  if (phase === 'profile_rollup') {
    return `Archian AI is building your connector profile for ${label}`;
  }
  return trigger === 'resync' || trigger === 'schema_sync'
    ? `Archian AI is updating ${label}`
    : `Archian AI is preparing your agent for ${label}`;
}

export function readinessCompleteMessage({
  trigger = 'create',
  name,
  connectorType,
}) {
  const label = (name || 'your connection').trim();
  const typeLabel = connectorTypeBannerLabel(connectorType);
  const isResync = trigger === 'resync' || trigger === 'schema_sync';

  if (isResync) {
    return `${label} is up to date.`;
  }
  return `Your Archian AI agent is ready to work with ${label} (${typeLabel}).`;
}

export function readinessSetupCta({ trigger, needsOrgName, needsOrgDescription }) {
  if (trigger === 'resync' || trigger === 'schema_sync') {
    if (needsOrgName) {
      return { label: 'Name this org →', show: true };
    }
    if (needsOrgDescription) {
      return { label: 'Describe this org (optional) →', show: true };
    }
    return { label: '', show: false };
  }
  if (needsOrgName) {
    return { label: 'Name this org →', show: true };
  }
  if (needsOrgDescription) {
    return { label: 'Describe this org (optional) →', show: true };
  }
  return { label: '', show: false };
}
