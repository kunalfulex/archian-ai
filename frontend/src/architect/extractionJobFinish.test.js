import {
  hasExistingExtractionPreview,
  isMigrationShapedExtractionFinish,
} from './extractionJobFinish';

describe('extractionJobFinish', () => {
  test('isMigrationShapedExtractionFinish detects migration_started', () => {
    expect(
      isMigrationShapedExtractionFinish({
        job_id: 'bg-1',
        result: { action: 'migration_started', batch_id: 'b1' },
      }),
    ).toBe(true);
  });

  test('isMigrationShapedExtractionFinish ignores data ops finish', () => {
    expect(
      isMigrationShapedExtractionFinish({
        job_id: 'extract-2',
        result: { operations_applied: ['filter'], record_count: 158 },
      }),
    ).toBe(false);
  });

  test('hasExistingExtractionPreview detects inline preview', () => {
    expect(
      hasExistingExtractionPreview('j1', [], { j1: { data: [{ id: 1 }] } }),
    ).toBe(true);
  });

  test('hasExistingExtractionPreview detects chat history entry', () => {
    expect(
      hasExistingExtractionPreview('j1', [{ id: 'data-preview-j1', type: 'data_preview' }], {}),
    ).toBe(true);
  });
});
