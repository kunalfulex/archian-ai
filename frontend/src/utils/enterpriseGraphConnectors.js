/** Load user's connectors for Enterprise Graph member picker */

export async function fetchGoogleSheetEntities(api, connectionId) {
  try {
    const { data } = await api.get(
      `/api/connectors/google-sheets/${connectionId}/assessments/entities`,
    );
    return (data.entities || []).map((ent) => ({
      ...ent,
      entity_id: ent.entity_id || ent.value || '',
    }));
  } catch {
    return [];
  }
}

export function connectorIconPath(opt) {
  if (!opt) return null;
  if (opt.icon === 'salesforce') {
    return '/assets/connectors/Salesforce.com_logo.svg (1).png';
  }
  if (opt.icon === 'sheets') {
    return '/assets/connectors/google-sheets-logo.png';
  }
  if (opt.icon === 'snowflake') {
    return '/assets/connectors/snowflake-logo.png';
  }
  if (opt.icon === 'sql' || opt.connector_type === 'sql') {
    const st = (opt.subtitle || opt.connector_type || '').toLowerCase();
    if (st.includes('mysql')) return '/assets/connectors/MySQL-Logo.wine.png';
    if (st.includes('mssql')) return '/assets/connectors/mssql-logo.png';
    if (st.includes('postgres')) return '/assets/connectors/postgresql-logo.png';
    return '/assets/connectors/postgresql-logo.png';
  }
  return null;
}

export async function fetchAvailableConnectors(api) {
  const options = [];

  try {
    const sfRes = await api.get('/api/salesforce-connection');
    const connections = sfRes.data?.connections || [];
    connections.forEach((conn) => {
      const orgId = conn.org_id;
      if (!orgId) return;
      options.push({
        key: `salesforce:${orgId}`,
        connector_type: 'salesforce',
        connector_id: orgId,
        label: conn.connection_name || conn.org_display_name || 'Salesforce',
        subtitle: 'Salesforce',
        icon: 'salesforce',
      });
    });
  } catch {
    /* ignore */
  }

  try {
    const sqlRes = await api.get('/api/sql-connectors/');
    const connectors = sqlRes.data?.connectors || [];
    connectors.forEach((c) => {
      if (!c.id || !c.is_active) return;
      const subtype = (c.connector_type || '').toLowerCase();
      options.push({
        key: `sql:${c.id}`,
        connector_type: 'sql',
        connector_id: c.id,
        label: c.name || `${c.connector_type} database`,
        subtitle: c.connector_type,
        icon: subtype === 'snowflake' ? 'snowflake' : 'sql',
        paletteRole: subtype === 'snowflake' ? 'snowflake' : 'source',
      });
    });
  } catch {
    /* ignore */
  }

  try {
    const gsRes = await api.get('/api/google-sheets-connections');
    const connections = gsRes.data?.connections || [];
    for (const conn of connections) {
      if (!conn.id) continue;
      const sheetEntities = await fetchGoogleSheetEntities(api, conn.id);
      options.push({
        key: `google_sheets:${conn.id}`,
        connector_type: 'google_sheets',
        connector_id: conn.id,
        label:
          conn.connection_name ||
          conn.google_account_email ||
          'Google Sheets',
        subtitle:
          sheetEntities.length > 0
            ? `${sheetEntities.length} sheet${sheetEntities.length === 1 ? '' : 's'} synced`
            : conn.google_account_email || 'Google Sheets',
        icon: 'sheets',
        sheetEntities,
        sheetCount: sheetEntities.length,
      });
    }
  } catch {
    /* ignore */
  }

  return options;
}

export function connectorIdFromMember(member) {
  const ref = member?.connector_ref || {};
  const ct = member?.connector_type;
  if (ct === 'salesforce') return ref.org_id || ref.connector_id || '';
  if (ct === 'sql') return ref.connector_id || '';
  if (ct === 'google_sheets') {
    return ref.google_sheets_connection_id || ref.connection_id || '';
  }
  return '';
}

export function googleSheetsCatalogLabelMap(entities = []) {
  return Object.fromEntries(
    entities
      .map((ent) => {
        const id = ent.entity_id || ent.value || '';
        const label = ent.label || id;
        return id ? [id, label] : null;
      })
      .filter(Boolean),
  );
}

/** Load workbook/tab labels for GSheets members from connector catalog (not assessment). */
export async function loadGoogleSheetsScopeLabelsForMembers(api, members = []) {
  const labelsByMember = {};
  await Promise.all(
    members
      .filter((m) => m.connector_type === 'google_sheets')
      .map(async (m) => {
        const connectionId = connectorIdFromMember(m);
        if (!connectionId) return;
        const entities = await fetchGoogleSheetEntities(api, connectionId);
        labelsByMember[m.id] = googleSheetsCatalogLabelMap(entities);
      }),
  );
  return labelsByMember;
}

export function googleSheetsScopeLabel(member, entityLabelById = null) {
  if (member?.connector_type !== 'google_sheets') return null;
  const allowlist = member?.connector_ref?.sheet_entity_allowlist;
  if (allowlist == null) return 'all sheets';
  if (allowlist.length === 0) return 'no sheets selected';
  if (entityLabelById && allowlist.length > 0) {
    const labels = allowlist
      .map((id) => entityLabelById[id])
      .filter(Boolean);
    if (labels.length === 1) return labels[0];
    if (labels.length > 1) return `${labels.length} sheets: ${labels.slice(0, 2).join(', ')}${labels.length > 2 ? '…' : ''}`;
  }
  return `${allowlist.length} sheet${allowlist.length === 1 ? '' : 's'} selected`;
}
