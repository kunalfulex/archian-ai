/**
 * Execution plan panel headers — label from step tools, not stale destination metadata.
 */

function normalizePlanConnectorType(connectorType) {
  const raw = String(connectorType || '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_');
  if (raw === 'gsheets' || raw === 'google_sheets') return 'google_sheets';
  if (raw === 'sql' || raw === 'database') return 'sql';
  if (raw === 'salesforce' || raw === 'sf') return 'salesforce';
  return raw;
}

/** User-facing entity fragment (tab name for GSheets, not spreadsheetId::tab). */
export function formatPlanEntityKeyForDisplay(entityKey, connectorType) {
  const key = (entityKey || '').trim();
  if (!key) return '';
  const ct = normalizePlanConnectorType(connectorType);
  if (ct === 'google_sheets' && key.includes('::')) {
    return key.split('::').pop() || key;
  }
  return key;
}

function formatDestinationLine(destination) {
  if (!destination?.display_name) return null;
  const name = destination.display_name;
  const tab = destination.sheet_name;
  const connector = (destination.connector_type || '').replace(/_/g, ' ');
  if (tab) {
    return connector
      ? `${name} — tab ${tab} (${connector})`
      : `${name} — tab ${tab}`;
  }
  return connector ? `${name} (${connector})` : name;
}

export function formatPlanEntityExecutionSide(side) {
  if (!side || typeof side !== 'object') return null;
  const key = (side.entity_key || '').trim();
  if (!key) return null;
  const conn = (side.display_name || '').trim();
  const ct = normalizePlanConnectorType(side.connector_type);
  const catLabel = (side.catalog_label || side.entity_label || '').trim();
  const isGsheetEntity = ct === 'google_sheets' || (key.includes('::') && !ct);

  if (isGsheetEntity) {
    const tab =
      (side.sheet_name || '').trim() ||
      formatPlanEntityKeyForDisplay(key, 'google_sheets');
    const target = tab || key;
    return conn ? `${conn} → tab \`${target}\`` : `tab \`${target}\``;
  }
  if (ct === 'sql') {
    return conn ? `${conn} → \`${key}\`` : `\`${key}\``;
  }
  const entityPart =
    catLabel && catLabel.toLowerCase() !== key.toLowerCase()
      ? `\`${key}\` (${catLabel})`
      : `\`${key}\``;
  return conn ? `${conn} → ${entityPart}` : entityPart;
}

export function planStepToolIds(steps = []) {
  return (steps || [])
    .map((s) => String(s.tool_id || s.step_type || s.parameters?.tool_id || '').toLowerCase())
    .filter(Boolean);
}

const CREATE_STEP_TOOL_MARKERS = [
  'google_sheets_create_from_prompt',
  'create_spreadsheet',
  'create_workbook',
  'create_google_sheet',
];

export function planHasCreateStep(steps = []) {
  const tools = planStepToolIds(steps);
  return tools.some(
    (t) =>
      CREATE_STEP_TOOL_MARKERS.includes(t) ||
      (t.includes('create') &&
        (t.includes('sheet') || t.includes('spreadsheet') || t.includes('workbook'))),
  );
}

/** Mode/intent label for UI — ignore stale classifier `create` when steps won't materialize create. */
export function resolveEffectivePlanMode(steps = [], mode) {
  const normalized = String(mode || '').toLowerCase();
  if (normalized !== 'create' || planHasCreateStep(steps)) {
    return mode;
  }
  const tools = planStepToolIds(steps);
  if (tools.some((t) => t === 'start_migration' || t === 'migrate')) {
    return 'migrate';
  }
  if (tools.some((t) => t === 'map_fields' || t === 'map')) {
    return 'map';
  }
  if (tools.some((t) => t.includes('extract'))) {
    return 'extract';
  }
  return mode;
}

/** Prefer bound plan_contract from execution plan / pending snapshot (post-reconcile). */
export function resolvePlannerPlanContract(meta = {}, pending = {}) {
  const executionPlan = pending?.execution_plan || meta?.execution_plan;
  const fromExecutionMeta =
    executionPlan?.metadata?.plan_contract || executionPlan?.metadata?.planContract;
  return (
    meta?.plan_contract ||
    pending?.classification?.plan_contract ||
    fromExecutionMeta ||
    null
  );
}

/** Destination display fields for plan panel — workbook title from contract, not entity api name. */
export function resolvePlannerDestinationDisplay(meta = {}, pending = {}, steps = []) {
  const cls = pending?.classification || {};
  const contract = resolvePlannerPlanContract(meta, pending) || {};
  const destSide = contract.destination || contract.mapping_target || null;
  const destinationName =
    destSide?.display_name ||
    meta?.destination_name ||
    cls.destination_name ||
    null;
  const destinationSheetName =
    destSide?.sheet_name ||
    meta?.destination_sheet_name ||
    cls.destination_sheet_name ||
    null;
  return {
    planContract: contract,
    destination: meta?.plan_destination || cls.plan_destination || null,
    destinationName,
    destinationSheetName,
    mode: resolveEffectivePlanMode(steps, cls.mode || meta?.mode),
    sourceName: cls.source_name || meta?.source_name,
    sourceEntityKey:
      contract?.source?.entity_key ||
      cls.source_object_api_name ||
      cls.source_table_name ||
      cls.source_sheet_name ||
      meta?.source_entity_key,
    sourceConnectorType:
      contract?.source?.connector_type || cls.source || meta?.source,
  };
}

export function buildPlannerPlanPanelProps(entry = {}) {
  const meta = entry.metadata || {};
  const pending = meta.planner_pending || {};
  const steps = meta.plan_steps_summary || pending.plan_steps_summary || [];
  const display = resolvePlannerDestinationDisplay(meta, pending, steps);
  return {
    steps,
    goalSummary: meta.goal_summary || pending.classification?.goal_summary,
    buildErrorMessage:
      steps.length === 0
        ? 'Planning paused before steps could be assembled. Complete any remaining prompts above or send your request again.'
        : null,
    ...display,
  };
}

export function isDataOpsOnlyPlan(steps, { mode } = {}) {
  if (mode === 'data_ops') {
    const tools = planStepToolIds(steps);
    if (!tools.length) return true;
    return tools.every(
      (t) => t === 'run_data_ops_plan' || t === 'transform' || t.startsWith('data_ops_'),
    );
  }
  const tools = planStepToolIds(steps);
  return (
    tools.length > 0 &&
    tools.every((t) => t === 'run_data_ops_plan' || t === 'transform' || t.startsWith('data_ops_'))
  );
}

export function isCreateOnlyPlan(steps) {
  const tools = planStepToolIds(steps);
  return (
    tools.length > 0 &&
    tools.every(
      (t) =>
        t === 'google_sheets_create_from_prompt' ||
        t === 'create_spreadsheet' ||
        t === 'create_workbook' ||
        t === 'create_google_sheet',
    )
  );
}

/** Pick destination header label for plan endpoint summary (steps keep action verbs). */
export function resolvePlannerDestinationHeader(steps, { mode } = {}) {
  if (isDataOpsOnlyPlan(steps, { mode })) {
    return null;
  }
  const tools = planStepToolIds(steps);
  if (!tools.length && !isCreateOnlyPlan(steps)) {
    return null;
  }
  return 'Destination:';
}

export function resolvePlannerPlanContextLines(steps, meta = {}) {
  const {
    planContract,
    destination,
    destinationName,
    destinationSheetName,
    mode,
    sourceName,
    sourceEntityKey,
    sourceConnectorType,
  } = meta;
  const contract = planContract || {};
  const effectiveMode = resolveEffectivePlanMode(steps, mode);
  const dataOpsOnly = isDataOpsOnlyPlan(steps, { mode: effectiveMode });
  let srcLine = formatPlanEntityExecutionSide(contract.source);
  if (!srcLine && sourceName) {
    const name = String(sourceName).trim();
    const srcCt =
      sourceConnectorType ||
      contract.source?.connector_type ||
      (String(sourceEntityKey || '').includes('::') ? 'google_sheets' : null);
    const entity = formatPlanEntityKeyForDisplay(sourceEntityKey, srcCt);
    srcLine = entity ? `${name} → \`${entity}\`` : name;
  }

  const destLine = dataOpsOnly
    ? null
    : formatPlanEntityExecutionSide(contract.destination) ||
      formatPlanEntityExecutionSide(contract.mapping_target) ||
      formatDestinationLine(destination) ||
      (destinationName
        ? `${destinationName}${destinationSheetName ? ` — tab ${destinationSheetName}` : ''}`
        : null);

  const destHeaderLabel = dataOpsOnly
    ? null
    : resolvePlannerDestinationHeader(steps, { mode: effectiveMode });

  const mapLine =
    dataOpsOnly || !destLine
      ? null
      : formatPlanEntityExecutionSide(contract.mapping_target || contract.destination);

  const sourceHeaderLabel =
    dataOpsOnly && srcLine
      ? 'Work on dataset:'
      : srcLine
        ? 'Source:'
        : null;

  return {
    srcLine,
    sourceHeaderLabel,
    destLine,
    destHeaderLabel,
    mapLine: mapLine && mapLine !== destLine ? mapLine : null,
    mapHeaderLabel: 'Mapping target:',
  };
}

export function buildPlannerPlanReadyMarkdown(steps, meta = {}) {
  const lines = ['**Execution plan** (review and approve to continue)', ''];
  const ctx = resolvePlannerPlanContextLines(steps, meta);

  if (ctx.srcLine && ctx.sourceHeaderLabel) {
    lines.push(`**${ctx.sourceHeaderLabel.replace(/:$/, '')}:** ${ctx.srcLine}`);
  }
  if (ctx.destLine && ctx.destHeaderLabel) {
    lines.push(`**${ctx.destHeaderLabel.replace(/:$/, '')}:** ${ctx.destLine}`);
  }
  if (ctx.srcLine || ctx.destLine) {
    lines.push('');
  }
  (steps || []).forEach((s, i) => {
    lines.push(`${i + 1}. ${s.label || s.step_type || 'Step'}`);
  });
  lines.push('');
  lines.push('_Click **Run plan** below to start._');
  return lines.join('\n');
}
