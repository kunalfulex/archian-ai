/**
 * Human-readable labels and summaries for migration_rules / extraction_rules JSON.
 * Keep in sync with backend: handlers/prompt_handler.py (filter/transformation/dedup),
 * handlers/file_handler.py (apply_transformations).
 */

/** @type {Record<string, string>} */
const FILTER_OPERATOR_LABELS = {
  equals: 'equals',
  not_equals: 'does not equal',
  greater_than: 'is greater than',
  less_than: 'is less than',
  greater_than_or_equal: 'is at least',
  less_than_or_equal: 'is at most',
  contains: 'contains',
  not_contains: 'does not contain',
  starts_with: 'starts with',
  ends_with: 'ends with',
  is_empty: 'is empty',
  is_not_empty: 'is not empty',
  between: 'is between',
};

/** Deduplication strategy: duplicate rows (not the same as transformation "merge"). */
/** @type {Record<string, string>} */
const DEDUP_STRATEGY_LABELS = {
  keep_first: 'Keep the first occurrence of each duplicate',
  keep_last: 'Keep the last occurrence of each duplicate',
  merge: 'Merge duplicate rows into one record',
};

/**
 * Human-readable description of a delimiter for split vs merge actions.
 * @param {string} raw
 * @param {'split' | 'merge'} kind
 * @returns {string}
 */
export function describeDelimiterHumanReadable(raw, kind) {
  const s = raw === undefined || raw === null ? '' : String(raw);
  const split = kind === 'split';

  if (s === ' ') {
    return split
      ? 'Separated by spaces between words.'
      : 'Joined with spaces between fields.';
  }
  if (s === ',') {
    return split ? 'Separated by commas.' : 'Joined with commas.';
  }
  if (s === ', ') {
    return split
      ? 'Separated by commas (comma and space).'
      : 'Joined with a comma and space.';
  }
  if (s === '\t') {
    return split ? 'Separated by tab characters.' : 'Joined with tab characters.';
  }
  if (s === '|') {
    return split ? 'Separated by vertical bars (|).' : 'Joined with vertical bars (|).';
  }
  if (s === '') {
    return split ? 'No separator specified.' : 'No separator between parts.';
  }
  if (s.length === 1) {
    return split
      ? `Separated by “${s}”.`
      : `Joined with “${s}” between parts.`;
  }
  return split
    ? `Separated by: ${JSON.stringify(s)}`
    : `Joined with: ${JSON.stringify(s)}`;
}

/**
 * @param {string | null | undefined} action
 * @returns {string}
 */
export function formatTransformationActionLabel(action) {
  if (action == null || action === '') return '—';
  const a = String(action).toLowerCase();
  if (a === 'keep') return 'Keep';
  if (a === 'exclude') return 'Exclude';
  if (a === 'drop') return 'Drop';
  if (a === 'merge') return 'Merge fields';
  if (a === 'combine') return 'Combine fields';
  if (a === 'split_address') return 'Split address';
  if (a.startsWith('split_')) {
    const n = a.replace(/^split_/, '');
    if (/^\d+$/.test(n)) return `Split into ${n} parts`;
    return String(action);
  }
  if (a === 'split') return 'Split';
  return String(action);
}

/**
 * @param {string | null | undefined} operationType
 * @returns {string}
 */
export function formatOperationTypeLabel(operationType) {
  if (operationType == null || operationType === '') return '—';
  const t = String(operationType).toLowerCase();
  if (t === 'extraction') return 'Extraction';
  if (t === 'mapping') return 'Mapping';
  return String(operationType);
}

/**
 * Source column names for a transformation rule (matches backend normalize_transformation_rule).
 * Merge/combine use source_fields; prepend/append may use source_field, field, or source_fields[0].
 * @param {Record<string, unknown>} rule
 * @returns {string[]}
 */
export function collectTransformationSourceFieldNames(rule) {
  if (!rule || typeof rule !== 'object') return [];
  const action = String(rule.action || '').toLowerCase();

  if (action === 'merge' || action === 'combine') {
    let srcs = rule.source_fields;
    if (srcs == null) srcs = rule.source_field;
    if (typeof srcs === 'string' && srcs.trim()) return [srcs.trim()];
    if (Array.isArray(srcs)) {
      return srcs
        .map((x) => (x != null && x !== '' ? String(x).trim() : ''))
        .filter(Boolean);
    }
    return [];
  }

  if (action === 'prepend' || action === 'append') {
    const scalar = rule.source_field ?? rule.field;
    if (typeof scalar === 'string' && scalar.trim()) return [scalar.trim()];
    const srcs = rule.source_fields;
    if (Array.isArray(srcs) && srcs.length === 1) {
      const x = srcs[0];
      if (x != null && String(x).trim()) return [String(x).trim()];
    }
    if (typeof srcs === 'string' && srcs.trim()) return [srcs.trim()];
    return [];
  }

  const sf = rule.source_field;
  if (Array.isArray(sf)) {
    return sf
      .map((x) => (x != null && x !== '' ? String(x).trim() : ''))
      .filter(Boolean);
  }
  if (sf != null && sf !== '') return [String(sf).trim()];
  return [];
}

/**
 * @param {Record<string, unknown>} rule
 * @returns {string}
 */
export function formatSourceFieldDisplay(rule) {
  const names = collectTransformationSourceFieldNames(rule);
  if (names.length === 0) return '—';
  return names.join(', ');
}

/** @param {Record<string, unknown>} rule */
function resolveTransformationOutputField(rule) {
  const of = rule.output_field ?? rule.target_field;
  if (of != null && of !== '') return String(of);
  return null;
}

/**
 * @param {Record<string, unknown>} rule
 * @returns {string}
 */
export function formatOutputFieldDisplay(rule) {
  const action = String(rule.action || '').toLowerCase();
  if (action === 'exclude') return '—';
  if (action === 'merge' || action === 'combine') {
    return resolveTransformationOutputField(rule) || '—';
  }
  if (action.startsWith('split_') || action === 'split_address') {
    const outs = rule.output_fields;
    if (Array.isArray(outs) && outs.length) return outs.join(', ');
  }
  const resolved = resolveTransformationOutputField(rule);
  if (resolved) return resolved;
  if (Array.isArray(rule.output_fields) && rule.output_fields.length) {
    return rule.output_fields.join(', ');
  }
  return '—';
}

/**
 * Secondary line under Output: plain-language split / merge delimiters.
 * @param {Record<string, unknown>} rule
 * @returns {string | null}
 */
export function formatTransformationDetailLine(rule) {
  const action = String(rule.action || '').toLowerCase();
  if (action.startsWith('split_') || action === 'split_address') {
    if (action === 'split_address') {
      return 'Parsed into address components (street, city, state, postal code, etc.).';
    }
    const sc = rule.split_char;
    if (sc !== undefined && sc !== null && sc !== '') {
      return describeDelimiterHumanReadable(String(sc), 'split');
    }
    return null;
  }
  if (action === 'merge' || action === 'combine') {
    const sep =
      rule.separator !== undefined && rule.separator !== null ? rule.separator : ' ';
    return describeDelimiterHumanReadable(String(sep), 'merge');
  }
  return null;
}

/**
 * @param {unknown[]} fields
 * @returns {unknown[]}
 */
export function sortTransformationFieldsByOrder(fields) {
  if (!Array.isArray(fields)) return [];
  return [...fields].sort((a, b) => {
    const ao =
      a && typeof a === 'object' && typeof a.order === 'number' ? a.order : 0;
    const bo =
      b && typeof b === 'object' && typeof b.order === 'number' ? b.order : 0;
    return ao - bo;
  });
}

/**
 * @param {Record<string, unknown>} cond
 * @returns {string}
 */
export function formatFilterConditionLine(cond) {
  if (!cond || typeof cond !== 'object') return String(cond);
  const field = cond.field != null ? String(cond.field) : '?';
  const op = String(cond.operator || '').toLowerCase();
  const label = FILTER_OPERATOR_LABELS[op] || cond.operator || op;
  const val = cond.value;

  if (op === 'is_empty' || op === 'is_not_empty') {
    return `${field} ${label}`;
  }
  if (op === 'between' && Array.isArray(val) && val.length >= 2) {
    return `${field} ${label} ${String(val[0])} and ${String(val[1])}`;
  }
  if (val !== undefined) {
    return `${field} ${label} ${typeof val === 'string' ? JSON.stringify(val) : String(val)}`;
  }
  return `${field} ${label}`;
}

/**
 * @param {Record<string, unknown> | null | undefined} filterRules
 * @returns {{ logic: string, lines: string[] } | null}
 */
export function formatFilterRulesBlock(filterRules) {
  if (!filterRules || typeof filterRules !== 'object') return null;
  const logic = String(filterRules.logic || 'AND').toUpperCase();
  const conditions = filterRules.conditions;
  if (!Array.isArray(conditions) || conditions.length === 0) return null;
  const lines = conditions.map((c) => formatFilterConditionLine(c));
  return { logic, lines };
}

/**
 * @param {string} logic AND | OR
 * @returns {string}
 */
export function formatFilterLogicDescription(logic) {
  const l = String(logic || 'AND').toUpperCase();
  if (l === 'OR') return 'Match if any of the following are true';
  return 'Match only if all of the following are true';
}

/**
 * @param {Record<string, unknown> | null | undefined} dedup
 * @returns {{ enabled: boolean, fieldsLine: string, strategyLine: string, summaryLines: string[] } | null}
 */
export function formatDeduplicationRulesBlock(dedup) {
  if (!dedup || typeof dedup !== 'object') return null;
  const enabled = dedup.enabled === true;
  const fields = Array.isArray(dedup.fields) ? dedup.fields : [];
  const strategy = String(dedup.strategy || 'keep_first').toLowerCase();
  const strategyLine = DEDUP_STRATEGY_LABELS[strategy] || `Strategy: ${strategy}`;
  const fieldsLine =
    fields.length > 0
      ? `Compare records using: ${fields.join(', ')}`
      : 'Compare records using all available fields (when no keys specified)';
  const summaryLines = [
    enabled ? 'Deduplication is turned on.' : 'Deduplication is turned off.',
    fieldsLine,
    strategyLine,
  ];
  return { enabled, fieldsLine, strategyLine, summaryLines };
}

/**
 * Brief lines for data_cleanup_rules (field -> type / rules object).
 * @param {Record<string, unknown> | null | undefined} cleanup
 * @returns {string[] | null}
 */
export function formatDataCleanupRulesLines(cleanup) {
  if (!cleanup || typeof cleanup !== 'object') return null;
  const keys = Object.keys(cleanup);
  if (keys.length === 0) return null;
  return keys.map((field) => {
    const rules = cleanup[field];
    if (rules && typeof rules === 'object' && rules.type) {
      return `${field}: format / validation type “${String(rules.type)}”`;
    }
    return `${field}: ${JSON.stringify(rules)}`;
  });
}
