/** Normalize FastAPI `detail` (string, object, or validation list) for chat and alerts. */
export function formatFastApiDetail(detail) {
  if (detail == null || detail === '') return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object' && item.msg != null) return String(item.msg);
        try {
          return JSON.stringify(item);
        } catch {
          return String(item);
        }
      })
      .filter(Boolean)
      .join('; ');
  }
  if (typeof detail === 'object' && detail.msg != null) return String(detail.msg);
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

/** User-facing chat error from architect SSE `status: error` payloads. */
export function formatArchitectStreamErrorMessage(data) {
  const base = (data?.message || '').trim() || 'An error occurred. Please try again.';
  if (data?.code === 'google_reauth_required') {
    return (
      `❌ **Error:** ${base}\n\n` +
      `[Open Connections to reconnect Google Sheets](/connections)`
    );
  }
  return `❌ **Error:** ${base}`;
}
