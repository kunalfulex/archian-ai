/**
 * Pick the record with the most non-empty fields for mapping sample values.
 * Mirrors backend utils/mapping_sample.py pick_most_complete_record.
 */

function filledValueCount(record) {
  if (!record || typeof record !== 'object') return 0;
  let score = 0;
  for (const v of Object.values(record)) {
    if (v == null) continue;
    if (typeof v === 'string' && !v.trim()) continue;
    if (Array.isArray(v) && v.length === 0) continue;
    if (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0) continue;
    score += 1;
  }
  return score;
}

export function pickMostCompleteRecord(records, { preferred = null } = {}) {
  if (records && !Array.isArray(records) && typeof records === 'object') {
    return { ...records };
  }
  if (!Array.isArray(records) || records.length === 0) {
    return {};
  }

  let best = {};
  let bestScore = -1;
  for (const item of records) {
    if (!item || typeof item !== 'object') continue;
    const score = filledValueCount(item);
    if (score > bestScore) {
      bestScore = score;
      best = item;
    } else if (score === bestScore && preferred === item) {
      best = item;
    }
  }

  if (preferred && typeof preferred === 'object' && filledValueCount(preferred) >= bestScore) {
    return { ...preferred };
  }
  return best && Object.keys(best).length ? { ...best } : {};
}
