/** Resolve job id from in-flight SSE JSON (canonical: job_id). */
export function extractionJobIdFromPayload(payload) {
  if (!payload || typeof payload !== 'object') return null;
  return payload.job_id || null;
}
