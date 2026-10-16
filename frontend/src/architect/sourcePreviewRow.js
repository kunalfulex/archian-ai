/** When row-level extraction rows are purged, rebuild a placeholder source row from stored mapping keys so the mapping UI can still render. */
export function syntheticSourceRowFromMappingConfig(mappingConfig) {
  if (!mappingConfig || typeof mappingConfig !== 'object') return {};
  const row = {};
  Object.keys(mappingConfig).forEach((k) => {
    row[k] = null;
  });
  return row;
}

/** Build left-column source preview from extraction fields + optional sample row (never destination column labels alone). */
export function buildSourcePreviewRow({ sourceFields, previewRecord, mappingConfig }) {
  const fields =
    Array.isArray(sourceFields) && sourceFields.length > 0
      ? sourceFields
      : Object.keys(previewRecord || {});
  if (fields.length > 0) {
    const row = {};
    fields.forEach((key) => {
      row[key] = previewRecord?.[key] ?? null;
    });
    return row;
  }
  return syntheticSourceRowFromMappingConfig(mappingConfig);
}
