/**
 * Resolve mapping panel + chat entry for agent "start migration" (same source of truth as UI button).
 */
import {
  buildPanelDataFromMapPayload,
  buildPanelDataFromMappingEntry,
  findLatestMappingPanelEntry,
  hasUsableMapping,
  normalizeMappingConfig,
  panelMatchesExtractionJob,
} from './mappingContextUtils';
import { resolveExtractionCompletedEvent } from './resolveExtractionContextForSave';
import { metadataJobId } from './metadataJobId';

export { hasUsableMapping, normalizeMappingConfig, buildPanelDataFromMapPayload };

export function findMappingContextForMigration({
  chatHistory,
  mappingPanels,
  payload,
  inlineDataPreviews,
}) {
  const panels = mappingPanels || {};
  const history = chatHistory || [];
  const previews = inlineDataPreviews || {};
  const payloadJobId = payload?.job_id;
  const mappingId = payload?.mapping_id;
  const latestForPayload = payloadJobId
    ? resolveExtractionCompletedEvent(history, payloadJobId)
    : null;
  const jobId = latestForPayload ? metadataJobId(latestForPayload.metadata) : payloadJobId;

  if (mappingId && panels[mappingId] && hasUsableMapping(panels[mappingId].mapping)) {
    const entry =
      history.find(
        (e) =>
          e.type === 'mapping_panel' &&
          (e.mappingId === mappingId || e.metadata?.mapping_id === mappingId),
      ) || null;
    return { mappingId, mappingData: panels[mappingId], entry };
  }

  if (jobId) {
    for (let i = history.length - 1; i >= 0; i--) {
      const e = history[i];
      if (e.type !== 'mapping_panel' || e.isLoading || e.isUnavailable) continue;
      const mid = e.mappingId || e.metadata?.mapping_id;
      const panelJobId = e.metadata?.job_id;
      if (mid && panels[mid] && hasUsableMapping(panels[mid].mapping)) {
        if (panelMatchesExtractionJob(panelJobId, jobId, history)) {
          return { mappingId: mid, mappingData: panels[mid], entry: e };
        }
      }
    }
  }

  const fromPayload = buildPanelDataFromMapPayload(payload, previews);
  if (fromPayload) {
    const entry = findLatestMappingPanelEntry(history, jobId);
    const mid =
      mappingId || entry?.mappingId || entry?.metadata?.mapping_id || `mapping-${Date.now()}`;
    return { mappingId: mid, mappingData: fromPayload, entry };
  }

  const entry = findLatestMappingPanelEntry(history, jobId);
  if (entry) {
    const fromMeta = buildPanelDataFromMappingEntry(entry, previews);
    if (fromMeta) {
      const mid = entry.mappingId || entry.metadata?.mapping_id;
      return { mappingId: mid, mappingData: fromMeta, entry };
    }
  }

  return null;
}
