import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { createPortal } from 'react-dom';
import { FiChevronDown, FiChevronUp, FiBarChart2, FiSearch, FiDownload, FiMaximize2, FiMinimize2, FiX } from 'react-icons/fi';
import './InlineDataPreview.css';
import DestinationTargetPicker from './DestinationTargetPicker';
import { isHiddenExtractionMetadataKey } from '../utils/extractionPreviewHiddenKeys';
import {
  destinationTargetValueForType,
  labelForDestinationType,
  normalizeDestType,
} from '../utils/destinationTargetOptions';

const VIRTUALIZATION_THRESHOLD = 50;
const ROW_HEIGHT = 36;
/** Above this count, download uses server export (full extraction_result_records) instead of in-memory CSV. */
const FULL_CSV_EXPORT_THRESHOLD = 500;

/** Fallback when container width is not measured yet */
const DEFAULT_COL_WIDTH = 160;
/** Minimum column width (readable text + drag limit); user can widen further */
const MIN_COL_WIDTH = 120;
const MAX_COL_WIDTH = 640;
/** Stored in colWidths under this key (not a data field name) */
const INDEX_COL_KEY = '__rowIndex__';
const DEFAULT_INDEX_COL_WIDTH = 52;
const MIN_INDEX_COL_WIDTH = 40;
const MAX_INDEX_COL_WIDTH = 220;

const camelCaseToLabel = (str) =>
  str
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^./, (char) => char.toUpperCase());

const InlineDataPreview = ({ 
  jobId, 
  data, 
  documentType, 
  documentDescription, 
  recordCount,
  sourceType,
  sourceName,
  destination,
  destinationName,
  destinationObjectApiName,
  destinationTableName,
  destinationSheetName,
  onMapToSalesforce,
  onExpand,
  isCollapsed: initialCollapsed = false,
  onMappingStart,
  onMappingEnd,
  isTransformedData = false,
  /** When true, we only have first page loaded (paginated mode) */
  isPaginated = false,
  /** Number of records currently loaded (when isPaginated) */
  loadedCount,
  /** Total records across all API pages (when isPaginated) */
  totalRecordCount,
  /** Full dataset size (for search placeholder when paginated) */
  fullRecordCount,
  /** API page size (e.g. 500) */
  apiPageSize = 500,
  /** Callback to fetch a specific API page: (jobId, page) => Promise */
  onFetchPage,
  /** When record count > FULL_CSV_EXPORT_THRESHOLD, called to download full CSV from GET /extraction-results/{jobId}/export.csv */
  onDownloadFullCsv,
  /** When true (e.g. data retention), CSV download is disabled */
  downloadDisabled = false,
  /** Tooltip when downloadDisabled */
  downloadDisabledTitle = 'Download is not available — row-level data is no longer stored (retention policy).',
  /** Optional ref for auto-click (programmatic trigger when Auto mode is on) */
  mapButtonRef,
  requiresDestination = false,
  onDestinationPick,
  mapDisabled = false,
}) => {
  const canManageDestination = Boolean(onDestinationPick);
  const destinationTarget = useMemo(
    () =>
      destinationTargetValueForType(destination, {
        destinationObjectApiName,
        destinationTableName,
        destinationSheetName,
      }),
    [
      destination,
      destinationObjectApiName,
      destinationTableName,
      destinationSheetName,
    ],
  );
  const destinationIsSet = Boolean(
    destination && destination !== 'unknown' && (destinationName || destinationTarget),
  );
  const [editingDestination, setEditingDestination] = useState(
    () => canManageDestination && requiresDestination,
  );
  useEffect(() => {
    if (requiresDestination) {
      setEditingDestination(true);
    }
  }, [requiresDestination]);
  const showDestinationPicker =
    canManageDestination && (requiresDestination || editingDestination);
  const showDestinationSummary =
    canManageDestination && destinationIsSet && !showDestinationPicker;
  const destinationTypeLabel = labelForDestinationType(normalizeDestType(destination));

  const [isCollapsed, setIsCollapsed] = useState(initialCollapsed);
  const [currentPage, setCurrentPage] = useState(0);
  const [isMapping, setIsMapping] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortColumn, setSortColumn] = useState(null);
  const [sortDirection, setSortDirection] = useState('asc');
  const [apiPage, setApiPage] = useState(1);
  const [apiPageLoading, setApiPageLoading] = useState(false);
  const [fullCsvDownloading, setFullCsvDownloading] = useState(false);
  /** Per data-column key → width in px (Excel-style resize). */
  const [colWidths, setColWidths] = useState({});
  /** Scroll area width — used to split space for default column widths when few columns exist */
  const [scrollContainerWidth, setScrollContainerWidth] = useState(0);
  const [previewTableExpanded, setPreviewTableExpanded] = useState(false);
  /** Client-side table pages — align with API extraction page size (500). */
  const pageSize = apiPageSize;

  const totalApiPages = useMemo(() => {
    if (!isPaginated || !totalRecordCount || !apiPageSize) return 0;
    return Math.ceil(totalRecordCount / apiPageSize);
  }, [isPaginated, totalRecordCount, apiPageSize]);

  const rowOffset = isPaginated ? (apiPage - 1) * apiPageSize : 0;

  const serverSearchEnabled = isPaginated && Boolean(onFetchPage && jobId);

  const handleFetchApiPage = useCallback(async (page, searchOverride) => {
    if (!onFetchPage || !jobId || page < 1 || page > totalApiPages) return;
    setApiPageLoading(true);
    try {
      const q =
        searchOverride !== undefined
          ? searchOverride
          : serverSearchEnabled
            ? searchQuery
            : '';
      await onFetchPage(jobId, page, q);
      setApiPage(page);
      setCurrentPage(0);
    } catch (err) {
      console.error('Failed to fetch extraction page:', err);
    } finally {
      setApiPageLoading(false);
    }
  }, [onFetchPage, jobId, totalApiPages, serverSearchEnabled, searchQuery]);

  const getColumnKeys = useCallback(() => {
    if (!data || !Array.isArray(data) || data.length === 0) return [];
    const visibleKey = (k) => k && typeof k === 'string' && !isHiddenExtractionMetadataKey(k);
    // Filter out internal/metadata fields (starting with '_') from display
    const orderedFromFirst = Object.keys(data[0] || {}).filter(visibleKey);
    const union = new Set(orderedFromFirst);
    for (const r of data) {
      Object.keys(r || {}).filter(visibleKey).forEach((k) => union.add(k));
    }
    const extras = Array.from(union).filter((k) => !orderedFromFirst.includes(k)).sort();
    return [...orderedFromFirst, ...extras];
  }, [data]);

  const escapeCsvCell = useCallback((value) => {
    if (value === null || value === undefined) return '';
    let s;
    if (typeof value === 'object') {
      try {
        s = JSON.stringify(value);
      } catch {
        s = String(value);
      }
    } else {
      s = String(value);
    }
    if (/[",\n\r]/.test(s)) {
      return `"${s.replace(/"/g, '""')}"`;
    }
    return s;
  }, []);

  const handleDownloadCsvClient = useCallback(() => {
    if (!data || !Array.isArray(data) || data.length === 0) return;
    const cols = getColumnKeys();
    const header = cols.map((c) => escapeCsvCell(camelCaseToLabel(c))).join(',');
    const lines = data.map((record) =>
      cols
        .map((col) => {
          const v = record[col];
          return escapeCsvCell(v);
        })
        .join(',')
    );
    const csv = `\uFEFF${header}\r\n${lines.join('\r\n')}`;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const safeBase = (documentType || 'extracted-data')
      .replace(/[^a-z0-9-_]+/gi, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 48) || 'extracted-data';
    a.download = `${safeBase}-${jobId || 'export'}.csv`;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [data, documentType, jobId, escapeCsvCell, getColumnKeys]);

  const totalRecordsForExport =
    totalRecordCount ?? recordCount ?? (Array.isArray(data) ? data.length : 0);
  const useServerFullExport =
    !downloadDisabled &&
    totalRecordsForExport > FULL_CSV_EXPORT_THRESHOLD &&
    Boolean(jobId && onDownloadFullCsv);

  const handleDownloadClick = useCallback(async () => {
    if (downloadDisabled) return;
    if (useServerFullExport) {
      setFullCsvDownloading(true);
      try {
        await onDownloadFullCsv(jobId, documentType);
      } catch (err) {
        console.error('Full CSV export failed:', err);
      } finally {
        setFullCsvDownloading(false);
      }
      return;
    }
    handleDownloadCsvClient();
  }, [
    useServerFullExport,
    onDownloadFullCsv,
    jobId,
    documentType,
    handleDownloadCsvClient,
    downloadDisabled,
  ]);

  // Helper function to count empty values in a record
  const countEmpty = useCallback((record) => {
    if (!record) return 0;
    const columns = getColumnKeys();
    let emptyCount = 0;
    columns.forEach((col) => {
      const value = record[col];
      if (value === null || value === undefined || value === '' || 
          (typeof value === 'string' && value.trim() === '')) {
        emptyCount++;
      }
    });
    return emptyCount;
  }, [getColumnKeys]);

  // Filter by search query (client-side on loaded rows unless server search is active)
  const filteredData = useMemo(() => {
    if (!data || !Array.isArray(data) || data.length === 0) return [];
    if (serverSearchEnabled) return data;
    const cols = getColumnKeys();
    const q = (searchQuery || '').trim().toLowerCase();
    if (!q) return data;
    return data.filter((record) =>
      cols.some((col) =>
        String(record[col] ?? '').toLowerCase().includes(q)
      )
    );
  }, [data, searchQuery, getColumnKeys, serverSearchEnabled]);

  const searchDebounceSkippedRef = useRef(false);
  // Server-side search across all stored rows (paginated jobs)
  useEffect(() => {
    if (!serverSearchEnabled) return undefined;
    if (!searchDebounceSkippedRef.current) {
      searchDebounceSkippedRef.current = true;
      return undefined;
    }
    const timer = setTimeout(() => {
      handleFetchApiPage(1, searchQuery);
    }, 400);
    return () => clearTimeout(timer);
  }, [searchQuery, serverSearchEnabled]); // eslint-disable-line react-hooks/exhaustive-deps

  // Compare two values for column sort (numbers, dates, strings; nulls last)
  const compareValues = useCallback((va, vb) => {
    const aNull = va == null;
    const bNull = vb == null;
    if (aNull && bNull) return 0;
    if (aNull) return 1;
    if (bNull) return -1;
    if (typeof va === 'number' && typeof vb === 'number') return va - vb;
    const da = new Date(va);
    const db = new Date(vb);
    if (!Number.isNaN(da.getTime()) && !Number.isNaN(db.getTime())) return da.getTime() - db.getTime();
    return String(va).toLowerCase().localeCompare(String(vb).toLowerCase());
  }, []);

  // Sort: by selected column (asc/desc) or by empty count (default)
  const sortedData = useMemo(() => {
    if (!filteredData || filteredData.length === 0) return [];
    if (sortColumn) {
      const sorted = [...filteredData].sort((a, b) =>
        compareValues(a[sortColumn], b[sortColumn])
      );
      return sortDirection === 'desc' ? sorted.reverse() : sorted;
    }
    return [...filteredData].sort((a, b) => countEmpty(b) - countEmpty(a));
  }, [filteredData, sortColumn, sortDirection, countEmpty, compareValues]);

  // Reset to first page when search or sort changes
  useEffect(() => {
    setCurrentPage(0);
  }, [searchQuery, sortColumn, sortDirection]);

  // Find the record with the lowest empty values (most complete) for mapping
  // This ensures we send the best record to the backend for field mapping
  const bestRecordForMapping = useMemo(() => {
    if (!data || !Array.isArray(data) || data.length === 0) return null;
    
    // Find record with minimum empty values
    return data.reduce((best, current) => {
      const bestEmpty = countEmpty(best);
      const currentEmpty = countEmpty(current);
      return currentEmpty < bestEmpty ? current : best;
    }, data[0]);
  }, [data, countEmpty]);

  const totalPages = Math.ceil(sortedData.length / pageSize);
  const paginatedData = sortedData.slice(currentPage * pageSize, (currentPage + 1) * pageSize);

  const useVirtual = sortedData.length > VIRTUALIZATION_THRESHOLD;
  const tableWrapperRef = useRef(null);
  const rowVirtualizer = useVirtualizer({
    count: useVirtual ? sortedData.length : 0,
    getScrollElement: () => tableWrapperRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  });

  const handleSort = useCallback((col) => {
    setSortColumn((prev) => {
      if (prev === col) {
        setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
        return col;
      }
      setSortDirection('asc');
      return col;
    });
  }, []);

  const columns = useMemo(() => getColumnKeys(), [getColumnKeys]);
  const columnsKey = useMemo(() => columns.join('\0'), [columns]);

  useEffect(() => {
    const el = tableWrapperRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width;
      if (w != null && w > 0) setScrollContainerWidth(Math.round(w));
    });
    ro.observe(el);
    setScrollContainerWidth(Math.round(el.getBoundingClientRect().width));
    return () => ro.disconnect();
  }, [data, isCollapsed, columnsKey]);

  const widthForIndex = useCallback(
    () => colWidths[INDEX_COL_KEY] ?? DEFAULT_INDEX_COL_WIDTH,
    [colWidths]
  );

  /** Equal share of horizontal space when user has not resized a column (fills wide layouts). */
  const baseColumnWidth = useMemo(() => {
    const n = columns.length;
    if (n === 0) return DEFAULT_COL_WIDTH;
    const w = scrollContainerWidth;
    if (w <= 0) return DEFAULT_COL_WIDTH;
    const idxW = colWidths[INDEX_COL_KEY] ?? DEFAULT_INDEX_COL_WIDTH;
    const usable = Math.max(0, w - idxW);
    const perCol = Math.floor(usable / n);
    return Math.min(MAX_COL_WIDTH, Math.max(MIN_COL_WIDTH, perCol));
  }, [columns.length, scrollContainerWidth, colWidths]);

  const widthFor = useCallback(
    (col) => colWidths[col] ?? baseColumnWidth,
    [colWidths, baseColumnWidth]
  );

  const gridTemplateColumns = useMemo(
    () =>
      `${widthForIndex()}px ${columns.map((c) => `${widthFor(c)}px`).join(' ')}`,
    [columns, widthFor, widthForIndex]
  );

  useEffect(() => {
    setColWidths({});
  }, [columnsKey]);

  const beginResize = useCallback((e, col) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const isIndex = col === INDEX_COL_KEY;
    const minW = isIndex ? MIN_INDEX_COL_WIDTH : MIN_COL_WIDTH;
    const maxW = isIndex ? MAX_INDEX_COL_WIDTH : MAX_COL_WIDTH;
    let startW = isIndex ? DEFAULT_INDEX_COL_WIDTH : baseColumnWidth;
    setColWidths((prev) => {
      startW = prev[col] ?? (isIndex ? DEFAULT_INDEX_COL_WIDTH : baseColumnWidth);
      return prev;
    });
    const onMove = (moveEvent) => {
      const dx = moveEvent.clientX - startX;
      const next = Math.min(maxW, Math.max(minW, startW + dx));
      setColWidths((p) => ({ ...p, [col]: next }));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [baseColumnWidth]);

  useEffect(() => {
    if (!previewTableExpanded) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setPreviewTableExpanded(false);
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [previewTableExpanded]);

  const renderTablePreviewChrome = (inOverlay) => (
          <div
            className={`inline-data-preview-table-wrapper${useVirtual ? ' inline-data-preview-table-wrapper-virtual' : ''}${
              inOverlay ? ' inline-data-preview-table-wrapper--overlay' : ''
            }`}
          >
            <div className="inline-data-preview-search">
              <button
                type="button"
                className="inline-data-preview-download-btn"
                onClick={handleDownloadClick}
                disabled={fullCsvDownloading || downloadDisabled}
                aria-label={
                  downloadDisabled
                    ? 'Download unavailable — data no longer stored'
                    : useServerFullExport
                      ? 'Download full extracted data as CSV from server'
                      : 'Download extracted data as CSV'
                }
                title={
                  downloadDisabled
                    ? downloadDisabledTitle
                    : fullCsvDownloading
                      ? 'Preparing download…'
                      : useServerFullExport
                        ? `Download all ${totalRecordsForExport} records as CSV (from server)`
                        : isPaginated
                          ? 'Download loaded rows as CSV (current data page)'
                          : 'Download extracted data as CSV'
                }
              >
                <FiDownload aria-hidden />
              </button>
              <button
                type="button"
                className="inline-data-preview-download-btn"
                onClick={() => (inOverlay ? setPreviewTableExpanded(false) : setPreviewTableExpanded(true))}
                aria-label={inOverlay ? 'Exit expanded table view' : 'Expand table preview'}
                title={inOverlay ? 'Exit expanded view' : 'Expand table preview'}
              >
                {inOverlay ? <FiMinimize2 aria-hidden /> : <FiMaximize2 aria-hidden />}
              </button>
              <FiSearch className="inline-data-preview-search-icon" aria-hidden />
              <input
                type="text"
                className="inline-data-preview-search-input"
                placeholder={
                  serverSearchEnabled && (fullRecordCount ?? totalRecordCount) != null
                    ? `Search all ${fullRecordCount ?? totalRecordCount} records…`
                    : 'Search loaded rows…'
                }
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                aria-label={
                  serverSearchEnabled ? 'Search all records' : 'Search in loaded table rows'
                }
              />
            </div>
            {isPaginated && totalApiPages > 1 && onFetchPage && (
              <div className="inline-data-preview-api-pagination">
                <button
                  type="button"
                  onClick={() => handleFetchApiPage(apiPage - 1)}
                  disabled={apiPage <= 1 || apiPageLoading}
                  className="inline-data-preview-pagination-btn"
                >
                  ← Previous page
                </button>
                <span className="inline-data-preview-pagination-info">
                  Data page {apiPage} of {totalApiPages}
                  {apiPageLoading && ' (loading…)'}
                </span>
                <button
                  type="button"
                  onClick={() => handleFetchApiPage(apiPage + 1)}
                  disabled={apiPage >= totalApiPages || apiPageLoading}
                  className="inline-data-preview-pagination-btn"
                >
                  Next page →
                </button>
              </div>
            )}
            <div ref={tableWrapperRef} className="inline-data-preview-table-scroll">
            {useVirtual ? (
              <div
                className="inline-data-preview-virtual-grid"
                style={{
                  display: 'grid',
                  gridTemplateColumns,
                }}
              >
                <div className="inline-data-preview-virtual-header inline-data-preview-virtual-header-index inline-data-preview-virtual-th-wrap">
                  <span className="inline-data-preview-th-label">#</span>
                  <button
                    type="button"
                    className="inline-data-preview-resize-handle"
                    onMouseDown={(e) => beginResize(e, INDEX_COL_KEY)}
                    onClick={(e) => e.stopPropagation()}
                    tabIndex={-1}
                    aria-label="Resize row number column"
                  />
                </div>
                {columns.map((col) => (
                  <div
                    key={col}
                    className="inline-data-preview-virtual-header inline-data-preview-th-sortable inline-data-preview-virtual-th-wrap"
                    onClick={() => handleSort(col)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        handleSort(col);
                      }
                    }}
                    role="columnheader"
                    tabIndex={0}
                    aria-sort={
                      sortColumn === col
                        ? sortDirection === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : 'none'
                    }
                  >
                    <span className="inline-data-preview-th-content">
                      <span className="inline-data-preview-th-label" title={camelCaseToLabel(col)}>
                        {camelCaseToLabel(col)}
                      </span>
                      {sortColumn === col ? (
                        sortDirection === 'asc' ? (
                          <FiChevronUp className="inline-data-preview-sort-icon" aria-hidden />
                        ) : (
                          <FiChevronDown className="inline-data-preview-sort-icon" aria-hidden />
                        )
                      ) : (
                        <span className="inline-data-preview-sort-placeholder" aria-hidden />
                      )}
                    </span>
                    <button
                      type="button"
                      className="inline-data-preview-resize-handle"
                      onMouseDown={(e) => beginResize(e, col)}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label={`Resize column ${camelCaseToLabel(col)}`}
                    />
                  </div>
                ))}
                <div
                  style={{
                    gridColumn: `1 / -1`,
                    height: rowVirtualizer.getTotalSize(),
                    position: 'relative',
                  }}
                >
                  {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                    const record = sortedData[virtualRow.index];
                    return (
                      <div
                        key={virtualRow.key}
                        className="inline-data-preview-virtual-row"
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          height: `${virtualRow.size}px`,
                          transform: `translateY(${virtualRow.start}px)`,
                          display: 'grid',
                          gridTemplateColumns,
                          alignItems: 'stretch',
                        }}
                      >
                        <div className="inline-data-preview-virtual-cell inline-data-preview-virtual-cell-index">
                          {rowOffset + virtualRow.index + 1}
                        </div>
                        {columns.map((col) => (
                          <div
                            key={`cell-${virtualRow.index}-${col}`}
                            className="inline-data-preview-virtual-cell"
                          >
                            {record[col] !== null && record[col] !== undefined
                              ? String(record[col])
                              : ''}
                          </div>
                        ))}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <table className="inline-data-preview-table">
                <thead>
                  <tr>
                    <th
                      className="inline-data-preview-th-index inline-data-preview-th-resizable"
                      style={{
                        width: widthForIndex(),
                        minWidth: MIN_INDEX_COL_WIDTH,
                        maxWidth: MAX_INDEX_COL_WIDTH,
                      }}
                    >
                      <span className="inline-data-preview-th-content">
                        <span className="inline-data-preview-th-label">#</span>
                      </span>
                      <button
                        type="button"
                        className="inline-data-preview-resize-handle"
                        onMouseDown={(e) => beginResize(e, INDEX_COL_KEY)}
                        onClick={(e) => e.stopPropagation()}
                        tabIndex={-1}
                        aria-label="Resize row number column"
                      />
                    </th>
                    {columns.map((col) => (
                      <th
                        key={col}
                        className="inline-data-preview-th-sortable inline-data-preview-th-resizable"
                        style={{
                          width: widthFor(col),
                          minWidth: MIN_COL_WIDTH,
                          maxWidth: MAX_COL_WIDTH,
                        }}
                        onClick={() => handleSort(col)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            handleSort(col);
                          }
                        }}
                        tabIndex={0}
                        aria-sort={
                          sortColumn === col
                            ? sortDirection === 'asc'
                              ? 'ascending'
                              : 'descending'
                            : 'none'
                        }
                      >
                        <span className="inline-data-preview-th-content">
                          <span className="inline-data-preview-th-label" title={camelCaseToLabel(col)}>
                            {camelCaseToLabel(col)}
                          </span>
                          {sortColumn === col ? (
                            sortDirection === 'asc' ? (
                              <FiChevronUp className="inline-data-preview-sort-icon" aria-hidden />
                            ) : (
                              <FiChevronDown className="inline-data-preview-sort-icon" aria-hidden />
                            )
                          ) : (
                            <span className="inline-data-preview-sort-placeholder" aria-hidden />
                          )}
                        </span>
                        <button
                          type="button"
                          className="inline-data-preview-resize-handle"
                          onMouseDown={(e) => beginResize(e, col)}
                          onClick={(e) => e.stopPropagation()}
                          tabIndex={-1}
                          aria-label={`Resize column ${camelCaseToLabel(col)}`}
                        />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {paginatedData.map((record, idx) => {
                    const globalIdx = currentPage * pageSize + idx;
                    return (
                      <tr key={`row-${globalIdx}`}>
                        <td
                          className="inline-data-preview-td-index"
                          style={{
                            width: widthForIndex(),
                            minWidth: MIN_INDEX_COL_WIDTH,
                            maxWidth: MAX_INDEX_COL_WIDTH,
                          }}
                        >
                          {rowOffset + globalIdx + 1}
                        </td>
                        {columns.map((col) => (
                          <td
                            key={`cell-${globalIdx}-${col}`}
                            style={{
                              width: widthFor(col),
                              minWidth: MIN_COL_WIDTH,
                              maxWidth: MAX_COL_WIDTH,
                            }}
                          >
                            {record[col] !== null && record[col] !== undefined
                              ? String(record[col])
                              : ''}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            </div>
          </div>
  );

  // Early return after all hooks — retention: show summary + message when rows are purged but counts remain
  const recordsReported = totalRecordCount ?? recordCount ?? 0;
  const showRetentionSummaryOnly =
    downloadDisabled &&
    recordsReported > 0 &&
    (!data || !Array.isArray(data) || data.length === 0);

  if (showRetentionSummaryOnly) {
    return (
      <div className="inline-data-preview">
        <div className="inline-data-preview-header">
          <div className="inline-data-preview-title">
            <span className="inline-data-preview-icon"><FiBarChart2 /></span>
            <span>
              <strong>{isTransformedData ? 'Transformed Data' : 'Extracted Data'}</strong>
              <span className="inline-data-preview-count"> ({recordsReported} records)</span>
            </span>
          </div>
        </div>
        <div className="inline-data-preview-content" style={{ paddingTop: '8px' }}>
          <div
            style={{
              padding: '12px',
              backgroundColor: '#fee2e2',
              border: '1px solid #fecaca',
              borderRadius: '6px',
              color: '#991b1b',
              fontSize: '13px',
            }}
          >
            Extraction record detail is no longer available (retention policy).
          </div>
        </div>
      </div>
    );
  }

  if (!data || !Array.isArray(data) || data.length === 0) {
    return null;
  }

  return (
    <div className="inline-data-preview">
      <div 
        className="inline-data-preview-header"
        onClick={() => {
          const wasCollapsed = isCollapsed;
          setIsCollapsed(!isCollapsed);
          // Call onExpand when user expands (if provided)
          if (wasCollapsed && !isCollapsed && onExpand) {
            onExpand();
          }
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            const wasCollapsed = isCollapsed;
            setIsCollapsed(!isCollapsed);
            if (wasCollapsed && !isCollapsed && onExpand) {
              onExpand();
            }
          }
        }}
      >
        <div className="inline-data-preview-title">
          <span className="inline-data-preview-icon"><FiBarChart2 /></span>
          <span>
            <strong>{isTransformedData ? 'Transformed Data' : 'Extracted Data'}</strong>
            {recordCount !== undefined && (
              <span className="inline-data-preview-count">
                {' '}({isPaginated && totalRecordCount != null
                  ? `Showing records ${1 + (apiPage - 1) * (apiPageSize || 500)}–${Math.min(apiPage * (apiPageSize || 500), totalRecordCount)} of ${totalRecordCount}`
                  : `${recordCount} records`})
              </span>
            )}
          </span>
        </div>
        <div className="inline-data-preview-toggle">
          {isCollapsed ? <FiChevronDown /> : <FiChevronUp />}
        </div>
      </div>

      {!isCollapsed && (
        <div className="inline-data-preview-content">
          {(documentType || sourceType) && (
            <div className="inline-data-preview-meta">
              {/* <span><strong>Type:</strong> {documentType}</span> */}
              {/* {documentDescription && (
                <span><strong>Description:</strong> {documentDescription}</span>
              )} */}
              {sourceType && (
                <span><strong>Source:</strong> {sourceName ? `${sourceName} (${sourceType})` : sourceType}</span>
              )}
            </div>
          )}


          {!previewTableExpanded && renderTablePreviewChrome(false)}
          {previewTableExpanded &&
            createPortal(
              <div className="inline-data-preview-overlay-root">
                <div
                  className="inline-data-preview-overlay-backdrop"
                  onClick={() => setPreviewTableExpanded(false)}
                  aria-hidden
                />
                <div
                  className="inline-data-preview-overlay-panel"
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="inline-data-preview-overlay-heading"
                >
                  <div className="inline-data-preview-overlay-topbar">
                    <h2 id="inline-data-preview-overlay-heading" className="inline-data-preview-overlay-heading">
                      {isTransformedData ? 'Transformed data' : 'Extracted data'}
                    </h2>
                    <button
                      type="button"
                      className="inline-data-preview-overlay-close"
                      onClick={() => setPreviewTableExpanded(false)}
                      aria-label="Close expanded preview"
                    >
                      <FiX aria-hidden />
                    </button>
                  </div>
                  {renderTablePreviewChrome(true)}
                </div>
              </div>,
              document.body
            )}

          {totalPages > 1 && !useVirtual && (
            <div className="inline-data-preview-pagination">
              <button
                onClick={() => setCurrentPage((prev) => Math.max(prev - 1, 0))}
                disabled={currentPage === 0}
                className="inline-data-preview-pagination-btn"
              >
                ← Previous
              </button>
              <span className="inline-data-preview-pagination-info">
                Page {currentPage + 1} of {totalPages}
              </span>
              <button
                onClick={() => setCurrentPage((prev) => Math.min(prev + 1, totalPages - 1))}
                disabled={currentPage >= totalPages - 1}
                className="inline-data-preview-pagination-btn"
              >
                Next →
              </button>
            </div>
          )}

          {showDestinationSummary && (
            <div className="inline-data-preview-destination-summary">
              <span className="inline-data-preview-destination-label">Destination</span>
              <span className="inline-data-preview-destination-value">
                {destinationName || destinationTypeLabel}
                {destinationTarget ? ` · ${destinationTarget}` : ''}
              </span>
              <button
                type="button"
                className="inline-data-preview-destination-edit-btn"
                onClick={() => setEditingDestination(true)}
              >
                Change
              </button>
            </div>
          )}
          {showDestinationPicker && onDestinationPick && (
            <DestinationTargetPicker
              variant="preview"
              title={destinationIsSet ? 'Change destination' : 'Choose destination'}
              submitLabel="Apply"
              initialDestType={destination}
              initialDestName={destinationName || ''}
              initialDestTarget={destinationTarget}
              onSubmit={async ({ destType, destName, destTarget }) => {
                await onDestinationPick(destType, destName, destTarget);
                setEditingDestination(false);
              }}
            />
          )}

          <div className="inline-data-preview-actions">
            {onMapToSalesforce && (() => {
              // Determine button label based on destination (similar to DataTablePage.js)
              let buttonLabel = 'Map to Salesforce'; // Default fallback
              let loadingLabel = 'Mapping...'; // Default loading text
              
              if (destination === 'google_sheets') {
                buttonLabel = 'Map to Google Sheets';
                loadingLabel = 'Mapping to Google Sheets...';
              } else if (destination === 'sql') {
                buttonLabel = 'Map to Database';
                loadingLabel = 'Mapping to Database...';
              } else if (destination === 'salesforce') {
                buttonLabel = 'Map to Salesforce';
                loadingLabel = 'Mapping to Salesforce...';
              } else {
                buttonLabel = 'Map';
                loadingLabel = 'Mapping...';
              }
              
              const handleMapClick = async () => {
                setIsMapping(true);
                
                // Show loader with mapping message
                if (onMappingStart) {
                  const destinationName = destination === 'google_sheets' 
                    ? 'Google Sheets' 
                    : destination === 'sql' 
                    ? 'Database' 
                    : 'Salesforce';
                  onMappingStart(`Mapping data to ${destinationName}`);
                }
                
                try {
                  // Pass the best record (lowest empty values) for mapping to backend
                  // This ensures we send the most complete record for field mapping,
                  // which helps preserve custom fields that might have empty values in other records
                  const recordForMapping = bestRecordForMapping || sortedData[0] || data[0];
                  await onMapToSalesforce(recordForMapping, sortedData.length > 0 ? sortedData : data);
                } catch (error) {
                  console.error('Mapping error:', error);
                  // Hide loader on error
                  if (onMappingEnd) {
                    onMappingEnd();
                  }
                } finally {
                  setIsMapping(false);
                  // Hide loader when mapping completes successfully
                  // Note: onMappingEnd will be called from parent after mapping panel is added
                }
              };
              
              return (
                <button
                  ref={mapButtonRef}
                  onClick={handleMapClick}
                  className="inline-data-preview-action-btn primary"
                  disabled={isMapping || mapDisabled || requiresDestination}
                  title={requiresDestination ? 'Choose a destination first' : undefined}
                >
                  {isMapping ? loadingLabel : buttonLabel}
                </button>
              );
            })()}
          </div>
        </div>
      )}
    </div>
  );
};

export default InlineDataPreview;

