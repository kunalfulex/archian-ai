import {
  sanitizeSqlMappingForMigration,
  shouldDropSqlLookupOf,
} from './sqlMappingSanitize';

describe('sqlMappingSanitize', () => {
  it('drops same-table lookup_of', () => {
    expect(shouldDropSqlLookupOf('users', 'users.user_id', 'users')).toEqual({
      drop: true,
      reason: 'same_table_as_lookup_child',
    });
  });

  it('sanitizes mapping before migrate', () => {
    const { mapping, dropped } = sanitizeSqlMappingForMigration(
      {
        Email: { table: 'users', column: 'email', lookup_of: 'users.user_id' },
      },
      'users',
    );
    expect(mapping.Email.lookup_of).toBeUndefined();
    expect(dropped).toHaveLength(1);
  });
});
