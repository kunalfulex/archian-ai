/**
 * Reducer for unified operation preview loader (dynamic pipeline + SSE events).
 */
import {
  buildPipelineFromClassification,
  connectSourceLabel,
  humanizePhaseId,
  labelForPhaseId,
  leadingPreludeDoneThroughIndex,
  phasesFromPipelineDefinitions,
  PHASE_STATUS,
  isPostExtractPipelinePhaseId,
  resolveDataOpsPhaseId,
} from './operationPhaseRegistry';

function phaseIndex(phases, id) {
  return phases.findIndex((p) => p.id === id);
}

function pipelineOrder(prev) {
  if (prev.pipelineOrder?.length) return prev.pipelineOrder;
  return (prev.phases || []).map((p) => p.id);
}

function ensurePhaseInPipeline(prev, phaseId, label, context) {
  const order = pipelineOrder(prev);
  if (order.includes(phaseId)) {
    return { phases: prev.phases || [], pipelineOrder: order };
  }
  const phases = [...(prev.phases || [])];
  phases.push({
    id: phaseId,
    label: label || humanizePhaseId(phaseId),
    status: PHASE_STATUS.PENDING,
    detail: null,
    progress: null,
  });
  return { phases, pipelineOrder: [...order, phaseId] };
}

function upsertPhase(phases, id, patch, context) {
  const idx = phaseIndex(phases, id);
  if (idx < 0) {
    return [
      ...phases,
      {
        id,
        label: patch.label || labelForPhaseId(id, context),
        status: patch.status || PHASE_STATUS.PENDING,
        detail: patch.detail ?? null,
        progress: patch.progress ?? null,
      },
    ];
  }
  const next = [...phases];
  const merged = { ...next[idx], ...patch };
  if (patch.label === undefined && !merged.label) {
    merged.label = labelForPhaseId(id, context);
  }
  next[idx] = merged;
  return next;
}

function markPriorDone(phases, activeId, order) {
  const activeIdx = order.indexOf(activeId);
  if (activeIdx < 0) return phases;
  return phases.map((p) => {
    const i = order.indexOf(p.id);
    if (i < 0) return p;
    if (
      i < activeIdx &&
      p.status !== PHASE_STATUS.DONE &&
      p.status !== PHASE_STATUS.ERROR
    ) {
      return { ...p, status: PHASE_STATUS.DONE, progress: null };
    }
    return p;
  });
}

function applyPipelineDefinitions(prev, definitions, context) {
  const defs = definitions?.length ? definitions : buildPipelineFromClassification(context || prev.context);
  const order = defs.map((d) => d.id);
  const phases = phasesFromPipelineDefinitions(defs, context || prev.context, {
    doneThroughIndex: leadingPreludeDoneThroughIndex(defs),
  });
  return {
    ...prev,
    phases,
    pipelineOrder: order,
    context: context || prev.context,
  };
}

const SETUP_TO_PHASE = {
  resolve_org: 'connect_source',
  resolve_connection: 'connect_source',
  read_sheet: 'connect_source',
  resolve_object: 'inspect_catalog',
  resolve_table: 'inspect_catalog',
  build_query: 'build_extraction_plan',
  execute: 'extract_records',
  processing: 'extract_records',
};

const EXTRACT_TOOL_IDS = new Set([
  'extract_salesforce',
  'extract_sql',
  'extract_google_sheets',
  'extract_file',
  'file_reextract',
]);

/** Granular setup phases, or the active plan extract step when multi-source uses one row per extract. */
function resolveExtractionProgressTarget(order, phases, setupPhase) {
  const granularId = SETUP_TO_PHASE[setupPhase] || 'extract_records';
  if (order.length && order.includes(granularId)) {
    return granularId;
  }
  if (order.length) {
    const running = order.find((id) => {
      if (!EXTRACT_TOOL_IDS.has(id)) return false;
      const p = phases.find((ph) => ph.id === id);
      return p?.status === PHASE_STATUS.RUNNING;
    });
    if (running) return running;
    const firstExtract = order.find((id) => EXTRACT_TOOL_IDS.has(id));
    if (firstExtract) return firstExtract;
  }
  return granularId;
}

export function createEmptyOperationPreview() {
  return { phases: [], pipelineOrder: [], context: null };
}

export function reduceOperationPreview(prev, event) {
  if (!event || typeof event !== 'object' || !event.event_type) return prev;

  const order = pipelineOrder(prev);
  const ctx = prev.context;

  switch (event.event_type) {
    case 'classification_started': {
      return prev;
    }

    case 'classification_completed': {
      return {
        ...prev,
        context: {
          source: event.source,
          destination: event.destination,
          intent: event.intent,
          mode: event.mode,
          source_name: event.source_name,
          destination_name: event.destination_name,
          data_operations: event.data_operations,
          plan_contract: event.plan_contract,
        },
      };
    }

    case 'agent_plan_ready': {
      if (!event.pipeline?.length) return prev;
      return applyPipelineDefinitions(
        prev,
        event.pipeline,
        prev.context,
      );
    }

    case 'operation_phase': {
      if (!event.phase) return prev;
      if (order.length && !order.includes(event.phase)) return prev;
      let phases = prev.phases?.length
        ? [...prev.phases]
        : applyPipelineDefinitions(prev, null, ctx).phases;
      phases = markPriorDone(phases, event.phase, order);
      const status = event.status || PHASE_STATUS.RUNNING;
      phases = upsertPhase(phases, event.phase, {
        status,
        label: event.label || undefined,
        detail: event.detail ?? undefined,
        progress:
          event.progress != null && !Number.isNaN(Number(event.progress))
            ? Number(event.progress)
            : undefined,
      }, ctx);
      return { ...prev, phases };
    }

    case 'extraction_progress': {
      const setupPhase = (event.phase || '').toLowerCase();
      const phasesForResolve = prev.phases?.length ? prev.phases : [];
      const opId = resolveExtractionProgressTarget(order, phasesForResolve, setupPhase);
      if (order.length && !order.includes(opId)) return prev;
      let phases = prev.phases?.length
        ? [...prev.phases]
        : applyPipelineDefinitions(prev, null, ctx).phases;
      phases = markPriorDone(phases, opId, order);
      const msg = event.message ? String(event.message).slice(0, 160) : null;
      const pct =
        event.progress != null && Number(event.progress) > 0
          ? Number(event.progress)
          : null;
      phases = upsertPhase(phases, opId, {
        status: PHASE_STATUS.RUNNING,
        detail: msg || undefined,
        progress: pct,
      }, ctx);
      return { ...prev, phases };
    }

    case 'extraction_job_finished': {
      let phases = prev.phases?.length ? [...prev.phases] : [];
      const finOrder = order.length ? order : phases.map((p) => p.id);
      const firstPostExtractIdx = finOrder.findIndex((id) => isPostExtractPipelinePhaseId(id));
      const lastExtractIdx =
        firstPostExtractIdx >= 0 ? firstPostExtractIdx - 1 : finOrder.length - 1;
      if (lastExtractIdx >= 0) {
        const lastId = finOrder[lastExtractIdx] || 'prepare_preview';
        phases = markPriorDone(phases, lastId, finOrder);
      }
      phases = phases.map((p) => {
        if (isPostExtractPipelinePhaseId(p.id)) {
          return p;
        }
        const idx = finOrder.indexOf(p.id);
        if (idx >= 0 && idx <= lastExtractIdx && p.status !== PHASE_STATUS.ERROR) {
          return { ...p, status: PHASE_STATUS.DONE, progress: null };
        }
        return p;
      });
      return { ...prev, phases };
    }

    case 'agent_step_started': {
      const toolId = event.tool_id;
      if (!toolId || toolId === 'run_data_ops_plan') return prev;

      const isMapOrMigrateStep =
        toolId === 'map_fields' || toolId === 'start_migration' || toolId === 'migrate_records';

      if (!(prev.phases?.length) && isMapOrMigrateStep) {
        const label = event.label || labelForPhaseId(toolId, ctx);
        return {
          ...prev,
          phases: [
            {
              id: toolId,
              label,
              status: PHASE_STATUS.RUNNING,
              detail: null,
              progress: null,
            },
          ],
          pipelineOrder: [toolId],
        };
      }

      let phases = prev.phases?.length
        ? [...prev.phases]
        : applyPipelineDefinitions(prev, null, ctx).phases;
      const ord = pipelineOrder({ ...prev, phases });

      if (ord.includes(toolId)) {
        phases = markPriorDone(phases, toolId, ord);
        phases = upsertPhase(phases, toolId, {
          status: PHASE_STATUS.RUNNING,
          label: event.label || labelForPhaseId(toolId, ctx),
        }, ctx);
        return { ...prev, phases };
      }

      if (EXTRACT_TOOL_IDS.has(toolId) && phaseIndex(phases, 'connect_source') >= 0) {
        phases = markPriorDone(phases, 'connect_source', ord);
        phases = upsertPhase(phases, 'connect_source', {
          status: PHASE_STATUS.RUNNING,
          label: event.label || connectSourceLabel(ctx),
        }, ctx);
      }
      return { ...prev, phases };
    }

    case 'agent_step_completed': {
      const toolId = event.tool_id;
      if (!toolId) return prev;
      const ord = pipelineOrder(prev);
      if (!ord.includes(toolId)) return prev;
      let phases = [...(prev.phases || [])];
      phases = upsertPhase(phases, toolId, {
        status: PHASE_STATUS.DONE,
        progress: null,
      }, ctx);
      return { ...prev, phases };
    }

    case 'agent_operation_started': {
      const phaseId = resolveDataOpsPhaseId(event);
      if (!phaseId) return prev;
      let base = prev;
      if (!order.includes(phaseId)) {
        const ensured = ensurePhaseInPipeline(prev, phaseId, event.label, ctx);
        base = { ...prev, phases: ensured.phases, pipelineOrder: ensured.pipelineOrder };
      }
      const ord = pipelineOrder(base);
      let phases = base.phases?.length ? [...base.phases] : [];
      phases = markPriorDone(phases, phaseId, ord);
      phases = upsertPhase(phases, phaseId, {
        status: PHASE_STATUS.RUNNING,
        label: event.label || labelForPhaseId(phaseId, ctx),
      }, ctx);
      return { ...base, phases };
    }

    case 'agent_operation_completed': {
      const phaseId = resolveDataOpsPhaseId(event);
      if (!phaseId) return prev;
      let base = prev;
      if (!order.includes(phaseId)) {
        const ensured = ensurePhaseInPipeline(prev, phaseId, event.label, ctx);
        base = { ...prev, phases: ensured.phases, pipelineOrder: ensured.pipelineOrder };
      }
      let phases = [...(base.phases || [])];
      phases = upsertPhase(phases, phaseId, {
        status: PHASE_STATUS.DONE,
        progress: null,
        detail: event.detail || event.summary || undefined,
        label: event.label || undefined,
      }, ctx);
      return { ...base, phases };
    }

    default:
      return prev;
  }
}

const INTERNAL_SPINNER_PREFIXES = /^(architect:|data_ops:|salesforce_info)/i;

function isInternalSpinnerText(text) {
  const t = (text || '').trim();
  return !t || INTERNAL_SPINNER_PREFIXES.test(t);
}

/** First user-facing pipeline step label (skips classification prelude ids). */
export function primaryPipelineSpinnerLabel(pipeline) {
  const steps = Array.isArray(pipeline) ? pipeline : [];
  const work = steps.find(
    (p) => p?.id && !['understand_request', 'identify_route'].includes(p.id),
  );
  return work?.label?.trim() || steps[steps.length - 1]?.label?.trim() || null;
}

/**
 * Spinner line above Agent steps — matches the currently running step label.
 */
export function spinnerMessageFromOperationPreview(preview) {
  const phases = preview?.phases || [];
  if (!phases.length) return null;

  const order =
    preview.pipelineOrder?.length > 0
      ? preview.pipelineOrder
      : phases.map((p) => p.id);

  const running = phases.find((p) => p.status === PHASE_STATUS.RUNNING);
  if (running?.label?.trim()) return running.label.trim();

  const runningIdx = order.findIndex((id) => {
    const p = phases.find((ph) => ph.id === id);
    return p?.status === PHASE_STATUS.RUNNING;
  });
  if (runningIdx >= 0) {
    const p = phases.find((ph) => ph.id === order[runningIdx]);
    if (p?.label?.trim()) return p.label.trim();
  }

  const lastDoneIdx = order.reduce(
    (acc, id, i) => {
      const p = phases.find((ph) => ph.id === id);
      return p?.status === PHASE_STATUS.DONE ? i : acc;
    },
    -1,
  );
  if (lastDoneIdx >= 0 && lastDoneIdx + 1 < order.length) {
    const next = phases.find((ph) => ph.id === order[lastDoneIdx + 1]);
    if (next?.status === PHASE_STATUS.RUNNING && next?.label?.trim()) {
      return next.label.trim();
    }
  }

  return null;
}

export function planReadyUserMessage(event) {
  const fromPipeline = primaryPipelineSpinnerLabel(event?.pipeline);
  if (fromPipeline) return fromPipeline;

  const ul = (event?.user_label || '').trim();
  if (ul && !isInternalSpinnerText(ul)) return ul.length > 120 ? `${ul.slice(0, 120)}…` : ul;

  const summary = (event?.summary || '').trim();
  if (isInternalSpinnerText(summary)) return null;
  if (summary) return summary.length > 120 ? `${summary.slice(0, 120)}…` : summary;
  return null;
}
