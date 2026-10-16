import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { FiSearch, FiX } from 'react-icons/fi';
import api from '../api';
import './SqlConnectorTablesModal.css';

/**
 * Browse tables and column types for a saved SQL connector (same flow as Salesforce object list).
 */
export default function SqlConnectorTablesModal({ connectorId, isOpen, onClose }) {
  const [tables, setTables] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [viewTable, setViewTable] = useState(null);
  const [fields, setFields] = useState([]);
  const [fieldsLoading, setFieldsLoading] = useState(false);
  const [fieldsError, setFieldsError] = useState(null);

  const filteredTables = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tables;
    return tables.filter((t) => String(t).toLowerCase().includes(q));
  }, [tables, search]);

  useEffect(() => {
    if (!isOpen || !connectorId) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      setTables([]);
      setViewTable(null);
      setSearch('');
      try {
        const { data } = await api.get(`/api/sql-connectors/${connectorId}/tables`);
        if (!cancelled) {
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
  }, [isOpen, connectorId]);

  const openFields = useCallback(
    async (tableName) => {
      setViewTable(tableName);
      setFields([]);
      setFieldsError(null);
      setFieldsLoading(true);
      try {
        const enc = encodeURIComponent(tableName);
        const { data } = await api.get(`/api/sql-connectors/${connectorId}/tables/${enc}/columns`);
        setFields(data.fields || []);
      } catch (e) {
        setFieldsError(e.response?.data?.message || e.message || 'Failed to load columns');
      } finally {
        setFieldsLoading(false);
      }
    },
    [connectorId]
  );

  useEffect(() => {
    if (!isOpen) return undefined;
    const onKey = (ev) => {
      if (ev.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen || !connectorId || typeof document === 'undefined') return null;

  return createPortal(
    <div
      className="sql-tables-modal-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="sql-tables-modal" role="dialog" aria-modal="true" aria-labelledby="sql-tables-modal-title">
        <div className="sql-tables-modal-header">
          <div>
            {!viewTable ? (
              <>
                <h2 id="sql-tables-modal-title" className="sql-tables-modal-title">
                  Table list
                </h2>
                <p className="sql-tables-modal-sub">
                  {loading ? 'Loading…' : `${tables.length} table${tables.length === 1 ? '' : 's'}`}
                </p>
              </>
            ) : (
              <>
                <h2 id="sql-tables-modal-title" className="sql-tables-modal-title">
                  Fields — {viewTable}
                </h2>
                <p className="sql-tables-modal-sub">Column name and data type</p>
              </>
            )}
          </div>
          <button type="button" className="sql-tables-modal-close" onClick={onClose} aria-label="Close">
            <FiX size={20} />
          </button>
        </div>

        <div className="sql-tables-modal-body">
          {!viewTable ? (
            <>
              {error && <div className="sql-tables-error">{error}</div>}
              {!loading && !error && (
                <>
                  <div className="sql-tables-search-wrap">
                    <FiSearch size={16} />
                    <input
                      type="search"
                      className="sql-tables-search"
                      placeholder="Search by table name…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      aria-label="Search tables"
                    />
                  </div>
                  <div className="sql-tables-table-wrap">
                    <table className="sql-tables-table">
                      <thead>
                        <tr>
                          <th>Table name</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {filteredTables.length === 0 ? (
                          <tr>
                            <td colSpan={2} style={{ color: '#6b7280', fontStyle: 'italic' }}>
                              {tables.length === 0 ? 'No tables found.' : 'No tables match your search.'}
                            </td>
                          </tr>
                        ) : (
                          filteredTables.map((name) => (
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
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              {loading && <div className="sql-tables-loading">Loading tables…</div>}
            </>
          ) : (
            <>
              <button type="button" className="sql-tables-back-btn" onClick={() => setViewTable(null)}>
                ← Back to tables
              </button>
              {fieldsError && <div className="sql-tables-error">{fieldsError}</div>}
              {fieldsLoading && <div className="sql-tables-loading">Loading fields…</div>}
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
                          <td colSpan={2} style={{ color: '#6b7280', fontStyle: 'italic', padding: '12px' }}>
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
            </>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
