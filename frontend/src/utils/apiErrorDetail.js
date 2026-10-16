/**
 * FastAPI often returns `detail` as a string or `{ code, message }`.
 * Axios surfaces generic `error.message`; use this for user-facing copy.
 */
export function getApiErrorMessage(error) {
  const d = error?.response?.data?.detail;
  if (d == null || d === '') return error?.message || 'Unknown error';
  if (typeof d === 'string') return d;
  if (typeof d === 'object' && d.message != null) return String(d.message);
  return error?.message || 'Unknown error';
}

/** Returns e.g. GOOGLE_SHEET_NOT_LINKED, FLOW_NOT_FOUND, or null */
export function getApiErrorCode(error) {
  const d = error?.response?.data?.detail;
  if (d && typeof d === 'object' && d.code) return String(d.code);
  return null;
}
