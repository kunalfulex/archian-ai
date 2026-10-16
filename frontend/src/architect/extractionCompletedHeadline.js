/**
 * Build the markdown headline shown when an extraction job completes or fails.
 */

/** Preview pipeline source key → display label for extraction status headlines. */
export function extractionSourceLabel(sourceType) {
  const st = (sourceType || '').toString().toLowerCase();
  if (st === 'gsheets' || st === 'google_sheets') return 'Google Sheets';
  if (st === 'salesforce') return 'Salesforce';
  if (st === 'file') return 'File';
  if (st === 'sql') return 'SQL';
  return null;
}

export function extractionFailedHeadline(sourceType) {
  const label = extractionSourceLabel(sourceType);
  return label ? `${label} Extraction Failed` : 'Extraction Failed';
}

function sourceLabelForType(sourceType) {
  const st = (sourceType || '').toString().toLowerCase();
  if (st === 'google_sheets' || st === 'gsheets') return 'Google Sheets';
  if (st === 'salesforce') return 'Salesforce';
  if (st === 'file') return 'File';
  return 'SQL';
}

/**
 * @param {string|null|undefined} sourceType
 * @param {number|null|undefined} recordCount
 * @param {{ summary_text?: string }|null|undefined} dataQualityReport
 * @param {{ originalFileRecords?: number|null, limitationApplied?: boolean }} [options]
 */
export function buildInitialExtractionCompletedHeadline(
  sourceType,
  recordCount,
  dataQualityReport,
  options = {},
) {
  const sourceLabel = sourceLabelForType(sourceType);
  const qualitySummaryText = dataQualityReport?.summary_text || '';
  const rc = recordCount ?? 0;
  const original =
    options.originalFileRecords != null ? Number(options.originalFileRecords) : null;
  const limited =
    Boolean(options.limitationApplied) &&
    original != null &&
    Number.isFinite(original) &&
    original > rc;

  const recordsLine = limited
    ? `Extracted **${rc}** of **${original}** records (plan credit limit).`
    : `Extracted **${rc}** records.`;

  return `✅ **${sourceLabel} Extraction Completed**\n\n${recordsLine}${
    qualitySummaryText ? `\n\n${qualitySummaryText}` : ''
  }`;
}

/**
 * @param {string|null|undefined} sourceType
 * @param {number|null|undefined} recordCount
 * @param {{ summary_text?: string }|null|undefined} dataQualityReport
 */
export function buildPostExtractDatasetUpdatedHeadline(
  sourceType,
  recordCount,
  dataQualityReport,
) {
  const sourceLabel = sourceLabelForType(sourceType);
  const qualitySummaryText = dataQualityReport?.summary_text || '';
  const rc = recordCount ?? 0;
  return `✅ **${sourceLabel} — dataset updated**\n\n**${rc}** records after these data operations.${
    qualitySummaryText ? `\n\n${qualitySummaryText}` : ''
  }`;
}
