import {
  filterSalesforceMappingToDestinationSchema,
  filterSqlMappingToDestinationSchema,
  sourceFieldMatchesDestinationColumn,
} from './mappingDestinationValidate';
import { pickMostCompleteRecord } from './pickMostCompleteRecord';

describe('pickMostCompleteRecord', () => {
  it('prefers row with more filled fields', () => {
    const rows = [
      { email: 'a@b.com', first_name: '', phone: '' },
      { email: 'c@d.com', first_name: 'Ada', phone: '555' },
    ];
    expect(pickMostCompleteRecord(rows)).toEqual(rows[1]);
  });
});

describe('filterSalesforceMappingToDestinationSchema', () => {
  it('drops fields not in org schema', () => {
    const objects = {
      Contact: { Email: {}, FirstName: {} },
    };
    const mapping = {
      email: { object: 'Contact', field: 'Email' },
      phone: { object: 'Contact', field: 'Phone__c' },
    };
    const out = filterSalesforceMappingToDestinationSchema(mapping, objects);
    expect(out).toHaveProperty('email');
    expect(out).not.toHaveProperty('phone');
  });
});

describe('filterSqlMappingToDestinationSchema', () => {
  it('drops implausible column mappings', () => {
    const tables = {
      users: { columns: { email: {}, first_name: {} } },
    };
    const mapping = {
      phone: { table: 'users', column: 'first_name' },
      email: { table: 'users', column: 'email' },
    };
    const out = filterSqlMappingToDestinationSchema(mapping, tables, { primaryTable: 'users' });
    expect(out).toHaveProperty('email');
    expect(out).not.toHaveProperty('phone');
  });
});

describe('sourceFieldMatchesDestinationColumn', () => {
  it('rejects phone to first_name', () => {
    expect(sourceFieldMatchesDestinationColumn('phone', 'first_name')).toBe(false);
  });
});
