/**
 * Detect extraction_job_finished payloads that are migration-shaped (mis-routed terminal events).
 */
export function isMigrationShapedExtractionFinish(jobStatus) {
  if (!jobStatus || typeof jobStatus !== 'object') return false;
  const result = jobStatus.result;
  if (!result || typeof result !== 'object') return false;
  const action = String(result.action || '').toLowerCase();
  if (
    action === 'migration_started' ||
    action === 'migration_needs_mapping' ||
    action === 'migration_blocked_by_audit'
  ) {
    return true;
  }
  if (result.batch_id && !(Array.isArray(result.operations_applied) && result.operations_applied.length)) {
    return true;
  }
  return false;
}

/** Skip a second preview card when this job already has inline preview or a chat entry. */
export function hasExistingExtractionPreview(jobId, chatHistory, inlineDataPreviews) {
  if (!jobId) return false;
  if (inlineDataPreviews?.[jobId]?.data?.length) return true;
  const previewId = `data-preview-${jobId}`;
  return Array.isArray(chatHistory) && chatHistory.some((entry) => entry?.id === previewId);
}
