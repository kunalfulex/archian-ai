/**
 * Helpers for flow field-mapping editor (Phase 1: non–file-upload flows).
 */

/** First non-empty source query string from stored schema + catalog (and legacy extracted_schema keys). */
export function coalesceSourceSqlQuery(schema, catalog) {
  const pick = (v) => {
    if (v == null) return '';
    const s = typeof v === 'string' ? v : String(v);
    return s.length ? s : '';
  };
  const fromCol = pick(schema?.sql_query);
  if (fromCol) return fromCol;
  const fromCatalog = pick(catalog?.source_sql_query);
  if (fromCatalog) return fromCatalog;
  const ex = schema?.extracted_schema;
  if (ex && typeof ex === 'object' && !Array.isArray(ex)) {
    for (const key of ['sql_query', 'query', 'soql', 'SOQL']) {
      const q = pick(ex[key]);
      if (q) return q;
    }
  }
  return '';
}

/** Build a flat object for POST /map-fields (keys only; values empty strings). */
export function flattenExtractedSchemaForMapFields(extractedSchema) {
  if (!extractedSchema || typeof extractedSchema !== 'object' || Array.isArray(extractedSchema)) {
    return { _field: '' };
  }
  const out = {};
  const walk = (node, prefix) => {
    if (!node || typeof node !== 'object' || Array.isArray(node)) return;
    const keys = Object.keys(node);
    if (keys.length === 0) {
      if (prefix) out[prefix] = '';
      return;
    }
    for (const k of keys) {
      if (k.startsWith('_')) continue;
      const path = prefix ? `${prefix}.${k}` : k;
      const v = node[k];
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        walk(v, path);
      } else {
        out[path] = '';
      }
    }
  };
  walk(extractedSchema, '');
  return Object.keys(out).length ? out : { _field: '' };
}

/** Collect selectable source field names from extracted_schema (same idea as backend). */
export function collectSourceFieldOptions(extractedSchema) {
  const allowed = new Set();
  if (!extractedSchema || typeof extractedSchema !== 'object') return [];

  const walk = (d, prefix = '') => {
    for (const k of Object.keys(d)) {
      if (k.startsWith('_')) continue;
      const path = prefix ? `${prefix}.${k}` : k;
      const v = d[k];
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        walk(v, path);
      } else {
        allowed.add(path);
        allowed.add(k);
      }
    }
  };
  walk(extractedSchema);
  return Array.from(allowed).sort((a, b) => a.localeCompare(b));
}

/** Normalize POST /map-fields response to objectFieldMap shape for InlineMapping-style selects. */
export function destinationSchemaFromMapFieldsResponse(destination, data) {
  if (!data) return {};
  const d = (destination || 'salesforce').toLowerCase();
  if (d === 'salesforce') return data.available_objects || {};
  if (d === 'google_sheets') return data.available_sheets || {};
  if (d === 'sql') return data.available_tables || {};
  return {};
}

/**
 * Build the same nested shape InlineMapping uses (available_objects / available_sheets / tables)
 * purely from current editor rows — mirrors what is stored in field_mappings, no /map-fields call.
 */
export function expandDestinationSchemaFromRows(rows, destination) {
  const d = (destination || 'salesforce').toLowerCase();
  const schema = {};
  for (const r of rows || []) {
    const obj = (r.salesforce_object || '').trim();
    const fld = (r.salesforce_field || '').trim();
    if (!obj) continue;
    if (d === 'google_sheets' || d === 'sql') {
      if (!schema[obj]) schema[obj] = { columns: {} };
      if (!schema[obj].columns) schema[obj].columns = {};
      if (fld) schema[obj].columns[fld] = {};
    } else {
      if (!schema[obj]) schema[obj] = {};
      if (fld) schema[obj][fld] = { type: '' };
    }
  }
  return schema;
}

/**
 * Merge API catalog (full destination describe) with row-derived schema so existing
 * mappings always resolve and users see all known objects/sheets/tables + fields.
 */
export function mergeDestinationSchemas(catalogSchema, rowsSchema, destination) {
  const d = (destination || 'salesforce').toLowerCase();
  const base =
    catalogSchema && typeof catalogSchema === 'object'
      ? JSON.parse(JSON.stringify(catalogSchema))
      : {};
  const rows = rowsSchema && typeof rowsSchema === 'object' ? rowsSchema : {};

  if (d === 'google_sheets' || d === 'sql') {
    for (const [name, block] of Object.entries(rows)) {
      if (!base[name]) base[name] = { columns: {} };
      if (!base[name].columns) base[name].columns = {};
      const cols = (block && block.columns) || {};
      Object.assign(base[name].columns, cols);
    }
    return base;
  }

  for (const [obj, fields] of Object.entries(rows)) {
    if (!fields || typeof fields !== 'object') continue;
    if (!base[obj]) base[obj] = {};
    for (const [fk, fv] of Object.entries(fields)) {
      if (fk === 'columns') continue;
      if (!(fk in base[obj])) {
        base[obj][fk] = fv;
      }
    }
  }
  return base;
}

/** Build merged destination blob from GET /flow/:id/mapping-catalog + current rows. */
export function mergedDestinationSchemaFromCatalog(catalogResponse, rows, destination) {
  const catalogPart = destinationSchemaFromMapFieldsResponse(destination, catalogResponse || {});
  const rowsPart = expandDestinationSchemaFromRows(rows, destination);
  return mergeDestinationSchemas(catalogPart, rowsPart, destination);
}

/** One row per DB field_mapping record. */
export function rowsFromFieldMappings(fieldMappings) {
  if (!fieldMappings || !fieldMappings.length) {
    return [
      {
        extracted_field_name: '',
        salesforce_object: '',
        salesforce_field: '',
        lookup_of: '',
        is_duplicate_check: false,
        is_required: false,
      },
    ];
  }
  return fieldMappings.map((m) => ({
    extracted_field_name: m.extracted_field_name || '',
    salesforce_object: m.salesforce_object || '',
    salesforce_field: m.salesforce_field || '',
    lookup_of: m.lookup_of || '',
    is_duplicate_check: !!m.is_duplicate_check,
    is_required: !!m.is_required,
  }));
}

/** Build PATCH body; omits empty rows. */
export function rowsToPatchPayload(rows) {
  const mappings = [];
  for (const r of rows) {
    const src = (r.extracted_field_name || '').trim();
    const obj = (r.salesforce_object || '').trim();
    const fld = (r.salesforce_field || '').trim();
    if (!src && !obj && !fld) continue;
    mappings.push({
      extracted_field_name: src,
      salesforce_object: obj,
      salesforce_field: fld,
      lookup_of: (r.lookup_of || '').trim() || null,
      is_duplicate_check: !!r.is_duplicate_check,
      is_required: !!r.is_required,
    });
  }
  return { mappings };
}

export function primarySecondaryLabels(destination) {
  const d = (destination || 'salesforce').toLowerCase();
  if (d === 'salesforce') return { primary: 'Salesforce object', secondary: 'Salesforce field', pk: 'object', sk: 'field' };
  if (d === 'google_sheets') return { primary: 'Sheet', secondary: 'Column', pk: 'sheet', sk: 'column' };
  if (d === 'sql') return { primary: 'Table', secondary: 'Column', pk: 'table', sk: 'column' };
  return { primary: 'Target', secondary: 'Field', pk: 'object', sk: 'field' };
}

/** Secondary options for react-select from destination schema blob. */
export function getSecondaryOptions(destination, destinationSchema, primaryValue) {
  if (!primaryValue || !destinationSchema || !destinationSchema[primaryValue]) return [];
  const d = (destination || 'salesforce').toLowerCase();
  const block = destinationSchema[primaryValue];
  if (d === 'salesforce') {
    return Object.keys(block).map((name) => ({ label: name, value: name }));
  }
  const cols = block.columns || {};
  return Object.keys(cols).map((name) => ({ label: name, value: name }));
}

export function getPrimaryOptions(destination, destinationSchema) {
  if (!destinationSchema) return [];
  return Object.keys(destinationSchema).map((k) => ({ label: k, value: k }));
}

/**
 * For Salesforce flows: map source field name (flattened column key, e.g. AccountName) → tooltip text.
 * Built from mapping rows that have lookup_of — explains that the SOQL path differs from the list label.
 */
export function lookupFlattenTooltipBySourceField(rows) {
  const map = {};
  for (const r of rows || []) {
    const name = (r.extracted_field_name || '').trim();
    const lo = (r.lookup_of || '').trim();
    const obj = (r.salesforce_object || '').trim();
    const fld = (r.salesforce_field || '').trim();
    if (!name || !lo || !obj || !fld) continue;
    map[name] =
      `Related field via ${lo}. SOQL queries ${obj}.${fld} through a relationship; "${name}" is the flattened column name in extracted data.`;
  }
  return map;
}
