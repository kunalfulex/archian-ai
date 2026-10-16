/**
 * Strip invalid SQL lookup_of values before migration.
 * lookup_of is only for cross-table FK relationships, not same-table columns.
 */

const INTERNAL_TABLE_NAMES = new Set([
  'extracted_schemas',
  'sql_extracted_schemas',
  'gsheets_extracted_schemas',
]);

function parseLookupOf(lookupOf) {
  if (!lookupOf || typeof lookupOf !== 'string') return null;
  const s = lookupOf.trim();
  const dot = s.lastIndexOf('.');
  if (dot <= 0) return null;
  const childTable = s.slice(0, dot).trim();
  const fkColumn = s.slice(dot + 1).trim();
  if (!childTable || !fkColumn) return null;
  return { childTable, fkColumn };
}

export function shouldDropSqlLookupOf(entryTable, lookupOf, primaryTable) {
  const lo = (lookupOf || '').trim();
  if (!lo) return { drop: true, reason: 'empty' };

  const parsed = parseLookupOf(lo);
  if (!parsed) return { drop: true, reason: 'unparseable' };

  const table = (entryTable || '').trim();
  const { childTable } = parsed;

  if (INTERNAL_TABLE_NAMES.has(childTable.toLowerCase())) {
    return { drop: true, reason: `internal_table_ref:${childTable}` };
  }
  if (table && table.toLowerCase() === childTable.toLowerCase()) {
    return { drop: true, reason: 'same_table_as_lookup_child' };
  }
  if (primaryTable) {
    const pt = primaryTable.trim().toLowerCase();
    if (table.toLowerCase() === pt && childTable.toLowerCase() === pt) {
      return { drop: true, reason: 'primary_table_self_lookup' };
    }
  }
  return { drop: false, reason: '' };
}

function sanitizeItem(item, primaryTable, fieldKey, index, dropped) {
  if (!item || typeof item !== 'object') return item;

  const next = { ...item };
  let table = (next.table || next.object || next.sheet || '').trim();

  if (INTERNAL_TABLE_NAMES.has(table.toLowerCase()) && primaryTable) {
    next.table = primaryTable;
    table = primaryTable;
  }

  const { drop, reason } = shouldDropSqlLookupOf(table, next.lookup_of, primaryTable);
  if (drop && (next.lookup_of || '').trim()) {
    const label = index != null ? `${fieldKey}[${index}]` : fieldKey;
    dropped.push({ field: label, lookup_of: next.lookup_of, reason });
    delete next.lookup_of;
  }
  return next;
}

/** @returns {{ mapping: object, dropped: object[] }} */
export function sanitizeSqlMappingForMigration(mapping, primaryTable) {
  if (!mapping || typeof mapping !== 'object') {
    return { mapping: mapping || {}, dropped: [] };
  }

  const dropped = [];
  const out = {};

  Object.entries(mapping).forEach(([fieldKey, config]) => {
    if (Array.isArray(config)) {
      out[fieldKey] = config.map((item, idx) =>
        sanitizeItem(item, primaryTable, fieldKey, idx, dropped),
      );
    } else if (config && typeof config === 'object') {
      out[fieldKey] = sanitizeItem(config, primaryTable, fieldKey, null, dropped);
    } else {
      out[fieldKey] = config;
    }
  });

  return { mapping: out, dropped };
}

export function logSqlMappingDebug(mapping, primaryTable, dropped = []) {
  if (process.env.NODE_ENV === 'production') return;

  const entries = [];
  let lookupCount = 0;
  Object.entries(mapping || {}).forEach(([field, config]) => {
    const items = Array.isArray(config) ? config : [config];
    items.forEach((item) => {
      if (!item || typeof item !== 'object') return;
      const lo = (item.lookup_of || '').trim();
      if (lo) lookupCount += 1;
      entries.push({
        field,
        table: item.table || item.object,
        column: item.column || item.field,
        lookup_of: lo || null,
      });
    });
  });

  console.debug('[SQL_MIGRATE_DEBUG] mapping payload', {
    primary_table: primaryTable,
    field_count: Object.keys(mapping || {}).length,
    entries_with_lookup_of: lookupCount,
    entries: entries.slice(0, 25),
    dropped,
  });
}
