import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FiDatabase, FiSearch, FiX } from 'react-icons/fi';
import api from '../api';
import './GoogleSheetsSpreadsheetsList.css';
import './SqlConnectorTablesModal.css';
import './SqlConnectorTablesList.css';

export default function SqlConnectorTablesList({ connectorId }) {
  const [tables, setTables] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [viewTable, setViewTable] = useState(null);
  const [fields, setFields] = useState([]);
  const [fieldsLoading, setFieldsLoading] = useState(false);
  const [fieldsError, setFieldsError] = useState(null);

  useEffect(() => {
    if (!connectorId) return undefined;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      setViewTable(null);
      setSearch('');
      try {
        const { data } = await api.get(`/api/sql-connectors/${connectorId}/tables`);
        if (!cancelled) {
          if (data?.status === 'error') {
            setError(data.message || 'Failed to load tables');
            setTables([]);
            return;
          }
          setTables(Array.isArray(data.tables) ? data.tables : []);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e.response?.data?.message || e.message || 'Failed to load tables');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [connectorId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tables;
    return tables.filter((name) => String(name).toLowerCase().includes(q));
  }, [tables, search]);

  const total = tables.length;
  const showing = filtered.length;

  const openFields = useCallback(
    async (tableName) => {
      setViewTable(tableName);
      setFields([]);
      setFieldsError(null);
      setFieldsLoading(true);
      try {
        const enc = encodeURIComponent(tableName);
        const { data } = await api.get(`/api/sql-connectors/${connectorId}/tables/${enc}/columns`);
        if (data?.status === 'error') {
          setFieldsError(data.message || 'Failed to load columns');
          return;
        }
        setFields(data.fields || []);
      } catch (e) {
        setFieldsError(e.response?.data?.message || e.message || 'Failed to load columns');
      } finally {
        setFieldsLoading(false);
      }
    },
    [connectorId],
  );

  const backToTables = () => {
    setViewTable(null);
    setFields([]);
    setFieldsError(null);
  };

  return (
    <div className="sql-tables-inline gs-spreadsheets-block">
      <div className="gs-spreadsheets-toolbar">
        <h3 className="gs-spreadsheets-heading">
          <FiDatabase className="gs-spreadsheets-heading-icon" aria-hidden />
          {viewTable ? (
            <>Fields — {viewTable}</>
          ) : (
            <>
              Connected tables
              {total > 0 && (
                <span className="gs-spreadsheets-count" title={`${total} tables`}>
                  {total}
                </span>
              )}
            </>
          )}
        </h3>
        {!viewTable && total > 0 && (
          <div className="gs-spreadsheets-search-wrap">
            <FiSearch className="gs-spreadsheets-search-icon" aria-hidden />
            <input
              type="search"
              className="gs-spreadsheets-search"
              placeholder="Search by table name…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search tables"
            />
            {search && (
              <button
                type="button"
                className="gs-spreadsheets-search-clear"
                onClick={() => setSearch('')}
                aria-label="Clear search"
              >
                <FiX aria-hidden />
              </button>
            )}
          </div>
        )}
      </div>

      {viewTable && (
        <button type="button" className="sql-tables-back-btn" onClick={backToTables}>
          ← Back to tables
        </button>
      )}

      {loading && <p className="gs-spreadsheets-empty">Loading tables…</p>}

      {!loading && error && <p className="gs-spreadsheets-empty sql-tables-error">{error}</p>}

      {!loading && !error && !viewTable && total === 0 && (
        <p className="gs-spreadsheets-empty">
          No tables found. Save the connection and run schema sync to refresh.
        </p>
      )}

      {!loading && !error && viewTable && (
        <div className="sql-tables-inline-body">
          {fieldsError && <div className="sql-tables-error">{fieldsError}</div>}
          {fieldsLoading && <p className="sql-tables-loading">Loading fields…</p>}
          {!fieldsLoading && !fieldsError && (
            <div className="sql-tables-table-wrap">
              <table className="sql-tables-table sql-tables-fields-table">
                <thead>
                  <tr>
                    <th>Field name</th>
                    <th>Type</th>
                  </tr>
                </thead>
                <tbody>
                  {fields.length === 0 ? (
                    <tr>
                      <td colSpan={2} style={{ color: '#6b7280', fontStyle: 'italic' }}>
                        No columns returned for this table.
                      </td>
                    </tr>
                  ) : (
                    fields.map((f) => (
                      <tr key={f.name}>
                        <td className="sql-tables-mono">{f.name || '—'}</td>
                        <td className="sql-tables-mono">{f.type || '—'}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {!loading && !error && !viewTable && total > 0 && (
        <div className="sql-tables-inline-body">
          {search && showing === 0 && (
            <p className="gs-spreadsheets-filter-hint">No tables match your search.</p>
          )}
          {showing > 0 && (
            <div className="sql-tables-table-wrap">
              <table className="sql-tables-table">
                <thead>
                  <tr>
                    <th>Table name</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((name) => (
                    <tr key={name}>
                      <td className="sql-tables-mono">{name}</td>
                      <td>
                        <button
                          type="button"
                          className="sql-tables-btn-link"
                          onClick={() => openFields(name)}
                        >
                          View fields
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
