/**
 * Build display groups for saved flow field_mappings (flat DB rows).
 *
 * Mirrors how chat save-mapping persists data:
 * - Split: multiple rows with the same extracted_field_name → one array in chat → multiple INSERTs.
 * - Combine: multiple rows with different extracted_field_name but same destination → multiple INSERTs.
 *
 * @param {Array<Record<string, unknown>> | null | undefined} fieldMappings
 * @returns {Array<{ kind: 'split' | 'combine' | 'single'; rows: Array<Record<string, unknown>> }>}
 */
export function buildFieldMappingDisplayGroups(fieldMappings) {
  const rows = Array.isArray(fieldMappings) ? fieldMappings : [];
  if (rows.length === 0) return [];

  const withIndex = rows.map((r, idx) => ({
    ...r,
    __order: idx,
    __key: r.id != null ? String(r.id) : `idx:${idx}`,
  }));

  const sourceKey = (r) => String(r.extracted_field_name ?? '').trim();
  const destKey = (r) => {
    const o = String(r.salesforce_object ?? '').trim();
    const f = String(r.salesforce_field ?? '').trim();
    return `${o}\u0000${f}`.toLowerCase();
  };

  /** @type {Map<string, typeof withIndex>} */
  const bySource = new Map();
  /** @type {Map<string, typeof withIndex>} */
  const byDest = new Map();

  for (const r of withIndex) {
    const sk = sourceKey(r);
    if (!bySource.has(sk)) bySource.set(sk, []);
    bySource.get(sk).push(r);

    const dk = destKey(r);
    if (!dk || dk === '\u0000') continue;
    if (!byDest.has(dk)) byDest.set(dk, []);
    byDest.get(dk).push(r);
  }

  for (const list of bySource.values()) {
    list.sort((a, b) => a.__order - b.__order);
  }
  for (const list of byDest.values()) {
    list.sort((a, b) => a.__order - b.__order);
  }

  const used = new Set();
  /** @type {Array<{ kind: 'split' | 'combine' | 'single'; rows: typeof withIndex }>} */
  const groups = [];

  const stripMeta = (row) => {
    const { __order, __key, ...rest } = row;
    return rest;
  };

  for (const r of withIndex) {
    if (used.has(r.__key)) continue;

    const sk = sourceKey(r);
    const splitCandidates = !sk ? [r] : bySource.get(sk) || [];

    if (sk && splitCandidates.length >= 2) {
      for (const x of splitCandidates) used.add(x.__key);
      groups.push({ kind: 'split', rows: splitCandidates.map(stripMeta) });
      continue;
    }

    const dk = destKey(r);
    const combineCandidates = dk && dk !== '\u0000' ? byDest.get(dk) || [] : [];
    if (combineCandidates.length >= 2) {
      for (const x of combineCandidates) used.add(x.__key);
      groups.push({ kind: 'combine', rows: combineCandidates.map(stripMeta) });
      continue;
    }

    used.add(r.__key);
    groups.push({ kind: 'single', rows: [stripMeta(r)] });
  }

  return groups;
}
