import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { FiArrowLeft, FiLayers, FiTrash2, FiRefreshCw } from 'react-icons/fi';
import InlineMigrationRecords from './components/InlineMigrationRecords';
import api from './api';
import './FlowsPage.css';

const API_PAGE_SIZE = 500;

/**
 * Full-page migration batch results (same table UX as chat InlineMigrationRecords).
 * Route: /flows/:flowId/migration/:batchId
 */
const FlowMigrationResultsPage = () => {
  const { flowId, batchId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [flowName, setFlowName] = useState('');
  const [records, setRecords] = useState([]);
  const [totalCount, setTotalCount] = useState(null);
  const [apiPage, setApiPage] = useState(1);
  const [batchType, setBatchType] = useState(null);
  const [destination, setDestination] = useState('salesforce');
  const [destinationName, setDestinationName] = useState('');
  const [mapping, setMapping] = useState(null);
  /** Salesforce instance base URL / Google spreadsheet id — required for "View record" links */
  const [migrationInstanceURL, setMigrationInstanceURL] = useState(null);
  const [destinationSpreadsheetId, setDestinationSpreadsheetId] = useState(null);
  const [apiPageLoading, setApiPageLoading] = useState(false);
  const [downloadDisabled, setDownloadDisabled] = useState(false);
  const [syncOutcome, setSyncOutcome] = useState(null);

  const [isRollingBack, setIsRollingBack] = useState(false);
  const [showRollbackConfirm, setShowRollbackConfirm] = useState(false);
  const [rollbackResult, setRollbackResult] = useState(null);
  const [rollbackError, setRollbackError] = useState(null);
  const [isRollbackCompleted, setIsRollbackCompleted] = useState(false);

  useEffect(() => {
    if (!flowId) return;
    api
      .get(`/flow/${flowId}`)
      .then((r) => {
        const f = r.data?.flow;
        if (f?.migration_name) setFlowName(f.migration_name);
      })
      .catch(() => {});
  }, [flowId]);

  useEffect(() => {
    setShowRollbackConfirm(false);
    setRollbackResult(null);
    setRollbackError(null);
    setIsRollingBack(false);
  }, [batchId]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!batchId) return;
      setLoading(true);
      setError(null);
      setDownloadDisabled(false);
      setSyncOutcome(null);
      try {
        const res = await api.get(`/migration-records/batch/${batchId}/records`, {
          params: { page: 1, page_size: API_PAGE_SIZE },
        });
        if (cancelled) return;
        const d = res.data;
        const rows = d.records || [];
        const meta = d.metadata || {};
        setDownloadDisabled(meta.detail_expired === true || meta.detail_available === false);
        setRecords(rows);
        setTotalCount(d.total_count ?? meta.total_records ?? rows.length);
        if (meta.batch_type) setBatchType(meta.batch_type);
        const dt = (meta.destination_type || 'salesforce').toLowerCase();
        if (dt === 'google_sheets') setDestination('google_sheets');
        else if (dt === 'sql') setDestination('sql');
        else setDestination('salesforce');
        setDestinationName(meta.destination_name || meta.batch_name || '');
        setMapping(meta.mapping_config || null);
        setSyncOutcome(meta.sync_outcome || null);
        setMigrationInstanceURL(meta.instance_url || meta.mapping_config?.instance_url || null);
        setDestinationSpreadsheetId(
          meta.spreadsheet_id || meta.mapping_config?.spreadsheet_id || meta.mapping_config?.destination_spreadsheet_id || null
        );
        setIsRollbackCompleted(meta.status === 'rolled_back');
        setApiPage(1);
      } catch (e) {
        if (cancelled) return;
        if (e.response?.status === 410) {
          try {
            const br = await api.get(`/migration-records/batch/${batchId}`);
            if (cancelled) return;
            const body = br.data;
            setRecords([]);
            setTotalCount(body.total_records ?? 0);
            setDownloadDisabled(true);
            setBatchType(body.batch_type || null);
            const mc = body.mapping_config || {};
            const dt = (mc.destination_type || 'salesforce').toString().toLowerCase();
            if (dt === 'google_sheets') setDestination('google_sheets');
            else if (dt === 'sql') setDestination('sql');
            else setDestination('salesforce');
            setDestinationName(mc.destination_name || body.batch_name || '');
            setMapping(typeof mc === 'object' ? mc : null);
            setSyncOutcome(body.sync_outcome || null);
            setError(null);
          } catch (e2) {
            setError(e2.response?.data?.detail || e2.message || 'Failed to load migration results');
            setRecords([]);
            setTotalCount(0);
          }
        } else {
          setError(e.response?.data?.detail || e.message || 'Failed to load migration results');
          setRecords([]);
          setTotalCount(0);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  const onFetchPage = useCallback(async (bid, page) => {
    setApiPageLoading(true);
    try {
      const res = await api.get(`/migration-records/batch/${bid}/records`, {
        params: { page, page_size: API_PAGE_SIZE },
      });
      const d = res.data;
      setRecords(d.records || []);
      setApiPage(page);
      const meta = d.metadata || {};
      setDownloadDisabled(meta.detail_expired === true || meta.detail_available === false);
      setSyncOutcome(meta.sync_outcome || null);
      if (meta.status === 'rolled_back') setIsRollbackCompleted(true);
    } catch (err) {
      console.error('Failed to fetch migration records page:', err);
      throw err;
    } finally {
      setApiPageLoading(false);
    }
  }, []);

  const computedTotalRecords = useMemo(
    () => Math.max(totalCount ?? 0, records?.length ?? 0),
    [totalCount, records]
  );

  const handleRollback = async () => {
    if (!batchId) return;
    setIsRollingBack(true);
    setRollbackError(null);
    setRollbackResult(null);
    try {
      const response = await api.post(`/migration-records/rollback/${batchId}`);
      const result = response.data;
      setRollbackResult(result);
      if (result.status === 'success' || result.status === 'partial_success') {
        setIsRollbackCompleted(true);
        try {
          const recordsResponse = await api.get(`/migration-records/batch/${batchId}/records`, {
            params: { page: 1, page_size: API_PAGE_SIZE },
          });
          const data = recordsResponse.data;
          const fetchedRecords = data?.records || [];
          const meta = data?.metadata || {};
          if (meta.batch_type) setBatchType(meta.batch_type);
          setDownloadDisabled(meta.detail_expired === true || meta.detail_available === false);
          setRecords(fetchedRecords);
          setTotalCount(data?.total_count ?? fetchedRecords.length);
          setApiPage(1);
          if (meta.status === 'rolled_back') setIsRollbackCompleted(true);
        } catch (e) {
          console.error('Failed to refresh migration records after rollback:', e);
        }
        setTimeout(() => setShowRollbackConfirm(false), 5000);
      }
    } catch (err) {
      console.error('Rollback failed:', err);
      setRollbackError(err.response?.data?.detail || err.response?.data?.message || 'Rollback failed');
    } finally {
      setIsRollingBack(false);
    }
  };

  const handleRollbackClick = () => {
    if (isRollbackCompleted) return;
    setShowRollbackConfirm(true);
    setRollbackError(null);
    setRollbackResult(null);
  };

  const handleRollbackCancel = () => {
    setShowRollbackConfirm(false);
    setRollbackError(null);
    setRollbackResult(null);
  };

  return (
    <div className="flows-container flow-detail-page flow-full-width-tables flow-full-width-viewport-cap">
      <div className="flow-detail-toolbar">
        <button
          type="button"
          className="flow-detail-back"
          onClick={() => navigate(flowId ? `/flows/${flowId}/timeline` : '/flows')}
        >
          <FiArrowLeft /> Back to timeline
        </button>
      </div>

      <div className="flows-header flow-detail-header">
        <h2 className="flow-detail-page-title">
          <FiLayers style={{ marginRight: 8, verticalAlign: 'middle' }} />
          Migration results{flowName ? ` — ${flowName}` : ''}
        </h2>
      </div>

      {!loading && !error && batchId && records.length > 0 && (
        <div className="flow-migration-results-actions">
          <button
            type="button"
            className="flow-migration-results-rollback-btn"
            onClick={handleRollbackClick}
            disabled={isRollingBack || isRollbackCompleted}
            title={isRollbackCompleted ? 'Rollback already completed' : undefined}
          >
            <FiTrash2 aria-hidden />
            {isRollbackCompleted ? 'Rolled back' : 'Rollback'}
          </button>
        </div>
      )}

      {showRollbackConfirm && (
        <div className="flow-migration-results-rollback-dialog">
          <div className="flow-migration-results-rollback-dialog-inner">
            <strong className="flow-migration-results-rollback-title">Confirm rollback</strong>
            <p className="flow-migration-results-rollback-copy">
              This will delete all successfully created records from this migration. Records that were updated (not
              created) will not be affected. This action cannot be undone.
            </p>
            {computedTotalRecords > 0 && (
              <p className="flow-migration-results-rollback-meta">
                Total records in batch: <strong>{computedTotalRecords}</strong>
              </p>
            )}
            {rollbackError && <div className="flow-migration-results-rollback-error">❌ {rollbackError}</div>}
            {rollbackResult && (
              <div
                className={
                  rollbackResult.status === 'success'
                    ? 'flow-migration-results-rollback-result flow-migration-results-rollback-result--ok'
                    : 'flow-migration-results-rollback-result flow-migration-results-rollback-result--warn'
                }
              >
                {rollbackResult.status === 'success' ? '✅' : '⚠️'} {rollbackResult.message}
                {rollbackResult.rollback_message && (
                  <div className="flow-migration-results-rollback-detail">📋 {rollbackResult.rollback_message}</div>
                )}
                {rollbackResult.deleted_records != null && (
                  <div className="flow-migration-results-rollback-stats">
                    Deleted: <strong>{rollbackResult.deleted_records}</strong>
                    {rollbackResult.failed_records > 0 && (
                      <>
                        {' '}
                        | Failed: <strong>{rollbackResult.failed_records}</strong>
                      </>
                    )}
                  </div>
                )}
                {rollbackResult.status === 'success' && (
                  <div className="flow-migration-results-rollback-close-wrap">
                    <button
                      type="button"
                      className="flow-migration-results-rollback-close"
                      onClick={() => {
                        setShowRollbackConfirm(false);
                        setRollbackResult(null);
                      }}
                    >
                      Close
                    </button>
                  </div>
                )}
              </div>
            )}
            <div className="flow-migration-results-rollback-actions">
              <button
                type="button"
                className="flow-migration-results-rollback-secondary"
                onClick={handleRollbackCancel}
                disabled={isRollingBack}
              >
                Cancel
              </button>
              <button
                type="button"
                className="flow-migration-results-rollback-primary"
                onClick={handleRollback}
                disabled={isRollingBack}
              >
                {isRollingBack ? (
                  <>
                    <FiRefreshCw className="flow-migration-results-rollback-spin" aria-hidden />
                    Rolling back…
                  </>
                ) : (
                  <>
                    <FiTrash2 aria-hidden />
                    Confirm rollback
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flow-detail-body">
        {loading && <p style={{ padding: '1rem' }}>Loading…</p>}
        {error && (
          <div className="error-state flow-detail-error">
            <p>{error}</p>
          </div>
        )}
        {!loading && !error && (!records || records.length === 0) && !downloadDisabled && (
          <p style={{ padding: '1rem', color: '#6b7280' }}>
            {syncOutcome === 'no_changes'
              ? 'Sync completed. No new or updated records were found for this run.'
              : 'No migration records for this batch, or the batch is still processing.'}
          </p>
        )}
        {!loading && !error && downloadDisabled && (!records || records.length === 0) && (
          <p style={{ padding: '1rem', color: '#6b7280' }}>
            Row-level migration detail is no longer available (retention policy).
          </p>
        )}
        {!loading && !error && records.length > 0 && (
          <div className="flow-full-width-table-shell">
            <InlineMigrationRecords
              records={records}
              destination={destination}
              destinationName={destinationName}
              mapping={mapping}
              migrationInstanceURL={migrationInstanceURL}
              destinationSpreadsheetId={destinationSpreadsheetId}
              successCount={totalCount}
              batchType={batchType}
              totalRecordsCount={totalCount}
              batchId={batchId}
              apiPage={apiPage}
              apiPageSize={API_PAGE_SIZE}
              onFetchPage={onFetchPage}
              apiPageLoading={apiPageLoading}
              downloadDisabled={downloadDisabled}
              isCollapsed={false}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default FlowMigrationResultsPage;
