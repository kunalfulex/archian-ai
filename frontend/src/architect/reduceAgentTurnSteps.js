/**
 * Pure reducers for agent / extraction progress steps (SSE event_type → steps array).
 */

export function reduceAgentTurnSteps(prev, event) {
  if (!event || typeof event !== 'object' || !event.event_type) return prev;

  switch (event.event_type) {
    case 'agent_step_started': {
      if (!event.tool_id || event.tool_id === 'run_data_ops_plan') return prev;
      const entry = { tool_id: event.tool_id, label: event.label || event.tool_id, status: 'running' };
      const idx = prev.findIndex((s) => s.tool_id === event.tool_id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], ...entry };
        return next;
      }
      return [...prev, entry];
    }
    case 'agent_step_completed': {
      if (!event.tool_id || event.tool_id === 'run_data_ops_plan') return prev;
      const idx = prev.findIndex((s) => s.tool_id === event.tool_id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], status: 'done' };
        return next;
      }
      return [...prev, { tool_id: event.tool_id, label: event.label || event.tool_id, status: 'done' }];
    }
    case 'agent_operation_started': {
      if (!event.operation_id) return prev;
      const entry = {
        tool_id: event.operation_id,
        label: event.label || event.operation_id,
        status: 'running',
      };
      const idx = prev.findIndex((s) => s.tool_id === event.operation_id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], ...entry };
        return next;
      }
      return [...prev, entry];
    }
    case 'agent_operation_completed': {
      if (!event.operation_id) return prev;
      const label = event.label || event.operation_id;
      const detail = event.detail || event.summary || null;
      const idx = prev.findIndex((s) => s.tool_id === event.operation_id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = { ...next[idx], label, detail, status: 'done' };
        return next;
      }
      return [...prev, { tool_id: event.operation_id, label, detail, status: 'done' }];
    }
    case 'extraction_progress': {
      if (!prev.length) return prev;
      const src = (event.source_type || 'salesforce').toLowerCase();
      const prefix =
        src === 'sql'
          ? 'SQL extraction'
          : src === 'google_sheets'
            ? 'Google Sheets extraction'
            : src === 'file'
              ? 'File extraction'
              : 'Salesforce extraction';
      const msg = event.message ? String(event.message).slice(0, 140).trim() : '';
      const phaseMsg = msg || prefix;
      const rev = [...prev].reverse();
      const ri = rev.findIndex((s) => s.status === 'running');
      if (ri < 0) return prev;
      const i = prev.length - 1 - ri;
      const next = [...prev];
      next[i] = { ...next[i], detail: phaseMsg };
      return next;
    }
    default:
      return prev;
  }
}

const SETUP_PHASE_SPINNER_LABELS = {
  resolve_org: 'Connecting to Salesforce',
  resolve_connection: 'Connecting to data source',
  read_sheet: 'Reading spreadsheet',
  resolve_object: 'Reviewing fields and structure',
  resolve_table: 'Reviewing table schema',
  build_query: 'Building extraction plan',
  execute: 'Extracting records',
  processing: 'Extracting records',
};

export function extractionProgressLoadingMessage(event) {
  const setupPhase = (event.phase || '').toLowerCase();
  if (SETUP_PHASE_SPINNER_LABELS[setupPhase]) {
    return SETUP_PHASE_SPINNER_LABELS[setupPhase];
  }

  const src = (event.source_type || 'salesforce').toLowerCase();
  if (src === 'sql') return 'Extracting from database';
  if (src === 'google_sheets') return 'Reading Google Sheet';
  if (src === 'file') return 'Extracting from file';
  return 'Extracting records';
}

export function planReadyLoadingMessage(summary) {
  const raw = typeof summary === 'string' ? summary.trim() : '';
  const firstLine = raw.split(/\r?\n/).map((ln) => ln.trim()).find(Boolean) || '';
  const short = firstLine.slice(0, 180);
  if (!short) return 'Plan ready — running tools';
  return short.length < firstLine.length ? `${short}…` : short;
}
