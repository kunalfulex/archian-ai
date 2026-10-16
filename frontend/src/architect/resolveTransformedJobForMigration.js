/**
 * If the user ran preview transformation for this mapping, migrate from the child extraction job.
 * Must stay on the same extraction chain as the mapping panel — never reuse an unrelated
 * transformation_completed from another session (e.g. prior SQL filter in the same chat).
 */
import { resolveExtractionCompletedEvent } from './resolveExtractionContextForSave';
import { metadataJobId } from './metadataJobId';

function transformationMatchesAnchor(md, anchorJobId, chainJobId) {
  if (!md?.transformed_job_id) return false;
  const parent = md.parent_job_id || md.job_id;
  if (parent === anchorJobId || parent === chainJobId) return true;
  if (md.job_id === anchorJobId || md.job_id === chainJobId) return true;
  if (md.transformed_job_id === chainJobId) return true;
  return false;
}

export function resolveTransformedJobForMigration(chatHistory, mappingId, originalExtractionJobId) {
  if (!originalExtractionJobId) return null;
  const history = chatHistory || [];
  const latest = resolveExtractionCompletedEvent(history, originalExtractionJobId);
  const chainJobId = latest ? metadataJobId(latest.metadata) : originalExtractionJobId;

  for (let i = history.length - 1; i >= 0; i--) {
    const md = history[i].metadata || {};
    if (md.event_type !== 'transformation_completed' || !md.transformed_job_id) continue;
    if (mappingId && md.mapping_id && md.mapping_id !== mappingId) continue;
    if (!transformationMatchesAnchor(md, originalExtractionJobId, chainJobId)) continue;
    return md.transformed_job_id;
  }

  return null;
}

/**
 * Job id whose rows should be loaded for migration (post-extract child or preview-transform child).
 */
export function resolveRecordsJobIdForMigration(chatHistory, anchorJobId, mappingId) {
  if (!anchorJobId) return null;
  const transformed = resolveTransformedJobForMigration(chatHistory, mappingId, anchorJobId);
  if (transformed) return transformed;
  const latest = resolveExtractionCompletedEvent(chatHistory, anchorJobId);
  return latest ? metadataJobId(latest.metadata) : anchorJobId;
}
