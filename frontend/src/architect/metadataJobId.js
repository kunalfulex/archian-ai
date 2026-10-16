/**
 * Canonical extraction job id on persisted chat metadata.
 * Reads legacy task_id only for messages saved before job_id unification.
 */
export function metadataJobId(metadata) {
  if (!metadata || typeof metadata !== 'object') return null;
  return metadata.job_id || metadata.task_id || null;
}

export function metadataMatchesJobId(metadata, jobId) {
  if (!jobId) return false;
  return metadataJobId(metadata) === jobId;
}
