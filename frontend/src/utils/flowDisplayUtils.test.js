import { buildSourceDataFormatRows } from './flowDisplayUtils';

describe('buildSourceDataFormatRows', () => {
  it('hides extraction rule metadata keys from source data format table', () => {
    const { rows } = buildSourceDataFormatRows({
      Name: 'Aanya Carter',
      Email: 'a@test.com',
      ordering_rules: { sort: [{ field: 'Name', direction: 'asc' }] },
      filter_rules: { conditions: [] },
    });
    const fields = rows.map((r) => r.field);
    expect(fields).toContain('Name');
    expect(fields).toContain('Email');
    expect(fields).not.toContain('ordering_rules');
    expect(fields).not.toContain('filter_rules');
  });
});
