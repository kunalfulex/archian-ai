/**
 * Keep mapping/migration UI in sync with extraction job metadata (no row payload).
 * Migration and transform APIs pull rows server-side via job_id.
 */

import { buildIntentStateFromExtraction } from './intentState';

/** Update mapping panels with record count / retention flags only. */
export function syncMappingPanelRecordCount(
  jobId,
  { totalRecordCount, detailExpired },
  setMappingPanels,
) {
  if (!jobId || typeof setMappingPanels !== 'function') return;
  const count =
    totalRecordCount != null && totalRecordCount !== '' ? Number(totalRecordCount) : null;
  if (count == null && detailExpired == null) return;

  setMappingPanels((prev) => {
    let changed = false;
    const next = { ...prev };
    for (const [mappingId, panel] of Object.entries(prev)) {
      if (String(panel?.jobId) !== String(jobId)) continue;
      changed = true;
      next[mappingId] = {
        ...panel,
        ...(count != null && !Number.isNaN(count) ? { totalRecordCount: count } : {}),
        ...(detailExpired != null ? { detailExpired: !!detailExpired } : {}),
      };
    }
    return changed ? next : prev;
  });
}

/**
 * Ensure chat has an extraction_completed anchor for job_id resolution (metadata only).
 */
export function ensureExtractionCompletedChatEntry({
  jobId,
  recordCount,
  metadata,
  setChatHistory,
  metadataMatchesJobId,
}) {
  if (!jobId || typeof setChatHistory !== 'function') return;

  setChatHistory((prev) => {
    const exists = prev.some(
      (e) =>
        e.metadata?.event_type === 'extraction_completed' &&
        metadataMatchesJobId(e.metadata, jobId),
    );
    if (exists) return prev;

    const meta = {
      event_type: 'extraction_completed',
      job_id: jobId,
      ...(recordCount != null ? { record_count: recordCount } : {}),
      ...(metadata || {}),
    };
    const sourceType = meta.source_type;
    if (sourceType && !meta.intent_state) {
      meta.intent_state = buildIntentStateFromExtraction({
        jobId,
        sourceType,
        sourceName: meta.source_name,
        destination: meta.destination,
        destinationName: meta.destination_name,
        extractionRules: meta.extraction_rules,
        extractionVersion: meta.extraction_version,
      });
    }

    return [
      ...prev,
      {
        id: `data-preview-${jobId}`,
        type: 'data_preview',
        text: '',
        timestamp: Date.now(),
        jobId,
        metadata: meta,
        isLoading: false,
        isUnavailable: false,
      },
    ];
  });
}
