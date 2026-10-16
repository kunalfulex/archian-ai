import React, { useState, useEffect, useLayoutEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import api from '../api';
import { formatHumanReadableDate } from '../utils/dateUtils';
import { FiSearch, FiChevronLeft, FiChevronRight, FiX } from 'react-icons/fi';
import './SalesforceOrgSchemaPanel.css';

const DEBOUNCE_MS = 400;
const PAGE_SIZE = 25;

export default function SalesforceOrgSchemaPanel({ orgId }) {
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);

  const [searchInput, setSearchInput] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);

  const [objects, setObjects] = useState([]);
  const [totalObjects, setTotalObjects] = useState(0);
  const [objectsLoading, setObjectsLoading] = useState(false);
  const [objectsError, setObjectsError] = useState(null);

  const [modalObject, setModalObject] = useState(null);
  const [modalFields, setModalFields] = useState([]);
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState(null);

  const encOrg = encodeURIComponent(orgId);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchInput.trim()), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  useLayoutEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setSummaryLoading(true);
      setSummaryError(null);
      try {
        const { data } = await api.get(`/api/salesforce-orgs/${encOrg}/schema/summary`);
        if (!cancelled) setSummary(data);
      } catch (e) {
        if (!cancelled) {
          setSummaryError(e.response?.data?.detail || e.message || 'Failed to load schema summary');
          setSummary(null);
        }
      } finally {
        if (!cancelled) setSummaryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [encOrg]);

  useEffect(() => {
    if (!orgId || summaryLoading || !summary?.schema_ready) return;
    let cancelled = false;
    (async () => {
      setObjectsLoading(true);
      setObjectsError(null);
      try {
        const { data } = await api.get(`/api/salesforce-orgs/${encOrg}/schema/objects`, {
          params: {
            page,
            page_size: PAGE_SIZE,
            q: debouncedSearch || undefined,
          },
        });
        if (!cancelled) {
          setObjects(data.items || []);
          setTotalObjects(typeof data.total === 'number' ? data.total : 0);
        }
      } catch (e) {
        if (!cancelled) {
          setObjectsError(e.response?.data?.detail || e.message || 'Failed to load objects');
          setObjects([]);
          setTotalObjects(0);
        }
      } finally {
        if (!cancelled) setObjectsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgId, encOrg, page, debouncedSearch, summaryLoading, summary]);

  const openFields = useCallback(
    async (objectApiName, objectLabel) => {
      setModalObject({ apiName: objectApiName, label: objectLabel });
      setModalFields([]);
      setModalError(null);
      setModalLoading(true);
      try {
        const encObj = encodeURIComponent(objectApiName);
        const { data } = await api.get(
          `/api/salesforce-orgs/${encOrg}/schema/objects/${encObj}/fields`
        );
        setModalFields(data.fields || []);
      } catch (e) {
        setModalError(e.response?.data?.detail || e.message || 'Failed to load fields');
      } finally {
        setModalLoading(false);
      }
    },
    [encOrg]
  );

  const closeModal = useCallback(() => {
    setModalObject(null);
    setModalFields([]);
    setModalError(null);
  }, []);

  useEffect(() => {
    if (!modalObject) return undefined;
    const onKey = (ev) => {
      if (ev.key === 'Escape') closeModal();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [modalObject, closeModal]);

  const totalPages = Math.max(1, Math.ceil((totalObjects || 0) / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const showEmptyHint = summary && !summary.schema_ready && !summaryLoading && !summaryError;

  return (
    <div className="sf-schema-panel">
      <div className="sf-schema-summary">
        <span>
          <strong>Synced objects:</strong>{' '}
          {summaryLoading ? '…' : summaryError ? '—' : summary?.total_objects ?? '—'}
        </span>
        <span>
          <strong>Last refreshed:</strong>{' '}
          {summaryLoading
            ? '…'
            : summaryError
              ? '—'
              : summary?.last_refreshed_at
                ? formatHumanReadableDate(summary.last_refreshed_at)
                : '—'}
        </span>
      </div>

      {summaryError && <div className="sf-schema-error">{summaryError}</div>}

      {showEmptyHint && (
        <p className="sf-schema-hint">
          Object metadata is not in Archian yet. It appears after your org syncs (reconnect or wait for
          background sync). You can still use Salesforce features once sync completes.
        </p>
      )}

      {!summaryLoading && !summaryError && summary?.schema_ready && (
        <>
          <div className="sf-schema-toolbar">
            <div className="sf-schema-search-wrap">
              <FiSearch size={16} />
              <input
                type="search"
                className="sf-schema-search"
                placeholder="Search by object name…"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                aria-label="Search Salesforce objects"
              />
            </div>
          </div>

          {objectsError && <div className="sf-schema-error">{objectsError}</div>}

          {objectsLoading && !objectsError && (
            <div className="sf-schema-loading">Loading objects…</div>
          )}

          {!objectsLoading && !objectsError && (
            <>
              <div className="sf-schema-table-wrap">
                <table className="sf-schema-table sf-schema-objects-table">
                  <thead>
                    <tr>
                      <th>Object name</th>
                      <th className="sf-schema-actions sf-schema-actions-header"> </th>
                    </tr>
                  </thead>
                  <tbody>
                    {objects.length === 0 ? (
                      <tr>
                        <td colSpan={2} style={{ color: '#6b7280', fontStyle: 'italic' }}>
                          No objects match your search.
                        </td>
                      </tr>
                    ) : (
                      objects.map((row) => (
                        <tr key={row.object_api_name}>
                          <td>{row.label || '—'}</td>
                          <td className="sf-schema-actions">
                            <button
                              type="button"
                              className="sf-schema-btn-link"
                              onClick={() => openFields(row.object_api_name, row.label)}
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

              <div className="sf-schema-pagination">
                <span className="sf-schema-page-info">
                  Page {safePage} of {totalPages}
                  {totalObjects > 0 ? ` · ${totalObjects} object${totalObjects === 1 ? '' : 's'}` : ''}
                </span>
                <div className="sf-schema-page-btns">
                  <button
                    type="button"
                    className="sf-schema-icon-btn"
                    disabled={safePage <= 1 || objectsLoading}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    aria-label="Previous page"
                  >
                    <FiChevronLeft size={18} />
                  </button>
                  <button
                    type="button"
                    className="sf-schema-icon-btn"
                    disabled={safePage >= totalPages || objectsLoading}
                    onClick={() => setPage((p) => p + 1)}
                    aria-label="Next page"
                  >
                    <FiChevronRight size={18} />
                  </button>
                </div>
              </div>
            </>
          )}
        </>
      )}

      {modalObject &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            className="sf-schema-modal-overlay"
            role="presentation"
            onClick={(e) => {
              if (e.target === e.currentTarget) closeModal();
            }}
          >
            <div className="sf-schema-modal" role="dialog" aria-modal="true" aria-labelledby="sf-schema-modal-title">
              <div className="sf-schema-modal-header">
                <div>
                  <h2 id="sf-schema-modal-title" className="sf-schema-modal-title">
                    Fields — {modalObject.label || modalObject.apiName}
                  </h2>
                  <p className="sf-schema-modal-sub">{modalObject.apiName}</p>
                </div>
                <button
                  type="button"
                  className="sf-schema-modal-close"
                  onClick={closeModal}
                  aria-label="Close"
                >
                  <FiX size={20} />
                </button>
              </div>
              <div className="sf-schema-modal-body">
                {modalLoading && <div className="sf-schema-loading" style={{ padding: '16px 18px' }}>Loading fields…</div>}
                {modalError && (
                  <div className="sf-schema-error" style={{ padding: '16px 18px' }}>
                    {modalError}
                  </div>
                )}
                {!modalLoading && !modalError && (
                  <div className="sf-schema-table-wrap sf-schema-modal-table-wrap">
                    <table className="sf-schema-table sf-schema-fields-table">
                      <thead>
                        <tr>
                          <th>Field name</th>
                          <th>Type</th>
                        </tr>
                      </thead>
                      <tbody>
                        {modalFields.length === 0 ? (
                          <tr>
                            <td colSpan={2} style={{ color: '#6b7280', fontStyle: 'italic', padding: '16px 18px' }}>
                              No fields stored for this object.
                            </td>
                          </tr>
                        ) : (
                          modalFields.map((f) => (
                            <tr key={f.api_name}>
                              <td className="sf-schema-mono">{f.api_name}</td>
                              <td className="sf-schema-mono">{f.type || '—'}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
