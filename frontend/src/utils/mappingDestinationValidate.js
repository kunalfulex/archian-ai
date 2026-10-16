/**
 * Client-side mapping validation against destination schema.
 * Mirrors backend utils/mapping_destination_validate.py (defense in depth for UI edits).
 */

const STOP = new Set(['the', 'a', 'an', 'field', 'column', 'value', 'data']);

function normTokens(text) {
  const raw = (text || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  return new Set(raw.split(/\s+/).filter((t) => t && !STOP.has(t)));
}

export function sourceFieldMatchesDestinationColumn(sourceField, destColumn) {
  const src = (sourceField || '').trim();
  const col = (destColumn || '').trim();
  if (!src || !col) return false;

  const srcCompact = src.toLowerCase().replace(/[^a-z0-9]/g, '');
  const colCompact = col.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (srcCompact && colCompact && (srcCompact.includes(colCompact) || colCompact.includes(srcCompact))) {
    return true;
  }

  const srcTokens = normTokens(src.replace(/_/g, ' '));
  const colTokens = normTokens(col.replace(/_/g, ' '));
  if ([...srcTokens].some((t) => colTokens.has(t))) return true;

  const aliasGroups = [
    new Set(['phone', 'mobile', 'tel', 'telephone', 'cell', 'fax']),
    new Set(['email', 'mail']),
    new Set(['firstname', 'fname', 'given']),
    new Set(['lastname', 'lname', 'surname', 'family']),
    new Set(['company', 'organization', 'org', 'account']),
    new Set(['street', 'address', 'line1', 'line2']),
    new Set(['city', 'town']),
    new Set(['state', 'province', 'region']),
    new Set(['zip', 'postal', 'postcode']),
    new Set(['country', 'nation']),
  ];

  for (const group of aliasGroups) {
    const srcHit = [...srcTokens].some((t) => group.has(t)) || group.has(srcCompact);
    const colHit = [...colTokens].some((t) => group.has(t)) || group.has(colCompact);
    if (srcHit && colHit) return true;
  }
  return false;
}

function sfEntryExists(entry, objectsStructure) {
  const obj = (entry?.object || '').trim();
  const field = (entry?.field || '').trim();
  if (!obj || !field) return false;
  const fields = objectsStructure?.[obj];
  if (!fields) return false;
  return Object.prototype.hasOwnProperty.call(fields, field);
}

export function filterSalesforceMappingToDestinationSchema(mapping, objectsStructure) {
  if (!mapping) return {};
  if (!objectsStructure) return { ...mapping };

  const filtered = {};
  for (const [sourceField, info] of Object.entries(mapping)) {
    if (Array.isArray(info)) {
      const kept = info.filter((item) => item && typeof item === 'object' && sfEntryExists(item, objectsStructure));
      if (kept.length) filtered[sourceField] = kept;
    } else if (info && typeof info === 'object' && sfEntryExists(info, objectsStructure)) {
      filtered[sourceField] = info;
    }
  }
  return filtered;
}

function sheetColumnsIndex(sheetsStructure) {
  const out = {};
  for (const [sheetName, meta] of Object.entries(sheetsStructure || {})) {
    if (!meta || typeof meta !== 'object') continue;
    const cols = meta.columns || {};
    const colNames = typeof cols === 'object' && !Array.isArray(cols)
      ? Object.keys(cols)
      : Array.isArray(cols)
        ? cols.map((c) => (typeof c === 'object' ? c.name : String(c)))
        : [];
    out[(sheetName || '').trim().toLowerCase()] = {};
    for (const name of colNames) {
      if (!name) continue;
      out[(sheetName || '').trim().toLowerCase()][String(name).trim().toLowerCase()] = String(name).trim();
    }
  }
  return out;
}

function resolveSheetColumn(entry, sheetIndex, defaultSheet) {
  const sheet = (entry?.sheet || defaultSheet || '').trim();
  const column = (entry?.column || '').trim();
  if (!sheet || !column) return [null, null];
  const canonical = sheetIndex[sheet.toLowerCase()]?.[column.toLowerCase()];
  return canonical ? [sheet, canonical] : [null, null];
}

function validSheetEntry(sourceField, entry, sheetIndex, defaultSheet) {
  const [sheet, column] = resolveSheetColumn(entry, sheetIndex, defaultSheet);
  if (!sheet || !column) return false;
  return sourceFieldMatchesDestinationColumn(sourceField, column);
}

export function filterGoogleSheetsMappingToDestinationSchema(mapping, sheetsStructure, { defaultSheet = null } = {}) {
  if (!mapping) return {};
  if (!sheetsStructure) return { ...mapping };

  const sheetIndex = sheetColumnsIndex(sheetsStructure);
  const resolvedDefault = defaultSheet || Object.keys(sheetsStructure)[0] || null;
  const filtered = {};

  for (const [sourceField, info] of Object.entries(mapping)) {
    if (Array.isArray(info)) {
      const kept = info.filter(
        (item) => item && typeof item === 'object' && validSheetEntry(sourceField, item, sheetIndex, resolvedDefault),
      );
      if (kept.length) filtered[sourceField] = kept;
    } else if (info && typeof info === 'object' && validSheetEntry(sourceField, info, sheetIndex, resolvedDefault)) {
      const [, column] = resolveSheetColumn(info, sheetIndex, resolvedDefault);
      filtered[sourceField] = column ? { ...info, column } : info;
    }
  }
  return filtered;
}

function tableColumnsIndex(tablesStructure) {
  const out = {};
  for (const [tableName, meta] of Object.entries(tablesStructure || {})) {
    if (!meta || typeof meta !== 'object') continue;
    const cols = meta.columns || {};
    const colNames = typeof cols === 'object' && !Array.isArray(cols)
      ? Object.keys(cols)
      : [];
    out[(tableName || '').trim().toLowerCase()] = {};
    for (const name of colNames) {
      if (!name) continue;
      out[(tableName || '').trim().toLowerCase()][String(name).trim().toLowerCase()] = String(name).trim();
    }
  }
  return out;
}

function resolveTableColumn(entry, tableIndex, primaryTable) {
  const table = (entry?.table || primaryTable || '').trim();
  const column = (entry?.column || '').trim();
  if (!table || !column) return [null, null];
  const canonical = tableIndex[table.toLowerCase()]?.[column.toLowerCase()];
  return canonical ? [table, canonical] : [null, null];
}

export function filterSqlMappingToDestinationSchema(mapping, tablesStructure, { primaryTable = null } = {}) {
  if (!mapping) return {};
  if (!tablesStructure) return { ...mapping };

  const tableIndex = tableColumnsIndex(tablesStructure);
  const resolvedPrimary = primaryTable || Object.keys(tablesStructure)[0] || null;
  const filtered = {};

  for (const [sourceField, info] of Object.entries(mapping)) {
    if (Array.isArray(info)) {
      const kept = [];
      for (const item of info) {
        if (!item || typeof item !== 'object') continue;
        const [table, column] = resolveTableColumn(item, tableIndex, resolvedPrimary);
        if (table && column && sourceFieldMatchesDestinationColumn(sourceField, column)) {
          kept.push({ ...item, table, column });
        }
      }
      if (kept.length) filtered[sourceField] = kept;
    } else if (info && typeof info === 'object') {
      const [table, column] = resolveTableColumn(info, tableIndex, resolvedPrimary);
      if (table && column && sourceFieldMatchesDestinationColumn(sourceField, column)) {
        filtered[sourceField] = { ...info, table, column };
      }
    }
  }
  return filtered;
}

/** Filter mapping before migration based on destination type and objectFieldMap. */
export function filterMappingToDestinationSchema(mapping, destination, destinationSchema, { primaryTable = null, defaultSheet = null } = {}) {
  if (!mapping || !destinationSchema) return mapping || {};

  switch (destination) {
    case 'salesforce':
      return filterSalesforceMappingToDestinationSchema(mapping, destinationSchema);
    case 'google_sheets':
      return filterGoogleSheetsMappingToDestinationSchema(mapping, destinationSchema, { defaultSheet });
    case 'sql':
      return filterSqlMappingToDestinationSchema(mapping, destinationSchema, { primaryTable });
    default:
      return mapping;
  }
}
