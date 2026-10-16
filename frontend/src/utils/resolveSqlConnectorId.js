import api from '../api';

/**
 * Resolve sql_connectors.id by display name (case-insensitive).
 * Used when mapping extracted data to a SQL destination (Test DB, etc.).
 */
export async function resolveSqlConnectorIdByName(destinationName) {
  const name = (destinationName || '').trim();
  if (!name) return null;
  try {
    const { data } = await api.get('/api/sql-connectors/');
    const list = Array.isArray(data) ? data : data?.connectors || [];
    const lower = name.toLowerCase();
    const hit = list.find(
      (c) => (c.name || c.connector_name || '').trim().toLowerCase() === lower,
    );
    return hit?.id ? String(hit.id) : null;
  } catch (err) {
    console.warn('resolveSqlConnectorIdByName failed:', err);
    return null;
  }
}
