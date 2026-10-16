/**
 * Metadata / rule blobs sometimes appear on row 0 from the backend.
 * Hide them from data preview tables, CSV, and field-mapping rows so we don't show "[object Object]".
 */
export const HIDDEN_EXTRACTION_RECORD_KEYS = new Set([
  'data_cleanup_rules',
  'transformation_rules',
  'filter_rules',
  'ordering_rules',
  'deduplication_rules',
  'mapping_context_summary',
  'source_schema',
  'document_type',
  'document_description',
  'object_name',
  'merge_actions',
  'remove_fields',
  'ordering_rules',
]);

/** Match InlineDataPreview rules: string keys only, no underscore-prefixed internals */
export function isHiddenExtractionMetadataKey(key) {
  return (
    typeof key === 'string' &&
    (key.startsWith('_') || HIDDEN_EXTRACTION_RECORD_KEYS.has(key))
  );
}

/** Shallow copy of a record without hidden keys (safe if record is null). */
export function omitHiddenExtractionRecordKeys(record) {
  if (!record || typeof record !== 'object') return {};
  const out = {};
  for (const [k, v] of Object.entries(record)) {
    if (!isHiddenExtractionMetadataKey(k)) out[k] = v;
  }
  return out;
}
