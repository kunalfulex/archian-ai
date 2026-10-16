import {
  buildInitialExtractionCompletedHeadline,
  buildPostExtractDatasetUpdatedHeadline,
} from './extractionCompletedHeadline';

describe('buildInitialExtractionCompletedHeadline', () => {
  it('shows simple count when no plan cap', () => {
    const text = buildInitialExtractionCompletedHeadline('file', 500, null);
    expect(text).toContain('File Extraction Completed');
    expect(text).toContain('Extracted **500** records.');
    expect(text).not.toContain('plan credit limit');
  });

  it('shows extracted of total when plan credit limit applied', () => {
    const text = buildInitialExtractionCompletedHeadline('file', 500, null, {
      limitationApplied: true,
      originalFileRecords: 5000,
    });
    expect(text).toContain('Extracted **500** of **5000** records (plan credit limit).');
  });
});

describe('buildPostExtractDatasetUpdatedHeadline', () => {
  it('shows dataset updated copy', () => {
    const text = buildPostExtractDatasetUpdatedHeadline('file', 400, null);
    expect(text).toContain('dataset updated');
    expect(text).toContain('**400** records after these data operations.');
  });
});
