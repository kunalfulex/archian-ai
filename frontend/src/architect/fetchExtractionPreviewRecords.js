import api from '../api';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Load first page of extraction rows for inline preview (Redis-slim SSE completion).
 * Retries while background persist may still be in flight.
 */
export async function fetchExtractionPreviewRecords(
  jobId,
  { pageSize = 500, maxAttempts = 12, initialDelayMs = 400 } = {},
) {
  if (!jobId) return null;
  let delay = initialDelayMs;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) {
      await sleep(delay);
      delay = Math.min(delay * 1.35, 3000);
    }
    try {
      const res = await api.get(`/extraction-results/${jobId}/records`, {
        params: { page: 1, page_size: pageSize },
      });
      const p = res.data;
      if (p?.status === 'success') {
        if (Array.isArray(p.records) && p.records.length > 0) {
          return { records: p.records, totalCount: p.total_count ?? p.records.length };
        }
        const total = Number(p.total_count);
        if (total === 0) {
          return { records: [], totalCount: 0 };
        }
      }
    } catch (err) {
      const status = err.response?.status;
      if (status && status !== 404 && status !== 503 && attempt === maxAttempts - 1) {
        throw err;
      }
    }
  }
  return null;
}
