import api from '../api';

function normalizeDestType(destType) {
  const t = (destType || '').toLowerCase();
  if (t === 'gsheets') return 'google_sheets';
  return t;
}

/**
 * Options for destination pickers: { value, label } where label is the display name
 * passed to map-fields / classification as destination_name.
 */
export async function fetchSalesforceDestinationOptions() {
  const res = await api.get('/api/salesforce-connection');
  const connections = res.data?.connections;
  if (!Array.isArray(connections)) return [];
  return connections
    .map((c) => {
      const label = (
        c.org_display_name ||
        c.connection_name ||
        c.instance_url ||
        (c.org_id != null ? String(c.org_id) : '') ||
        (c.token_id != null ? String(c.token_id) : '')
      ).trim();
      if (!label) return null;
      return { value: label, label };
    })
    .filter(Boolean);
}

export async function fetchSqlDestinationOptions() {
  const res = await api.get('/api/sql-connectors/');
  const list =
    res.data?.status === 'success' && Array.isArray(res.data.connectors)
      ? res.data.connectors
      : Array.isArray(res.data)
        ? res.data
        : [];
  return list
    .map((c) => {
      const label = (c.name || c.connector_name || c.database_name || '').trim();
      if (!label) return null;
      return { value: label, label };
    })
    .filter(Boolean);
}

export async function fetchGoogleSheetsDestinationOptions() {
  const res = await api.get('/google-sheets/categorized-spreadsheets');
  if (res.data?.status !== 'success' || !Array.isArray(res.data.spreadsheets)) {
    return [];
  }
  const spreadsheets = res.data.spreadsheets;
  const nameCounts = {};
  spreadsheets.forEach((s) => {
    const n = (s.spreadsheet_name || '').trim().toLowerCase();
    if (n) nameCounts[n] = (nameCounts[n] || 0) + 1;
  });

  return spreadsheets
    .map((s) => {
      const name = (s.spreadsheet_name || '').trim();
      const base = name || String(s.spreadsheet_id || '').trim();
      if (!base) return null;
      const isDuplicate = name && nameCounts[name.toLowerCase()] > 1;
      const email = (s.google_account_email || '').trim();
      const label =
        isDuplicate && email ? `${base} (${email})` : base;
      return { value: label, label };
    })
    .filter(Boolean);
}

export async function fetchDestinationConnectionOptions(destType) {
  const t = normalizeDestType(destType);
  if (t === 'sql') return fetchSqlDestinationOptions();
  if (t === 'google_sheets') return fetchGoogleSheetsDestinationOptions();
  if (t === 'salesforce') return fetchSalesforceDestinationOptions();
  return [];
}

export function labelForDestinationType(destType) {
  const t = normalizeDestType(destType);
  if (t === 'sql') return 'SQL database';
  if (t === 'google_sheets') return 'Spreadsheet';
  return 'Salesforce org';
}
