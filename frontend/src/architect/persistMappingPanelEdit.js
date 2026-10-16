/**
 * Persist manual mapping panel edits to chat message metadata + session mapping draft.
 * Canonical migration context is flushed server-side on migrate (flush_mapping_draft_to_context).
 */
import api from '../api';
import { MAPPING_TRANSFORMATION_KEY } from '../utils/migrationRulesKeys';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function buildMappingPanelMetadataPatch(entryMetadata, updatedMapping, transformationRules, panel) {
  const primaryTable =
    panel?.primaryTable ?? entryMetadata?.primary_table ?? null;
  return {
    ...entryMetadata,
    mapping_config: updatedMapping,
    [MAPPING_TRANSFORMATION_KEY]: transformationRules,
    primary_table: primaryTable,
  };
}

/**
 * Sync mapping edit to local chat state, message metadata, and session mapping draft.
 */
export async function persistMappingPanelEdit({
  conversationId,
  entryId,
  entryMetadata,
  panel,
  updatedMapping,
  transformationRules,
  updateMessageMetadata,
  setChatHistory,
}) {
  if (!updatedMapping || typeof updatedMapping !== 'object') return;

  const updatedMetadata = buildMappingPanelMetadataPatch(
    entryMetadata,
    updatedMapping,
    transformationRules,
    panel,
  );

  if (typeof setChatHistory === 'function' && entryId) {
    setChatHistory((prev) =>
      prev.map((e) =>
        e.id === entryId ? { ...e, metadata: { ...e.metadata, ...updatedMetadata } } : e,
      ),
    );
  }

  if (conversationId && entryId && UUID_RE.test(entryId) && typeof updateMessageMetadata === 'function') {
    await updateMessageMetadata(conversationId, entryId, updatedMetadata);
  }

  const jobId = entryMetadata?.job_id;
  if (!conversationId || !jobId) return;

  try {
    await api.patch(`/conversations/${conversationId}/session/mapping-draft`, {
      job_id: jobId,
      mapping_config: updatedMapping,
      transformation_rules: transformationRules,
      mapping_instruction: panel?.mappingInstruction ?? entryMetadata?.mapping_instruction ?? null,
      destination_object_api_name:
        panel?.destinationObjectApiName ?? entryMetadata?.destination_object_api_name ?? null,
      table_name: panel?.primaryTable ?? entryMetadata?.primary_table ?? null,
      destination: entryMetadata?.destination ?? null,
      destination_name: entryMetadata?.destination_name ?? null,
      missing_field_preference:
        panel?.missingFieldPreference ?? entryMetadata?.missing_field_preference ?? null,
    });
  } catch (err) {
    console.error('Failed to persist mapping draft to session:', err);
  }
}

/** Cancel pending debounced persist for a panel entry. */
export function cancelScheduledPersistMappingPanelEdit(deps) {
  const timers = deps.timersRef?.current || {};
  const key = `${deps.mappingId}-${deps.entryId}`;
  if (timers[key]) {
    clearTimeout(timers[key]);
    delete timers[key];
  }
}

/** Await immediate persist (call before migrate so DB has latest mapping). */
export async function flushPersistMappingPanelEdit(deps) {
  cancelScheduledPersistMappingPanelEdit(deps);
  await persistMappingPanelEdit(deps);
}

/** Debounced wrapper for rapid mapping edits. */
export function schedulePersistMappingPanelEdit(deps, delayMs = 400) {
  const timers = deps.timersRef?.current || {};
  const key = `${deps.mappingId}-${deps.entryId}`;
  if (timers[key]) {
    clearTimeout(timers[key]);
  }
  timers[key] = setTimeout(() => {
    persistMappingPanelEdit(deps).catch((err) => {
      console.error('Failed to persist mapping panel edit:', err);
    });
    delete timers[key];
  }, delayMs);
  if (deps.timersRef) {
    deps.timersRef.current = timers;
  }
}
