/**
 * Normalize map_fields_ready SSE payload into panel / migration context fields.
 */

export function resolveDestinationSpreadsheetId(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const mapping = payload.mapping;
  return (
    payload.spreadsheet_id ||
    payload.destination_spreadsheet_id ||
    (typeof mapping === 'object' && mapping !== null
      ? mapping.spreadsheet_id || mapping.destination_spreadsheet_id
      : null) ||
    null
  );
}

export function resolveDestinationConnectorId(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const mapping = payload.mapping;
  return (
    payload.destination_connector_id ||
    payload.token_id ||
    payload.salesforce_token_id ||
    payload.connector_id ||
    (typeof mapping === 'object' && mapping !== null
      ? mapping.token_id ||
        mapping.salesforce_token_id ||
        mapping.connector_id ||
        mapping.destination_connector_id
      : null) ||
    null
  );
}

/** Source-side connector (SQL/SF extract), not destination. */
export function resolveSourceConnectorId(payload) {
  if (!payload || typeof payload !== 'object') return null;
  return payload.source_connector_id || null;
}

export function resolveMapFieldsSseContext(payload) {
  const mapping = payload?.mapping;
  const availableSheets =
    (typeof mapping === 'object' && mapping?.available_sheets) ||
    payload?.available_sheets ||
    null;
  const availableTables =
    (typeof mapping === 'object' && mapping?.available_tables) ||
    payload?.available_tables ||
    null;
  const availableObjects =
    (typeof mapping === 'object' && mapping?.available_objects) ||
    payload?.available_objects ||
    null;

  const destConnector = resolveDestinationConnectorId(payload);
  const srcConnector = resolveSourceConnectorId(payload);

  return {
    destinationSpreadsheetId: resolveDestinationSpreadsheetId(payload),
    // Prefer explicit destination connector; avoid using source connector_id for SQL dest.
    destinationConnectorId: destConnector,
    sourceConnectorId: srcConnector,
    availableSheets,
    availableTables,
    availableObjects,
    primaryTable:
      payload?.primary_table ||
      payload?.table_name ||
      mapping?.primary_table ||
      null,
    destinationName: payload?.destination_name || null,
    destinationObjectApiName:
      payload?.destination_object_api_name || mapping?.destination_object_api_name || null,
    orgRulesDisplayByObject:
      mapping?.org_rules_display_by_object || payload?.org_rules_display_by_object || {},
  };
}
