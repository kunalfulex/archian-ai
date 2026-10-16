import { getSourceTypeKey } from './flowDisplayUtils';

/**
 * Effective cursor API name / column for picker semantics (draft wins over saved).
 */
export function resolvedIncrementalCursorField(flow, cursorFieldDraft) {
  const st = getSourceTypeKey(flow || {});
  const saved = (flow?.incremental_cursor_field || '').trim();
  const draft = (cursorFieldDraft || '').trim();
  const raw = draft || saved;
  if (raw) return raw;
  if (st === 'salesforce') return 'LastModifiedDate';
  return '';
}

/**
 * @returns {'date' | 'datetime'}
 */
export function watermarkPickerKind(flow, cursorFieldDraft, cursorFieldOptions) {
  const resolved = resolvedIncrementalCursorField(flow, cursorFieldDraft);
  if (!resolved) return 'datetime';
  const opt = (cursorFieldOptions || []).find((o) => o.value === resolved);
  if (!opt?.data_type) return 'datetime';
  const dt = String(opt.data_type).toLowerCase();
  if (dt === 'date') return 'date';
  const u = String(opt.data_type).toUpperCase();
  if (/\bDATE\b/.test(u) && !/(TIMESTAMP|DATETIME)/.test(u)) return 'date';
  return 'datetime';
}

/** ISO string from API → native date / datetime-local value (UTC calendar components). */
export function isoWatermarkToInputValue(iso, kind) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  if (kind === 'date') {
    return `${y}-${m}-${day}`;
  }
  const h = String(d.getUTCHours()).padStart(2, '0');
  const min = String(d.getUTCMinutes()).padStart(2, '0');
  return `${y}-${m}-${day}T${h}:${min}`;
}

/** Parse picker value to ISO UTC string for API. */
export function inputValueToIsoWatermark(value, kind) {
  if (value == null || String(value).trim() === '') return null;
  if (kind === 'date') {
    const parts = String(value).split('-').map((x) => parseInt(x, 10));
    if (parts.length < 3 || parts.some((n) => Number.isNaN(n))) return null;
    const [yp, mp, dp] = parts;
    return new Date(Date.UTC(yp, mp - 1, dp, 0, 0, 0)).toISOString();
  }
  const s = String(value);
  const [datePart, timePart = '00:00'] = s.split('T');
  const dparts = datePart.split('-').map((x) => parseInt(x, 10));
  if (dparts.length < 3 || dparts.some((n) => Number.isNaN(n))) return null;
  const [yp, mp, dp] = dparts;
  const [hs, ms = '0'] = timePart.split(':');
  const hh = parseInt(hs, 10);
  const mi = parseInt(ms, 10);
  if (Number.isNaN(hh) || Number.isNaN(mi)) return null;
  return new Date(Date.UTC(yp, mp - 1, dp, hh, mi, 0)).toISOString();
}
