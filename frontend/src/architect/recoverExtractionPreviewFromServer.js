/**
 * Recover inline extraction preview when SSE ends without extraction_job_finished.
 * Uses extraction-results + paginated records — not GET /job-status.
 */
import api from '../api';
import { fetchExtractionPreviewRecords } from './fetchExtractionPreviewRecords';

const RECOVER_DELAYS_MS = [400, 800, 1200, 2000, 3000, 4500, 6000, 8000];

function parseExtractionMetadata(row) {
  let meta = row?.extraction_metadata;
  if (typeof meta === 'string') {
    try {
      meta = JSON.parse(meta);
    } catch {
      meta = {};
    }
  }
  return meta && typeof meta === 'object' ? meta : {};
}

/**
 * @returns {Promise<object|null>} synthetic job-status shape for applyTaskJobStatusToUi
 */
export async function loadCompletedExtractionTaskData(jobId, { maxAttempts = RECOVER_DELAYS_MS.length } = {}) {
  if (!jobId) return null;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) {
      await new Promise((resolve) => setTimeout(resolve, RECOVER_DELAYS_MS[attempt - 1] ?? 2000));
    }

    let row = null;
    try {
      const res = await api.get(`/extraction-results/${jobId}`);
      row = res.data?.result || res.data || null;
    } catch (err) {
      if (err.response?.status !== 404) {
        console.warn('loadCompletedExtractionTaskData metadata failed:', err);
      }
      continue;
    }

    const loaded = await fetchExtractionPreviewRecords(jobId, {
      maxAttempts: 1,
      initialDelayMs: 0,
    });

    const meta = parseExtractionMetadata(row);
    const recordCount =
      loaded?.totalCount ??
      row?.record_count ??
      row?.total_records ??
      (Array.isArray(loaded?.records) ? loaded.records.length : 0);

    const hasRows = Array.isArray(loaded?.records) && loaded.records.length > 0;
    const isZeroRecordSuccess = recordCount === 0 && row;

    if (!row || (!hasRows && !isZeroRecordSuccess && recordCount == null)) {
      continue;
    }

    return {
      job_id: jobId,
      status: 'completed',
      source_type: row.source_type,
      parent_job_id: meta.parent_job_id || row.parent_job_id || null,
      result: {
        status: 'success',
        data: loaded?.records || [],
        total_records: recordCount,
        record_count: recordCount,
        document_type: row.document_type,
        document_description: row.document_description,
        source_type: row.source_type,
        source_name: row.source_name,
        destination: row.destination,
        destination_name: row.destination_name,
        sql_query: meta.sql_query || meta.soql || meta.query || null,
        table_name: meta.table_name || meta.object_api_name || null,
        connector_id: row.source_identifier || meta.source_connector_id || null,
        pipeline_steps: meta.pipeline_steps || row.pipeline_steps || null,
        pipeline_narrative: meta.pipeline_narrative || row.pipeline_narrative || null,
        operations_applied: meta.operations_applied || row.operations_applied || null,
      },
    };
  }

  return null;
}

const activeExtractionRecovers = new Map();

export async function syncExtractionPreviewFromServer({
  jobId,
  conversationId,
  sourceTypeHint,
  applyTaskJobStatusToUi,
  resolvePreviewSourceType,
  maxAttempts,
}) {
  if (!jobId || typeof applyTaskJobStatusToUi !== 'function') return false;

  const key = `${conversationId || 'none'}:${jobId}`;
  if (activeExtractionRecovers.has(key)) {
    return activeExtractionRecovers.get(key);
  }

  const work = (async () => {
    const taskData = await loadCompletedExtractionTaskData(jobId, { maxAttempts });
    if (!taskData) return false;
    const sourceType = resolvePreviewSourceType(taskData.source_type, sourceTypeHint);
    await applyTaskJobStatusToUi(taskData, sourceType, conversationId);
    return true;
  })();

  activeExtractionRecovers.set(key, work);
  try {
    return await work;
  } finally {
    activeExtractionRecovers.delete(key);
  }
}
