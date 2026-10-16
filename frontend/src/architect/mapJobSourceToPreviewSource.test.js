import { mapJobSourceToPreviewSource } from './mapJobSourceToPreviewSource';

describe('mapJobSourceToPreviewSource', () => {
  it('maps google_sheets variants to gsheets', () => {
    expect(mapJobSourceToPreviewSource('google_sheets')).toBe('gsheets');
    expect(mapJobSourceToPreviewSource('gsheets')).toBe('gsheets');
    expect(mapJobSourceToPreviewSource('Google_Sheets')).toBe('gsheets');
  });

  it('maps other known source types', () => {
    expect(mapJobSourceToPreviewSource('salesforce')).toBe('salesforce');
    expect(mapJobSourceToPreviewSource('sql')).toBe('sql');
    expect(mapJobSourceToPreviewSource('file')).toBe('file');
  });

  it('returns null for unknown or missing source types', () => {
    expect(mapJobSourceToPreviewSource(undefined)).toBeNull();
    expect(mapJobSourceToPreviewSource('')).toBeNull();
    expect(mapJobSourceToPreviewSource('unknown')).toBeNull();
  });
});
