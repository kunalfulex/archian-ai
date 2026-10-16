/**
 * Map GET /job-status source_type to Inline preview pipeline source key (ChatUploader).
 */
export function mapJobSourceToPreviewSource(sourceType) {
  const st = String(sourceType || '').toLowerCase();
  if (st === 'google_sheets' || st === 'gsheets') return 'gsheets';
  if (st === 'sql') return 'sql';
  if (st === 'salesforce') return 'salesforce';
  if (st === 'file') return 'file';
  return null;
}
