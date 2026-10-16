import {
  buildPanelDataFromMapPayload,
  hasUsableMapping,
  normalizeMappingConfig,
} from './mappingContextUtils';
import { findMappingContextForMigration } from './findMappingContextForMigration.js';

describe('mappingContextUtils', () => {
  test('normalizeMappingConfig unwraps nested mapping', () => {
    expect(normalizeMappingConfig({ mapping: { Email: { column: 'Email' } } })).toEqual({
      Email: { column: 'Email' },
    });
  });

  test('hasUsableMapping rejects metadata-only objects', () => {
    expect(hasUsableMapping({ available_sheets: {} })).toBe(false);
    expect(hasUsableMapping({ Email: { column: 'Email' } })).toBe(true);
  });
});

describe('findMappingContextForMigration', () => {
  test('uses mapping_config on payload when panels ref is empty', () => {
    const ctx = findMappingContextForMigration({
      chatHistory: [],
      mappingPanels: {},
      payload: {
        job_id: 'job-1',
        mapping_id: 'mapping-99',
        mapping_config: { FirstName: { column: 'FirstName' } },
        destination: 'google_sheets',
        destination_name: 'SF 1 Contact list',
      },
      inlineDataPreviews: { 'job-1': { data: [{ FirstName: 'A' }] } },
    });
    expect(ctx).not.toBeNull();
    expect(ctx.mappingId).toBe('mapping-99');
    expect(hasUsableMapping(ctx.mappingData.mapping)).toBe(true);
  });

  test('buildPanelDataFromMapPayload returns panel data', () => {
    const panel = buildPanelDataFromMapPayload({
      job_id: 'j1',
      mapping_config: { Phone: { column: 'Phone' } },
      destination: 'google_sheets',
    });
    expect(panel?.mapping?.Phone).toBeDefined();
  });
});
