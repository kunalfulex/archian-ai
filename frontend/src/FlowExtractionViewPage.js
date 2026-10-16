import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { FiArrowLeft, FiBarChart2 } from 'react-icons/fi';
import InlineDataPreview from './components/InlineDataPreview';
import api from './api';
import './FlowsPage.css';

const API_PAGE_SIZE = 500;

/**
 * Full-page extraction preview (same UX as chat InlineDataPreview): pagination, search, CSV export.
 * Route: /flows/:flowId/extraction/:extractionId — extractionId may be extraction_results.id or job_id.
 */
const FlowExtractionViewPage = () => {
  const { flowId, extractionId } = useParams();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [flowName, setFlowName] = useState('');
  const [previewData, setPreviewData] = useState([]);
  const [metadata, setMetadata] = useState(null);
  const [totalRecordCount, setTotalRecordCount] = useState(0);
  const fullRecordCountRef = useRef(0);
  const [downloadDisabled, setDownloadDisabled] = useState(false);
  const isPaginated = totalRecordCount > API_PAGE_SIZE;

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!extractionId) return;
      setLoading(true);
      setError(null);
      setDownloadDisabled(false);
      try {
        const res = await api.get(`/extraction-results/${extractionId}/records`, {
          params: { page: 1, page_size: API_PAGE_SIZE },
        });
        if (cancelled) return;
        const d = res.data;
        if (d?.status !== 'success') {
          setError('Could not load extraction.');
          setPreviewData([]);
          setMetadata(null);
          setTotalRecordCount(0);
          return;
        }
        const meta = d.metadata || {};
        setDownloadDisabled(meta.detail_expired === true || meta.detail_available === false);
        const total = Number(d.total_count ?? meta.record_count ?? 0);
        fullRecordCountRef.current = total;
        setPreviewData(d.records || []);
        setTotalRecordCount(total);
        setMetadata(meta);
      } catch (e) {
        if (cancelled) return;
        if (e.response?.status === 410) {
          try {
            const r = await api.get(`/extraction-results/${extractionId}`);
            const result = r.data?.result;
            if (cancelled) return;
            setPreviewData([]);
            setMetadata(result || {});
            setTotalRecordCount(Number(result?.record_count ?? 0));
            setDownloadDisabled(true);
            setError(null);
          } catch (e2) {
            setError(e2.response?.data?.detail || e2.message || 'Failed to load extraction');
            setPreviewData([]);
            setMetadata(null);
            setTotalRecordCount(0);
          }
        } else {
          setError(e.response?.data?.detail || e.message || 'Failed to load extraction');
          setPreviewData([]);
          setMetadata(null);
          setTotalRecordCount(0);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [extractionId]);

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

  const onFetchPage = useCallback(async (jobId, page, searchQuery = '') => {
    try {
      const params = { page, page_size: API_PAGE_SIZE };
      const q = (searchQuery || '').trim();
      if (q) params.q = q;
      const res = await api.get(`/extraction-results/${jobId}/records`, { params });
      const d = res.data;
      if (d?.status === 'success' && d.records) {
        const meta = d.metadata || {};
        setDownloadDisabled(meta.detail_expired === true || meta.detail_available === false);
        setTotalRecordCount(
          q ? (d.total_count ?? 0) : (fullRecordCountRef.current || (d.total_count ?? 0)),
        );
        setPreviewData(d.records);
      }
    } catch (err) {
      if (err.response?.status === 410) setDownloadDisabled(true);
      throw err;
    }
  }, []);

  const downloadFullCsv = useCallback(async (jobId, documentType) => {
    const safeBase = (documentType || 'extracted-data')
      .replace(/[^a-z0-9-_]+/gi, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 48) || 'extracted-data';
    const res = await api.get(`/extraction-results/${jobId}/export.csv`, {
      responseType: 'blob',
    });
    const blob = res.data;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${safeBase}-${jobId}.csv`;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, []);

  const meta = metadata || {};
  const sourceType = meta.source_type || '';
  const documentType = meta.document_type || 'Extracted data';
  const documentDescription = meta.document_description || '';
  const sourceName = meta.source_name || '';

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
          <FiBarChart2 style={{ marginRight: 8, verticalAlign: 'middle' }} />
          Extracted data{flowName ? ` — ${flowName}` : ''}
        </h2>
      </div>

      <div className="flow-detail-body">
        {loading && <p style={{ padding: '1rem' }}>Loading…</p>}
        {error && (
          <div className="error-state flow-detail-error">
            <p>{error}</p>
          </div>
        )}
        {!loading && !error && totalRecordCount === 0 && !downloadDisabled && (
          <p style={{ padding: '1rem', color: '#6b7280' }}>No extracted records for this run.</p>
        )}
        {!loading && !error && downloadDisabled && previewData.length === 0 && (
          <p style={{ padding: '1rem', color: '#6b7280' }}>
            Row-level extraction detail is no longer available (retention policy). CSV download is disabled.
          </p>
        )}
        {!loading && !error && previewData.length > 0 && (
          <div className="flow-full-width-table-shell">
            <InlineDataPreview
              jobId={extractionId}
              data={previewData}
              documentType={documentType}
              documentDescription={documentDescription}
              recordCount={totalRecordCount}
              sourceType={sourceType}
              sourceName={sourceName}
              isPaginated={isPaginated}
              loadedCount={previewData.length}
              totalRecordCount={totalRecordCount}
              fullRecordCount={fullRecordCountRef.current || totalRecordCount}
              apiPageSize={API_PAGE_SIZE}
              onFetchPage={onFetchPage}
              onDownloadFullCsv={downloadFullCsv}
              downloadDisabled={downloadDisabled}
              initialCollapsed={false}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default FlowExtractionViewPage;
