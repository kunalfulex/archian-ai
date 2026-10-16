/**
 * Mirrors backend utils/soql_parse.validate_select_query_for_mapping:
 * lightweight SELECT ... FROM ... shape check before API / remote execution.
 */

const SOQL_CLAUSE_AFTER_OBJECT =
  /^(where|order\s+by|limit|group\s+by|offset|having|for|with|using)\b/i;

const INVALID_FIRST_FROM = new Set([
  'limit',
  'order',
  'group',
  'offset',
  'having',
  'union',
  'where',
  'inner',
  'left',
  'right',
  'full',
  'cross',
  'join',
  'straight',
]);

/** @returns {[string|null, string|null]} [selectList, suffixFromFROM] */
export function splitSelectAndSuffix(query) {
  const s = String(query).trim();
  if (!s) return [null, null];
  const m = s.match(/^\s*select\s+/i);
  if (!m) return [null, null];
  let i = m[0].length;
  let depth = 0;
  const selectStart = i;
  let inQuote = false;
  let quoteCh = '';
  while (i < s.length) {
    const ch = s[i];
    if (inQuote) {
      if (ch === quoteCh && (i === 0 || s[i - 1] !== '\\')) inQuote = false;
      i += 1;
      continue;
    }
    if (ch === "'" || ch === '"') {
      inQuote = true;
      quoteCh = ch;
      i += 1;
      continue;
    }
    if (ch === '(') depth += 1;
    else if (ch === ')') depth = Math.max(0, depth - 1);
    else if (
      depth === 0 &&
      s.slice(i, i + 4).toLowerCase() === 'from' &&
      (i === 0 || !/[a-z0-9]/i.test(s[i - 1]))
    ) {
      return [s.slice(selectStart, i).trim(), s.slice(i).replace(/^\s+/, '')];
    }
    i += 1;
  }
  return [null, null];
}

/**
 * @param {string} query
 * @param {{ strictSoqlFrom?: boolean }} [options]
 * @returns {string|null} error message, or null if OK
 */
export function validateMappingSelectQuery(query, options = {}) {
  const strictSoqlFrom = !!options.strictSoqlFrom;
  const s = String(query || '')
    .trim()
    .replace(/;+\s*$/, '');
  if (!s) return 'Query cannot be empty.';

  const [sel, suffix] = splitSelectAndSuffix(s);
  if (sel == null || suffix == null) {
    return (
      'Could not parse the query. Use one SELECT ... FROM ... statement with matching parentheses and quotes.'
    );
  }

  let innerCheck = sel.trim();
  if (innerCheck.toUpperCase().startsWith('DISTINCT ')) {
    innerCheck = innerCheck.slice(9).trim();
  }
  if (!innerCheck) return 'The SELECT list cannot be empty.';
  if (innerCheck === '*') {
    return 'Replace SELECT * with an explicit column list before saving.';
  }

  const suf = suffix.trim();
  if (!/^\s*from\s+/i.test(suf)) {
    return 'Could not find a valid FROM clause after the SELECT list.';
  }

  const rest = suf.replace(/^\s*from\s+/i, '').trim();
  if (!rest) return 'Missing table or Salesforce object name after FROM.';

  const c0 = rest[0];
  if (c0 === '(' || c0 === '"' || c0 === "'" || c0 === '[' || c0 === '`') {
    return null;
  }

  const tm = rest.match(/^[^\s,]+/);
  if (!tm) return 'Invalid syntax after FROM.';

  let rawTok = tm[0];
  let tok = rawTok.toLowerCase();
  if (tok.endsWith(',')) tok = tok.slice(0, -1);
  const key = tok.replace(/[`"[\]]/g, '').split('.')[0];

  if (INVALID_FIRST_FROM.has(key)) {
    if (key === 'limit') {
      return (
        'The query is missing a table or object name between FROM and LIMIT. ' +
        'Example: SELECT Id, Name FROM Contact LIMIT 10.'
      );
    }
    return `Invalid query: add a table or object name after FROM before ${rawTok.toUpperCase()}.`;
  }

  if (strictSoqlFrom) {
    const suf = suffix.trim();
    const fromRest = suf.replace(/^\s*from\s+/i, '').trim();
    if (
      fromRest &&
      fromRest[0] !== '(' &&
      fromRest[0] !== '"' &&
      fromRest[0] !== "'" &&
      fromRest[0] !== '[' &&
      fromRest[0] !== '`'
    ) {
      const objM = fromRest.match(/^([a-zA-Z][a-zA-Z0-9_]*)\s*/);
      if (objM) {
        const afterObj = fromRest.slice(objM[0].length).trim();
        if (afterObj && !SOQL_CLAUSE_AFTER_OBJECT.test(afterObj)) {
          return (
            'After the object name in FROM, use only SOQL clauses (WHERE, ORDER BY, LIMIT, …) — ' +
            'not an extra label or typo. Example: SELECT Id FROM Contact WHERE LastName != null'
          );
        }
      }
    }
  }

  return null;
}
