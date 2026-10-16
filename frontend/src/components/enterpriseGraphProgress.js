/**
 * Enterprise graph assessment: graph-level phases and per-connector sub-phases.
 * Keep in sync with backend services/data_assessment/progress.py and job_worker.
 */

/** Graph run phases (enterprise_graph_runs.progress.phase). */
export const GRAPH_RUN_PHASES = [
  { id: 'queued', label: 'Queued' },
  { id: 'assessment', label: 'Assessing connectors' },
  { id: 'linking', label: 'Cross-connector mapping' },
  { id: 'canonical', label: 'Canonical model' },
  { id: 'lake', label: 'Lake layout' },
  { id: 'warehouse', label: 'Warehouse design' },
  { id: 'report', label: 'Unified report' },
  { id: 'done', label: 'Complete' },
];

/** Per-connector assessment sub-phases (connector_assessment_jobs.progress.sub_phase). */
export const CONNECTOR_SUB_PHASES = [
  { id: 'listing', label: 'Listing objects / tables' },
  { id: 'profiling', label: 'Profiling data quality' },
  { id: 'saving', label: 'Saving partial results' },
  { id: 'metadata_gaps', label: 'Analyzing metadata gaps' },
  { id: 'synthesizing', label: 'Generating AI report sections' },
  { id: 'attaching_graph', label: 'Attaching schema graph' },
  { id: 'finalizing', label: 'Finalizing report' },
];

export const SUB_PHASE_LABELS = Object.fromEntries(
  CONNECTOR_SUB_PHASES.map((p) => [p.id, p.label]),
);

/** Graph rollup sub-steps (enterprise_graph_runs.progress.sub_step). */
export const GRAPH_SUB_STEP_LABELS = {
  entity_linking: 'Matching entities across connectors',
  inventory: 'Building unified inventory',
  canonical_build: 'Building canonical model',
  lake_context: 'Loading inventory for lake design',
  lake_bronze: 'Designing bronze tables',
  lake_silver: 'Designing silver views',
  lake_ddl: 'Generating lake DDL',
  lake_save: 'Saving lake proposal',
  warehouse_start: 'Starting warehouse design',
  warehouse_context: 'Loading model and mappings',
  warehouse_ai: 'AI designing warehouse tables',
  warehouse_rules: 'Building model from rules',
  warehouse_map: 'Mapping sources to warehouse tables',
  warehouse_ddl: 'Generating warehouse DDL',
  warehouse_save: 'Saving warehouse proposal',
  report_start: 'Starting unified report',
  report_member: 'Merging connector reports',
  report_rollup: 'Computing combined scores',
  report_sections: 'Assembling dashboard sections',
  report_snapshot: 'Assembling unified report',
};

export function isConnectorAssessmentPhase(phase) {
  const p = (phase || 'assessment').toLowerCase();
  return p === 'assessment' || p === 'queued' || p === 'rollup';
}

export function graphRollupDetail(progress) {
  if (!progress) return null;
  if (progress.detail) return progress.detail;
  const sub = progress.sub_step;
  if (sub && GRAPH_SUB_STEP_LABELS[sub]) return GRAPH_SUB_STEP_LABELS[sub];
  if (sub) return sub.replace(/_/g, ' ');
  return null;
}

export function graphRunSteps() {
  return GRAPH_RUN_PHASES.filter((p) => p.id !== 'queued');
}

export function graphPhaseIndex(phaseId) {
  let id = (phaseId || 'assessment').toLowerCase();
  // Backend sets rollup while waiting to persist linking phase.
  if (id === 'rollup') {
    id = 'linking';
  }
  const steps = graphRunSteps();
  const idx = steps.findIndex((p) => p.id === id);
  return idx >= 0 ? idx : 0;
}

/** Phase chip + bar: advance UI when every connector is done but graph is still rolling up. */
export function effectiveGraphPhase(progress, memberJobs) {
  const phase = (progress?.phase || 'assessment').toLowerCase();
  if (phase !== 'assessment' && phase !== 'rollup') {
    return phase;
  }
  const allDone = (memberJobs || []).length > 0
    && (memberJobs || []).every((m) => {
      const st = (m.display_status || m.status || '').toLowerCase();
      return st === 'completed' || st === 'failed';
    });
  if (allDone) {
    return 'linking';
  }
  return phase === 'rollup' ? 'linking' : phase;
}

export function memberProgressDetail(mj) {
  const prog = mj.assessment_progress || {};
  const phaseLabel = prog.sub_phase ? SUB_PHASE_LABELS[prog.sub_phase] || prog.sub_phase : null;

  if (prog.message && prog.message.toLowerCase() !== 'complete') {
    return prog.message;
  }
  if (prog.message && prog.message.toLowerCase() === 'complete') {
    return 'Finishing connector report';
  }
  if (mj.status === 'running' && phaseLabel && prog.current_entity) {
    return `${phaseLabel} · ${prog.current_entity}`;
  }
  if (mj.status === 'running' && phaseLabel && prog.total_entities) {
    const done = prog.completed_entities ?? 0;
    return `${phaseLabel} · ${done}/${prog.total_entities}`;
  }
  if (mj.status === 'running' && phaseLabel) {
    return prog.percent != null ? `${phaseLabel} (${prog.percent}%)` : phaseLabel;
  }
  if (mj.status === 'running' && prog.current_entity) {
    return prog.current_entity;
  }
  if (mj.status === 'running' && prog.total_entities) {
    return `${prog.completed_entities ?? 0}/${prog.total_entities} objects`;
  }
  if (mj.display_status === 'queued') {
    return 'Waiting for a slot (max 2 parallel)';
  }
  return null;
}

export function memberProgressPercent(mj) {
  if (mj.status === 'completed' || mj.status === 'failed') {
    return mj.status === 'completed' ? 100 : 0;
  }
  if (mj.status !== 'running') {
    return 0;
  }
  const prog = mj.assessment_progress || {};
  if (prog.percent != null) {
    return Math.min(100, Math.max(0, Number(prog.percent)));
  }
  const total = prog.total_entities || 0;
  if (total > 0) {
    return Math.min(100, Math.round(((prog.completed_entities || 0) / total) * 100));
  }
  return 5;
}
