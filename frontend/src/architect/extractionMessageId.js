/** Resolve persisted chat_messages.id for extraction_completed rows (not data-preview-* UI ids). */

export const CHAT_MESSAGE_UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isPersistedChatMessageId(id) {
  return CHAT_MESSAGE_UUID_RE.test(String(id || '').trim());
}

/**
 * @param {{ id?: string, metadata?: object } | object | null | undefined} source
 * Chat entry or metadata object.
 */
export function resolvePersistedExtractionMessageId(source) {
  if (!source || typeof source !== 'object') return null;
  const meta = source.metadata && source.metadata.event_type ? source.metadata : source;
  const fromMeta = String(meta?.extraction_message_id || '').trim();
  if (isPersistedChatMessageId(fromMeta)) return fromMeta;
  const entryId = String(source.id || '').trim();
  if (isPersistedChatMessageId(entryId)) return entryId;
  return null;
}

export function metadataWithExtractionMessageId(metadata, messageId) {
  const id = String(messageId || '').trim();
  if (!isPersistedChatMessageId(id)) return metadata || {};
  return { ...(metadata || {}), extraction_message_id: id };
}

export function dataPreviewEntryId(jobId) {
  return `data-preview-${jobId}`;
}
