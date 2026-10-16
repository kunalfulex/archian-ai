import {
  resolveDestinationSpreadsheetId,
  resolveMapFieldsSseContext,
} from './resolveMapFieldsSsePayload';

describe('resolveMapFieldsSsePayload', () => {
  it('reads spreadsheet_id from top-level SSE payload', () => {
    expect(
      resolveDestinationSpreadsheetId({
        spreadsheet_id: 'abc123',
        mapping: { Name: { column: 'Name' } },
      }),
    ).toBe('abc123');
  });

  it('falls back to nested mapping.spreadsheet_id', () => {
    expect(
      resolveDestinationSpreadsheetId({
        mapping: { spreadsheet_id: 'nested-id' },
      }),
    ).toBe('nested-id');
  });

  it('resolveMapFieldsSseContext collects sheets and connector', () => {
    const ctx = resolveMapFieldsSseContext({
      spreadsheet_id: 'sh-1',
      destination_connector_id: 'conn-1',
      mapping: {
        available_sheets: { Sheet1: {} },
      },
    });
    expect(ctx.destinationSpreadsheetId).toBe('sh-1');
    expect(ctx.destinationConnectorId).toBe('conn-1');
    expect(ctx.availableSheets).toEqual({ Sheet1: {} });
  });

  it('resolves Salesforce token_id as destination connector', () => {
    const ctx = resolveMapFieldsSseContext({
      destination_name: 'My Org',
      token_id: 'sf-token-9',
      mapping: { available_objects: { Contact: {} } },
    });
    expect(ctx.destinationConnectorId).toBe('sf-token-9');
    expect(ctx.destinationName).toBe('My Org');
    expect(ctx.availableObjects).toEqual({ Contact: {} });
  });

  it('resolves SQL tables and primary_table from SSE payload', () => {
    const ctx = resolveMapFieldsSseContext({
      destination_connector_id: 'pg-dest',
      source_connector_id: 'pg-src',
      primary_table: 'contacts',
      available_tables: { contacts: {}, accounts: {} },
    });
    expect(ctx.destinationConnectorId).toBe('pg-dest');
    expect(ctx.sourceConnectorId).toBe('pg-src');
    expect(ctx.primaryTable).toBe('contacts');
    expect(ctx.availableTables).toEqual({ contacts: {}, accounts: {} });
  });
});
