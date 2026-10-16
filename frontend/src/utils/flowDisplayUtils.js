import { isHiddenExtractionMetadataKey } from './extractionPreviewHiddenKeys';

/** True when value is null, empty string/array, or recursively empty object. */
export function isDeepEmptyValue(value) {
  if (value == null) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (Array.isArray(value)) return value.length === 0 || value.every(isDeepEmptyValue);
  if (typeof value === 'object') {
    const keys = Object.keys(value);
    if (keys.length === 0) return true;
    return keys.every((k) => isDeepEmptyValue(value[k]));
  }
  return false;
}

export function isMigrationRulesEmpty(rules) {
  return rules == null || isDeepEmptyValue(rules);
}

const MIGRATION_RULE_LABELS = {
  extraction_rules: 'Extraction rules',
  mapping_transformation: 'Mapping transformation',
  transformation_rules: 'Mapping transformation (legacy)',
  missing_field_preference: 'Missing field preference',
  source_schema: 'Source schema',
};

export function labelForMigrationRuleKey(key) {
  if (MIGRATION_RULE_LABELS[key]) return MIGRATION_RULE_LABELS[key];
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function isExtractedSchemaEmpty(value) {
  if (value == null) return true;
  if (typeof value === 'string') {
    const t = value.trim();
    if (t === '' || t === '{}' || t === '[]') return true;
    try {
      return isExtractedSchemaEmpty(JSON.parse(t));
    } catch {
      return false;
    }
  }
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
}

export function getSourceTypeKey(flow) {
  if (flow == null || typeof flow !== 'object') {
    return 'file';
  }
  return (flow.source_type || flow.extracted_schema_type || 'file').toLowerCase();
}

export function getDestinationTypeKey(flow) {
  if (flow == null || typeof flow !== 'object') {
    return 'salesforce';
  }
  return (flow.destination_type || 'salesforce').toLowerCase();
}

/** Salesforce & SQL flows support optional incremental sync (watermark-based). */
export function supportsIncrementalSync(flow) {
  const st = getSourceTypeKey(flow);
  return st === 'sql' || st === 'salesforce';
}

/** Salesforce and SQL only (any mix); reverse leg supported on backend. */
export function supportsBidirectionalSync(flow) {
  if (!flow || flow.parent_flow_id) return false;
  const st = getSourceTypeKey(flow);
  const dt = getDestinationTypeKey(flow);
  if (st === 'file' || dt === 'file') return false;
  if (st === 'gsheets' || dt === 'google_sheets') return false;
  return (
    (st === 'salesforce' || st === 'sql') && (dt === 'salesforce' || dt === 'sql')
  );
}

export function formatSourceLabel(flow) {
  const typeKey = getSourceTypeKey(flow);
  const typeLabels = {
    file: 'File Upload',
    sql: 'SQL Database',
    gsheets: 'Google Sheets',
  };
  const baseLabel =
    typeLabels[typeKey] || typeKey.charAt(0).toUpperCase() + typeKey.slice(1);
  const name =
    flow.source_name ||
    (typeKey === 'sql' || typeKey === 'gsheets' ? flow.table_name : null);
  return name ? `${baseLabel} • ${name}` : baseLabel;
}

export function formatDestinationLabel(flow) {
  const typeKey = getDestinationTypeKey(flow);
  const typeLabels = {
    salesforce: 'Salesforce',
    google_sheets: 'Google Sheets',
    sql: 'SQL Database',
  };
  const baseLabel =
    typeLabels[typeKey] || typeKey.charAt(0).toUpperCase() + typeKey.slice(1);
  const name = flow.destination_name;
  return name ? `${baseLabel} • ${name}` : baseLabel;
}

/** Short label for source category (modals, summaries). */
export function formatSourceTypeForDisplay(flow) {
  const k = getSourceTypeKey(flow);
  const labels = {
    file: 'File upload',
    sql: 'SQL database',
    gsheets: 'Google Sheets',
    google_sheets: 'Google Sheets',
    salesforce: 'Salesforce',
  };
  if (labels[k]) return labels[k];
  if (!k) return '—';
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Short label for destination category (modals, summaries). */
export function formatDestinationTypeForDisplay(flow) {
  const k = getDestinationTypeKey(flow);
  const labels = {
    salesforce: 'Salesforce',
    google_sheets: 'Google Sheets',
    sql: 'SQL database',
  };
  if (labels[k]) return labels[k];
  if (!k) return '—';
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Prefer API source_name; fall back to schema table, source_target, or document type label. */
export function getFlowSourceDisplayName(flow, schema) {
  return (
    flow?.source_name ||
    schema?.table_name ||
    flow?.source_target ||
    schema?.document_type ||
    null
  );
}

export function getFlowDestinationDisplayName(flow) {
  return flow?.destination_name || null;
}

/**
 * Turn stored extracted_schema (object or JSON string) into table rows for the flow detail page.
 * @param {unknown} raw
 * @returns {{ rows: Array<{ field: string, value: string }>, fallbackRaw: string | null }}
 */
export function buildSourceDataFormatRows(raw) {
  if (raw == null) {
    return { rows: [], fallbackRaw: null };
  }
  let data = raw;
  if (typeof raw === 'string') {
    const t = raw.trim();
    if (t === '' || t === '{}' || t === '[]') {
      return { rows: [], fallbackRaw: null };
    }
    try {
      data = JSON.parse(t);
    } catch {
      return { rows: [], fallbackRaw: t };
    }
  }
  if (data !== null && typeof data === 'object') {
    const rows = [];
    if (Array.isArray(data)) {
      data.forEach((item, i) => {
        rows.push({ field: `[${i}]`, value: formatSchemaCellValue(item) });
      });
    } else {
      Object.keys(data).forEach((k) => {
        if (isHiddenExtractionMetadataKey(k)) return;
        rows.push({ field: k, value: formatSchemaCellValue(data[k]) });
      });
    }
    return { rows, fallbackRaw: null };
  }
  return { rows: [{ field: '—', value: formatSchemaCellValue(data) }], fallbackRaw: null };
}

/**
 * @param {unknown} v
 * @returns {string}
 */
export function formatSchemaCellValue(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') {
    return JSON.stringify(v, null, 2);
  }
  return String(v);
}
