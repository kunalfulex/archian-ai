import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { FiChevronDown, FiChevronUp, FiBarChart2, FiSearch, FiDownload, FiMaximize2, FiMinimize2, FiX } from 'react-icons/fi';
import {
  isHiddenExtractionMetadataKey,
  omitHiddenExtractionRecordKeys,
} from '../utils/extractionPreviewHiddenKeys';
import './InlineMigrationRecords.css';

const DEFAULT_COL_WIDTH = 160;
const MIN_COL_WIDTH = 120;
const MAX_COL_WIDTH = 640;
const SR_COL_KEY = '__sr__';
const DEFAULT_SR_WIDTH = 52;
const MIN_SR_WIDTH = 40;
const MAX_SR_WIDTH = 220;

const camelCaseToLabel = (str) =>
  str
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^./, (char) => char.toUpperCase());

const getStatusClass = (status) => {
  const s = (status || '').toLowerCase();
  if (s === 'created' || s === 'success') return 'status-badge status-created';
  if (s === 'updated') return 'status-badge status-updated';
  if (s === 'rolled_back') return 'status-badge status-rolled-back';
  if (s === 'skipped') return 'status-badge status-skipped';
  if (s === 'error' || s === 'failed') return 'status-badge status-error';
  return 'status-badge';
};

const formatStatusText = (status) => {
  const s = (status || '').toLowerCase();
  if (s === 'rolled_back') return 'ROLLED BACK';
  if (s === 'created') return 'CREATED';
  if (s === 'updated') return 'UPDATED';
  if (s === 'skipped') return 'SKIPPED';
  if (s === 'error' || s === 'failed') return 'ERROR';
  return status?.toUpperCase() || 'UNKNOWN';
};

const getDestinationHeaders = (destination) => {
  switch(destination) {
    case 'salesforce':
      return {
        primaryLabel: 'Salesforce Object',
        primaryKey: 'object'
      };
    case 'google_sheets':
      return {
        primaryLabel: 'Sheet Name',
        primaryKey: 'sheet'
      };
    case 'sql':
      return {
        primaryLabel: 'Table Name',
        primaryKey: 'table'
      };
    default:
      return {
        primaryLabel: 'Object/Sheet/Table',
        primaryKey: 'object'
      };
  }
};

/** Parse salesforce_results from API (object or JSON string). */
const parseSalesforceResults = (raw) => {
  if (!raw) return null;
  let sr = raw;
  if (typeof sr === 'string') {
    try {
      sr = JSON.parse(sr);
    } catch {
      return null;
    }
  }
  if (typeof sr !== 'object' || sr === null) return null;
  const entries = Object.entries(sr).filter(([k]) => k !== 'error');
  if (entries.length === 0) return null;
  const normalized = {};
  entries.forEach(([key, val]) => {
    if (typeof val === 'object' && val !== null) {
      normalized[key] = val;
    }
  });
  return Object.keys(normalized).length > 0 ? normalized : null;
};

const primaryObjectFromMapping = (mapping) => {
  if (!mapping || typeof mapping !== 'object') return null;
  for (const val of Object.values(mapping)) {
    if (Array.isArray(val)) {
      const obj = val.find((item) => item?.object)?.object;
      if (obj) return obj;
    } else if (val && typeof val === 'object' && val.object) {
      return val.object;
    }
  }
  return null;
};

const getRecordViewLink = (result, destination, migrationInstanceURL, destinationSpreadsheetId) => {
  if (result?.view_link && typeof result.view_link === 'string') {
    return result.view_link;
  }
  if (destination === 'salesforce' && result.id && migrationInstanceURL) {
    return `${migrationInstanceURL.replace(/\/$/, '')}/${result.id}`;
  } else if (destination === 'google_sheets' && (result.sheet_name || destinationSpreadsheetId)) {
    const sheetGid = result.sheet_gid || '';
    const baseUrl = destinationSpreadsheetId
      ? `https://docs.google.com/spreadsheets/d/${destinationSpreadsheetId}`
      : migrationInstanceURL || '';
    if (!baseUrl) return null;
    return sheetGid ? `${baseUrl}/edit#gid=${sheetGid}` : `${baseUrl}/edit`;
  } else if (destination === 'sql' && result.id) {
    // SQL doesn't have a view URL
    return null;
  }
  return null;
};

const InlineMigrationRecords = ({
  records,
  destination = 'salesforce',
  destinationName,
  mapping,
  migrationInstanceURL,
  destinationSpreadsheetId,
  successCount,
  isCollapsed: initialCollapsed = false,
  batchType,
  totalRecordsCount, // Total from API (for paginated display)
  batchId, // For API pagination callback
  apiPage = 1, // Current API page (1-based)
  apiPageSize = 500,
  onFetchPage, // (batchId, page) => Promise - fetch a specific page (replaces records)
  apiPageLoading = false,
  /** When true (e.g. data retention), CSV download is disabled */
  downloadDisabled = false,
  downloadDisabledTitle = 'Download is not available — row-level data is no longer stored (retention policy).',
}) => {
  const [isCollapsed, setIsCollapsed] = useState(initialCollapsed);
  const [currentPage, setCurrentPage] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');
  const [sortColumn, setSortColumn] = useState(null);
  const [sortDirection, setSortDirection] = useState('asc');
  const [migrationTableExpanded, setMigrationTableExpanded] = useState(false);
  // Use 500 per table page when in batch/API pagination mode
  const pageSize = totalRecordsCount != null ? 500 : 10;

  const isMetadataBatch = batchType === 'metadata';
  const headers = getDestinationHeaders(destination);
  const displayedCount = successCount != null ? successCount : (records?.length ?? 0);
  const totalApiPages = totalRecordsCount != null && apiPageSize > 0
    ? Math.ceil(totalRecordsCount / apiPageSize)
    : 0;
  const hasApiPagination = totalApiPages > 1 && onFetchPage && batchId;
  const serverSearchEnabled = hasApiPagination;
  const rowOffset = hasApiPagination ? (apiPage - 1) * apiPageSize : 0;

  const handleFetchApiPage = useCallback(async (page, searchOverride) => {
    if (!onFetchPage || !batchId || page < 1 || page > totalApiPages) return;
    try {
      const q =
        searchOverride !== undefined
          ? searchOverride
          : serverSearchEnabled
            ? searchQuery
            : '';
      await onFetchPage(batchId, page, q);
      setCurrentPage(0);
    } catch (err) {
      console.error('Failed to fetch migration records page:', err);
    }
  }, [onFetchPage, batchId, totalApiPages, serverSearchEnabled, searchQuery]);

  const searchDebounceSkippedRef = useRef(false);
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

  // Search: client filter on loaded rows unless server search is active
  const filteredRecords = useMemo(() => {
    if (!records || records.length === 0) return [];
    if (serverSearchEnabled) return records;
    const q = (searchQuery || '').trim().toLowerCase();
    if (!q) return records;
    return records.filter((record) => {
      const sr = record.salesforce_results || {};
      const sd = record.source_data || record.mapped_data || {};
      const status = (record.status || '').toLowerCase();
      const message = (record.error_message || '').toLowerCase();
      const objectNames = Object.keys(sr).join(' ').toLowerCase();
      if (status.includes(q) || message.includes(q) || objectNames.includes(q)) return true;
      if (isMetadataBatch) {
        const field = (sd.field_api_name || '').toLowerCase();
        const type = (sd.type || '').toLowerCase();
        const label = (sd.label || '').toLowerCase();
        if (field.includes(q) || type.includes(q) || label.includes(q)) return true;
      }
      return Object.values(sd).some((v) =>
        String(v ?? '').toLowerCase().includes(q)
      );
    });
  }, [records, searchQuery, isMetadataBatch]);

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

  // Sort filtered records by selected column
  const sortedRecords = useMemo(() => {
    if (!filteredRecords.length) return [];
    if (!sortColumn) return filteredRecords;
    const sorted = [...filteredRecords].sort((a, b) => {
      let va;
      let vb;
      if (sortColumn === 'status') {
        const getStatus = (r) => (r.status || (r.salesforce_results && (Object.values(r.salesforce_results)[0] || {}).status) || '').toLowerCase();
        va = getStatus(a);
        vb = getStatus(b);
      } else if (sortColumn === 'message') {
        va = a.error_message ?? '';
        vb = b.error_message ?? '';
      } else if (sortColumn === 'object') {
        va = Object.keys(a.salesforce_results || {})[0] ?? '';
        vb = Object.keys(b.salesforce_results || {})[0] ?? '';
      } else if (isMetadataBatch) {
        const sdA = a.source_data || {};
        const sdB = b.source_data || {};
        if (sortColumn === 'field') { va = sdA.field_api_name ?? ''; vb = sdB.field_api_name ?? ''; }
        else if (sortColumn === 'type') { va = sdA.type ?? ''; vb = sdB.type ?? ''; }
        else if (sortColumn === 'label') { va = sdA.label ?? ''; vb = sdB.label ?? ''; }
        else if (sortColumn === 'message') {
          const srA = a.salesforce_results || {};
          const srB = b.salesforce_results || {};
          va = srA.error || a.error_message || '';
          vb = srB.error || b.error_message || '';
        }
        else { va = ''; vb = ''; }
      } else {
        const sdA = a.source_data || a.mapped_data || {};
        const sdB = b.source_data || b.mapped_data || {};
        va = sdA[sortColumn]; vb = sdB[sortColumn];
      }
      return compareValues(va, vb);
    });
    return sortDirection === 'desc' ? sorted.reverse() : sorted;
  }, [filteredRecords, sortColumn, sortDirection, isMetadataBatch, compareValues]);

  useEffect(() => {
    setCurrentPage(0);
  }, [searchQuery, sortColumn, sortDirection]);

  // Transform sorted records to match expected structure (same logic as before, on sortedRecords)
  const migrationResults = useMemo(() => {
    const mappedObjectLabel = primaryObjectFromMapping(mapping);
    return sortedRecords.map((record) => {
    const salesforceResults = parseSalesforceResults(record.salesforce_results) || {};
    const recordStatus = record.status || 'unknown';
    const recordMessage = record.error_message || null;
    
    // salesforce_results is already in the correct format: { "ObjectName": { status, id, message, ... } }
    // For Salesforce: { "Contact": { status: "created", id: "...", message: "..." } }
    // For Google Sheets: { "Sheet1": { status: "created", sheet_gid: "...", message: "..." } }
    // For SQL: { "TableName": { status: "created", id: "...", message: "..." } }
    
    // Metadata batch: salesforce_results is flat { id, action, view_link }
    if (isMetadataBatch && typeof salesforceResults === 'object' && salesforceResults !== null) {
      const finalStatus = (recordStatus === 'rolled_back') ? recordStatus : (salesforceResults.action || recordStatus);
      return {
        _metadata: {
          status: finalStatus,
          message: recordMessage,
          view_link: salesforceResults.view_link,
          id: salesforceResults.id,
        },
      };
    }
    if (typeof salesforceResults === 'object' && salesforceResults !== null && Object.keys(salesforceResults).length > 0) {
      // Already in correct format: { "PrimaryKey": { status, id, message, ... } }
      // Just ensure all result objects have required fields
      const transformed = {};
      Object.entries(salesforceResults).forEach(([primaryKey, result]) => {
        if (typeof result === 'object' && result !== null) {
          // Prioritize record.status if it's "rolled_back", otherwise use result.status
          const finalStatus = (recordStatus === 'rolled_back') ? recordStatus : (result.status || recordStatus);
          const defaultSuccessMsg =
            finalStatus === 'created' || finalStatus === 'updated' || finalStatus === 'success'
              ? 'Successfully migrated to Salesforce'
              : null;
          transformed[primaryKey] = {
            ...result, // Include any other fields first
            status: finalStatus, // Override status to prioritize rolled_back
            message:
              result.message ||
              result.error ||
              recordMessage ||
              defaultSuccessMsg,
            id: result.id,
            object_name: result.object_name || primaryKey,
            sheet_name: result.sheet_name,
            table_name: result.table_name,
            sheet_gid: result.sheet_gid,
          };
        } else {
          // Fallback: create a result object from the value
          const finalStatus = (recordStatus === 'rolled_back') ? recordStatus : (result || recordStatus);
          transformed[primaryKey] = {
            status: finalStatus,
            message: recordMessage,
          };
        }
      });
      return transformed;
    } else {
      // Fallback: if salesforce_results is empty or invalid, create a default structure
      const primaryKey =
        mappedObjectLabel ||
        (destination === 'salesforce' ? null : destinationName) ||
        'Unknown';
      const fallbackMsg =
        recordMessage ||
        (recordStatus === 'success' || recordStatus === 'created' || recordStatus === 'updated'
          ? 'Migration completed (details unavailable — re-run migration to refresh links)'
          : recordStatus === 'failed' || recordStatus === 'error'
            ? 'Migration failed'
            : '-');
      return {
        [primaryKey || destinationName || 'Unknown']: {
          status: recordStatus, // Use recordStatus directly (will be "rolled_back" if rolled back)
          message: fallbackMsg,
          id: null,
          object_name: destination === 'salesforce' ? mappedObjectLabel || primaryKey : null,
          sheet_name: destination === 'google_sheets' ? primaryKey : null,
          table_name: destination === 'sql' ? primaryKey : null,
        }
      };
    }
  });
  }, [sortedRecords, isMetadataBatch, destination, destinationName, mapping]);

  // migratedRecords from sorted records (strip rule blobs embedded on row 0 during post-extract save)
  const migratedRecords = useMemo(
    () =>
      sortedRecords.map((record) =>
        omitHiddenExtractionRecordKeys(record.source_data || record.mapped_data || {}),
      ),
    [sortedRecords]
  );
  
  // Reverse mapping from source field names to mapped column names (for mapped_data fallback)
  const sourceToMappedFieldMap = useMemo(() => {
    const m = {};
    if (mapping) {
      Object.entries(mapping).forEach(([sourceField, mappingInfo]) => {
        if (mappingInfo && typeof mappingInfo === 'object') {
          const mappedColumn = mappingInfo.column || mappingInfo.field || sourceField;
          m[sourceField] = mappedColumn;
        }
      });
    }
    return m;
  }, [mapping]);

  // Get all field keys from the actual transformed data (source_data)
  // After transformation, source_data contains transformed field names (e.g., "FirstName", "LastName")
  // which may differ from the original mapping keys (e.g., "Contact Name")
  // Use source_data keys to ensure headers match the actual data structure
  const allFieldKeys = migratedRecords[0] 
    ? Object.keys(migratedRecords[0])
    : (mapping ? Object.keys(mapping) : []);
  
  // Filter out metadata fields that shouldn't be displayed:
  // - extraction rule blobs (filter_rules, data_cleanup_rules, …)
  // - Fields starting with '_' (e.g., '_attachments')
  // - 'attachments' (lowercase) if it contains object data (metadata)
  const fieldKeys = allFieldKeys.filter((key) => {
    if (isHiddenExtractionMetadataKey(key)) {
      return false;
    }
    // Exclude fields starting with underscore (metadata fields)
    if (key.startsWith('_')) {
      return false;
    }
    // Exclude lowercase 'attachments' if it's an object/array (metadata)
    // Keep 'Attachments' (capital A) as it's the display field with file names
    if (key.toLowerCase() === 'attachments' && key === 'attachments') {
      const firstRecord = migratedRecords[0];
      const value = firstRecord && firstRecord[key];
      // If it's an object or array, it's metadata - exclude it
      if (value && (typeof value === 'object' || Array.isArray(value))) {
        return false;
      }
    }
    return true;
  });

  // Pagination (based on sorted/filtered list)
  const totalPages = Math.ceil(sortedRecords.length / pageSize);
  const paginatedResults = migrationResults.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize
  );
  const paginatedMetadataRecords = isMetadataBatch
    ? sortedRecords.slice(currentPage * pageSize, (currentPage + 1) * pageSize)
    : [];

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

  const tableWrapperRef = useRef(null);
  const [colWidths, setColWidths] = useState({});
  const [scrollContainerWidth, setScrollContainerWidth] = useState(0);

  const columnIds = useMemo(() => {
    if (isMetadataBatch) {
      return [SR_COL_KEY, '__field__', '__type__', '__label__', '__status__', '__message__', '__view__'];
    }
    const ids = [SR_COL_KEY, '__status__', '__message__', '__object__', '__view__'];
    if (destination === 'salesforce') ids.push('__attachments__');
    ids.push(...fieldKeys);
    return ids;
  }, [isMetadataBatch, destination, fieldKeys]);

  const columnsKey = useMemo(() => columnIds.join('\0'), [columnIds]);

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
  }, [records, migrationTableExpanded, columnsKey]);

  const baseColumnWidth = useMemo(() => {
    const n = columnIds.length;
    if (n === 0) return DEFAULT_COL_WIDTH;
    const w = scrollContainerWidth;
    if (w <= 0) return DEFAULT_COL_WIDTH;
    const srW = colWidths[SR_COL_KEY] ?? DEFAULT_SR_WIDTH;
    const usable = Math.max(0, w - srW);
    const dataCols = Math.max(1, n - 1);
    const perCol = Math.floor(usable / dataCols);
    return Math.min(MAX_COL_WIDTH, Math.max(MIN_COL_WIDTH, perCol));
  }, [columnIds.length, scrollContainerWidth, colWidths]);

  const widthForSr = useCallback(() => colWidths[SR_COL_KEY] ?? DEFAULT_SR_WIDTH, [colWidths]);

  const widthForCol = useCallback(
    (id) => colWidths[id] ?? baseColumnWidth,
    [colWidths, baseColumnWidth]
  );

  const beginResize = useCallback(
    (e, colId) => {
      e.preventDefault();
      e.stopPropagation();
      const startX = e.clientX;
      const isSr = colId === SR_COL_KEY;
      const minW = isSr ? MIN_SR_WIDTH : MIN_COL_WIDTH;
      const maxW = isSr ? MAX_SR_WIDTH : MAX_COL_WIDTH;
      let startW = isSr ? DEFAULT_SR_WIDTH : baseColumnWidth;
      setColWidths((prev) => {
        startW = prev[colId] ?? (isSr ? DEFAULT_SR_WIDTH : baseColumnWidth);
        return prev;
      });
      const onMove = (moveEvent) => {
        const dx = moveEvent.clientX - startX;
        const next = Math.min(maxW, Math.max(minW, startW + dx));
        setColWidths((p) => ({ ...p, [colId]: next }));
      };
      const onUp = () => {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [baseColumnWidth]
  );

  useEffect(() => {
    setColWidths({});
  }, [columnsKey]);

  const escapeCsvCell = useCallback((value) => {
    if (value === null || value === undefined) return '';
    const s = typeof value === 'object' ? JSON.stringify(value) : String(value);
    if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  }, []);

  const handleDownloadMigrationCsv = useCallback(() => {
    if (downloadDisabled) return;
    if (!sortedRecords.length) return;
    const out = [];
    if (isMetadataBatch) {
      out.push(['Sr No.', 'Field', 'Type', 'Label', 'Status', 'Message', 'View URL'].map(escapeCsvCell).join(','));
      sortedRecords.forEach((record, i) => {
        const sd = record.source_data || {};
        const sr = record.salesforce_results || {};
        const status = record.status === 'rolled_back' ? 'rolled_back' : (sr.action || record.status);
        const isError = ['error', 'failed', 'create_failed', 'update_failed'].includes((status || '').toLowerCase());
        const msgText = isError
          ? (sr.error || record.error_message || '')
          : (status === 'created' || status === 'updated')
            ? 'Deployed successfully'
            : '';
        out.push(
          [
            rowOffset + i + 1,
            sd.field_api_name || '',
            sd.type || '',
            sd.label || '',
            formatStatusText(status),
            msgText,
            sr.view_link || '',
          ]
            .map(escapeCsvCell)
            .join(',')
        );
      });
    } else {
      const head = ['Sr No.', 'Status', 'Message', headers.primaryLabel, 'View Record'];
      if (destination === 'salesforce') head.push('Attachments');
      fieldKeys.forEach((fk) => head.push(camelCaseToLabel(fk)));
      out.push(head.map(escapeCsvCell).join(','));
      sortedRecords.forEach((record, globalIdx) => {
        const mr = migrationResults[globalIdx];
        const recordData = migratedRecords[globalIdx] || {};
        if (!mr || typeof mr !== 'object') return;
        Object.entries(mr).forEach(([primaryKey, result]) => {
          const viewLink = getRecordViewLink(result, destination, migrationInstanceURL, destinationSpreadsheetId) || '';
          let att = '';
          if (destination === 'salesforce' && result.attachments?.length) {
            const a = result.attachments;
            const ok = a.filter((x) => ['linked', 'uploaded_not_linked'].includes(x.status)).length;
            const fail = a.filter((x) => x.status === 'failed').length;
            att = ok || fail ? `${ok} created, ${fail} failed` : '';
          }
          const cells = [
            rowOffset + globalIdx + 1,
            formatStatusText(result.status),
            result.message || '-',
            primaryKey,
            viewLink,
          ];
          if (destination === 'salesforce') cells.push(att);
          fieldKeys.forEach((fk) => {
            let v = recordData[fk];
            if ((v === undefined || v === null) && sourceToMappedFieldMap[fk] && record.mapped_data) {
              v = record.mapped_data[sourceToMappedFieldMap[fk]];
            }
            cells.push(v !== undefined && v !== null ? String(v) : '');
          });
          out.push(cells.map(escapeCsvCell).join(','));
        });
      });
    }
    const csv = `\uFEFF${out.join('\r\n')}`;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `migration-records-${batchId ? String(batchId).slice(0, 8) : 'export'}.csv`;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [
    sortedRecords,
    isMetadataBatch,
    migrationResults,
    migratedRecords,
    escapeCsvCell,
    destination,
    headers,
    fieldKeys,
    migrationInstanceURL,
    destinationSpreadsheetId,
    batchId,
    rowOffset,
    sourceToMappedFieldMap,
    downloadDisabled,
  ]);

  useEffect(() => {
    if (!migrationTableExpanded) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setMigrationTableExpanded(false);
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [migrationTableExpanded]);

  if (!records || records.length === 0) {
    return null;
  }

  const renderMigrationTableBlock = (inOverlay) => (
    <>
      <div
        className={`inline-migration-records-table-wrapper${
          inOverlay ? ' inline-migration-records-table-wrapper--overlay' : ''
        }`}
      >
        <div className="inline-migration-records-search">
          <button
            type="button"
            className="inline-migration-records-toolbar-btn"
            onClick={handleDownloadMigrationCsv}
            disabled={downloadDisabled}
            aria-label={downloadDisabled ? 'Download unavailable — data no longer stored' : 'Download migration records as CSV'}
            title={downloadDisabled ? downloadDisabledTitle : 'Download current table as CSV'}
          >
            <FiDownload aria-hidden />
          </button>
          <button
            type="button"
            className="inline-migration-records-toolbar-btn"
            onClick={() => (inOverlay ? setMigrationTableExpanded(false) : setMigrationTableExpanded(true))}
            aria-label={inOverlay ? 'Exit expanded table view' : 'Expand migration records table'}
            title={inOverlay ? 'Exit expanded view' : 'Expand table'}
          >
            {inOverlay ? <FiMinimize2 aria-hidden /> : <FiMaximize2 aria-hidden />}
          </button>
          <FiSearch className="inline-migration-records-search-icon" aria-hidden />
          <input
            type="text"
            className="inline-migration-records-search-input"
            placeholder={
              serverSearchEnabled && totalRecordsCount != null
                ? `Search all ${totalRecordsCount} migration records…`
                : 'Search loaded rows…'
            }
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label={
              serverSearchEnabled ? 'Search all migration records' : 'Search in loaded table rows'
            }
          />
        </div>
        {hasApiPagination && (
          <div className="inline-migration-records-api-pagination">
            <button
              type="button"
              onClick={() => handleFetchApiPage(apiPage - 1)}
              disabled={apiPage <= 1 || apiPageLoading}
              className="inline-migration-records-pagination-btn"
            >
              ← Previous page
            </button>
            <span className="inline-migration-records-pagination-info">
              Data page {apiPage} of {totalApiPages}
              {apiPageLoading && ' (loading…)'}
            </span>
            <button
              type="button"
              onClick={() => handleFetchApiPage(apiPage + 1)}
              disabled={apiPage >= totalApiPages || apiPageLoading}
              className="inline-migration-records-pagination-btn"
            >
              Next page →
            </button>
          </div>
        )}
        <div ref={tableWrapperRef} className="inline-migration-records-table-scroll">
          {sortedRecords.length === 0 ? (
            <div className="inline-migration-records-empty">
              {searchQuery.trim() ? 'No records match your search.' : 'No records.'}
            </div>
          ) : isMetadataBatch ? (
            <table className="inline-migration-records-table">
              <thead>
                <tr>
                  <th
                    className="sr-col inline-migration-records-th-resizable"
                    style={{ width: widthForSr(), minWidth: MIN_SR_WIDTH, maxWidth: MAX_SR_WIDTH }}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Sr No.</span>
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, SR_COL_KEY)}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Sr No. column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-sortable inline-migration-records-th-resizable"
                    style={{ width: widthForCol('__field__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    onClick={() => handleSort('field')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('field'); } }}
                    tabIndex={0}
                    aria-sort={sortColumn === 'field' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Field</span>
                      {sortColumn === 'field' ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__field__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Field column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-sortable inline-migration-records-th-resizable"
                    style={{ width: widthForCol('__type__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    onClick={() => handleSort('type')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('type'); } }}
                    tabIndex={0}
                    aria-sort={sortColumn === 'type' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Type</span>
                      {sortColumn === 'type' ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__type__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Type column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-sortable inline-migration-records-th-resizable"
                    style={{ width: widthForCol('__label__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    onClick={() => handleSort('label')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('label'); } }}
                    tabIndex={0}
                    aria-sort={sortColumn === 'label' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Label</span>
                      {sortColumn === 'label' ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__label__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Label column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-sortable inline-migration-records-th-resizable status-col"
                    style={{ width: widthForCol('__status__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    onClick={() => handleSort('status')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('status'); } }}
                    tabIndex={0}
                    aria-sort={sortColumn === 'status' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Status</span>
                      {sortColumn === 'status' ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__status__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Status column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-sortable inline-migration-records-th-resizable"
                    style={{ width: widthForCol('__message__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    onClick={() => handleSort('message')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('message'); } }}
                    tabIndex={0}
                    aria-sort={sortColumn === 'message' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Message</span>
                      {sortColumn === 'message' ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__message__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Message column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-resizable"
                    style={{ width: widthForCol('__view__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>View</span>
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__view__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize View column"
                    />
                  </th>
                </tr>
              </thead>
              <tbody>
                {paginatedMetadataRecords.map((record, recordIdx) => {
                  const globalIdx = currentPage * pageSize + recordIdx;
                  const sd = record.source_data || {};
                  const sr = record.salesforce_results || {};
                  const status = record.status === 'rolled_back' ? 'rolled_back' : (sr.action || record.status);
                  const isError = ['error', 'failed', 'create_failed', 'update_failed'].includes((status || '').toLowerCase());
                  const msgText = isError
                    ? (sr.error || record.error_message || '')
                    : (status === 'created' || status === 'updated')
                      ? 'Deployed successfully'
                      : '';
                  return (
                    <tr key={`meta-${globalIdx}`}>
                      <td className="sr-col" style={{ width: widthForSr(), minWidth: MIN_SR_WIDTH, maxWidth: MAX_SR_WIDTH }}>{rowOffset + globalIdx + 1}</td>
                      <td style={{ width: widthForCol('__field__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}>{sd.field_api_name || '-'}</td>
                      <td style={{ width: widthForCol('__type__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}>{sd.type || '-'}</td>
                      <td style={{ width: widthForCol('__label__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}>{sd.label || '-'}</td>
                      <td style={{ width: widthForCol('__status__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}>
                        <span className={getStatusClass(status)}>
                          {formatStatusText(status)}
                        </span>
                      </td>
                      <td
                        style={{ width: widthForCol('__message__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                        className={isError ? 'metadata-message-cell metadata-message-error' : 'metadata-message-cell'}
                        title={msgText || undefined}
                      >
                        {msgText || '-'}
                      </td>
                      <td style={{ width: widthForCol('__view__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}>
                        {sr.view_link ? (
                          <a
                            href={sr.view_link}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="view-link"
                          >
                            View
                          </a>
                        ) : (
                          '-'
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <table className="inline-migration-records-table">
              <thead>
                <tr>
                  <th
                    className="sr-col inline-migration-records-th-resizable"
                    style={{ width: widthForSr(), minWidth: MIN_SR_WIDTH, maxWidth: MAX_SR_WIDTH }}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Sr No.</span>
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, SR_COL_KEY)}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Sr No. column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-sortable inline-migration-records-th-resizable status-col"
                    style={{ width: widthForCol('__status__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    onClick={() => handleSort('status')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('status'); } }}
                    tabIndex={0}
                    aria-sort={sortColumn === 'status' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Status</span>
                      {sortColumn === 'status' ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__status__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Status column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-sortable inline-migration-records-th-resizable"
                    style={{ width: widthForCol('__message__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    onClick={() => handleSort('message')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('message'); } }}
                    tabIndex={0}
                    aria-sort={sortColumn === 'message' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>Message</span>
                      {sortColumn === 'message' ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__message__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize Message column"
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-sortable inline-migration-records-th-resizable"
                    style={{ width: widthForCol('__object__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    onClick={() => handleSort('object')}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort('object'); } }}
                    tabIndex={0}
                    aria-sort={sortColumn === 'object' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>{headers.primaryLabel}</span>
                      {sortColumn === 'object' ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__object__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label={`Resize ${headers.primaryLabel} column`}
                    />
                  </th>
                  <th
                    className="inline-migration-records-th-resizable"
                    style={{ width: widthForCol('__view__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                  >
                    <span className="inline-migration-records-th-content">
                      <span>View Record</span>
                    </span>
                    <button
                      type="button"
                      className="inline-migration-records-resize-handle"
                      onMouseDown={(e) => beginResize(e, '__view__')}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      aria-label="Resize View Record column"
                    />
                  </th>
                  {destination === 'salesforce' && (
                    <th
                      className="inline-migration-records-th-resizable"
                      style={{ width: widthForCol('__attachments__'), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                    >
                      <span className="inline-migration-records-th-content">
                        <span>Attachments</span>
                      </span>
                      <button
                        type="button"
                        className="inline-migration-records-resize-handle"
                        onMouseDown={(e) => beginResize(e, '__attachments__')}
                        onClick={(e) => e.stopPropagation()}
                        tabIndex={-1}
                        aria-label="Resize Attachments column"
                      />
                    </th>
                  )}
                  {fieldKeys.map((fieldKey) => (
                    <th
                      key={`hdr-${fieldKey}`}
                      title={camelCaseToLabel(fieldKey)}
                      className="inline-migration-records-th-sortable inline-migration-records-th-resizable"
                      style={{ width: widthForCol(fieldKey), minWidth: MIN_COL_WIDTH, maxWidth: MAX_COL_WIDTH }}
                      onClick={() => handleSort(fieldKey)}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort(fieldKey); } }}
                      tabIndex={0}
                      aria-sort={sortColumn === fieldKey ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                    >
                      <span className="inline-migration-records-th-content">
                        <span>{camelCaseToLabel(fieldKey)}</span>
                        {sortColumn === fieldKey ? (sortDirection === 'asc' ? <FiChevronUp className="inline-migration-records-sort-icon" /> : <FiChevronDown className="inline-migration-records-sort-icon" />) : <span className="inline-migration-records-sort-placeholder" />}
                      </span>
                      <button
                        type="button"
                        className="inline-migration-records-resize-handle"
                        onMouseDown={(e) => beginResize(e, fieldKey)}
                        onClick={(e) => e.stopPropagation()}
                        tabIndex={-1}
                        aria-label={`Resize ${camelCaseToLabel(fieldKey)} column`}
                      />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {paginatedResults
                  .flatMap((recordResult, recordIdx) => {
                    const globalIdx = currentPage * pageSize + recordIdx;
                    const entries = Object.entries(recordResult);
                    const recordData = migratedRecords[globalIdx] || {};
                    const cell = (id) => ({
                      width: widthForCol(id),
                      minWidth: MIN_COL_WIDTH,
                      maxWidth: MAX_COL_WIDTH,
                    });
                    return entries.map(([primaryKey, result], idx) => (
                      <tr key={`${globalIdx}-${idx}`}>
                        {idx === 0 && (
                          <td
                            className="sr-col"
                            rowSpan={entries.length}
                            style={{ width: widthForSr(), minWidth: MIN_SR_WIDTH, maxWidth: MAX_SR_WIDTH }}
                          >
                            {rowOffset + globalIdx + 1}
                          </td>
                        )}
                        <td style={cell('__status__')}>
                          <span className={getStatusClass(result.status)}>
                            {formatStatusText(result.status)}
                          </span>
                        </td>
                        <td style={{ ...cell('__message__'), color: result.status === 'error' ? 'red' : 'inherit' }}>
                          {result.message || '-'}
                        </td>
                        <td style={cell('__object__')}>{primaryKey}</td>
                        <td style={cell('__view__')}>
                          {(() => {
                            const viewLink = getRecordViewLink(
                              result,
                              destination,
                              migrationInstanceURL,
                              destinationSpreadsheetId
                            );
                            if (viewLink) {
                              return (
                                <a
                                  href={viewLink}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="view-link"
                                >
                                  View Record
                                </a>
                              );
                            }
                            return '-';
                          })()}
                        </td>
                        {destination === 'salesforce' && (
                          <td style={cell('__attachments__')}>
                            {(() => {
                              const attachments = result.attachments || [];
                              if (attachments.length === 0) {
                                return <span style={{ color: '#999' }}>-</span>;
                              }
                              const failedAttachments = attachments.filter(a => a.status === 'failed');
                              const failedCount = failedAttachments.length;
                              const createdCount = attachments.filter(a =>
                                ['linked', 'uploaded_not_linked'].includes(a.status)
                              ).length;
                              const linkedCount = attachments.filter(a =>
                                ['linked_existing'].includes(a.status)
                              ).length;
                              const skippedDuplicates = attachments.filter(a =>
                                a.status === 'skipped_duplicate'
                              );
                              const skippedCount = skippedDuplicates.length;

                              return (
                                <div style={{ fontSize: '0.9em' }}>
                                  {createdCount > 0 && (
                                    <span style={{ color: '#4caf50', marginRight: '4px' }}>
                                      {createdCount} created
                                    </span>
                                  )}
                                  {linkedCount > 0 && (
                                    <span style={{ color: '#2196f3', marginRight: '4px' }}>
                                      {linkedCount} linked
                                    </span>
                                  )}
                                  {skippedCount > 0 && (
                                    <span
                                      style={{ color: '#6b7280', marginRight: '4px' }}
                                      title="These attachments were skipped because they are already attached to this Salesforce record."
                                    >
                                      {skippedCount} skipped (already attached)
                                    </span>
                                  )}
                                  {failedCount > 0 && (
                                    <span style={{ color: '#f44336', display: 'inline-block' }}>
                                      ({failedCount} failed)
                                      {failedAttachments.some(a => a.error) && (
                                        <span
                                          style={{
                                            display: 'block',
                                            fontSize: '0.85em',
                                            color: '#c62828',
                                            marginTop: '2px',
                                            fontWeight: 500,
                                            maxWidth: '280px',
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                            whiteSpace: 'nowrap',
                                          }}
                                          title={failedAttachments.map(a => `${a.title || 'Attachment'}: ${a.error || 'Unknown error'}`).join('\n')}
                                        >
                                          {failedAttachments.length === 1 && failedAttachments[0].error
                                            ? failedAttachments[0].error
                                            : failedAttachments.map((a, i) => `${a.title || 'Attachment'}: ${a.error || 'Unknown error'}`).join('; ')}
                                        </span>
                                      )}
                                    </span>
                                  )}
                                </div>
                              );
                            })()}
                          </td>
                        )}
                        {fieldKeys.map((fieldKey) => {
                          let fieldValue = recordData[fieldKey];
                          if (fieldValue === undefined || fieldValue === null) {
                            const mappedColumnName = sourceToMappedFieldMap[fieldKey];
                            if (mappedColumnName && sortedRecords[globalIdx]?.mapped_data) {
                              fieldValue = sortedRecords[globalIdx].mapped_data[mappedColumnName];
                            }
                          }

                          return idx === 0 ? (
                            <td
                              key={`row-${globalIdx}-${fieldKey}`}
                              rowSpan={entries.length}
                              style={cell(fieldKey)}
                            >
                              <span
                                className="cell-ellipsis"
                                title={
                                  fieldValue !== undefined &&
                                  fieldValue !== null
                                    ? String(fieldValue)
                                    : ''
                                }
                              >
                                {fieldValue !== undefined &&
                                fieldValue !== null
                                  ? String(fieldValue)
                                  : ''}
                              </span>
                            </td>
                          ) : null;
                        })}
                      </tr>
                    ));
                  })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {totalPages > 1 && (
        <div className="inline-migration-records-pagination">
          <button
            onClick={() => setCurrentPage((prev) => Math.max(prev - 1, 0))}
            disabled={currentPage === 0}
            className="inline-migration-records-pagination-btn"
          >
            ← Previous
          </button>
          <span className="inline-migration-records-pagination-info">
            Page {currentPage + 1} of {totalPages}
          </span>
          <button
            onClick={() => setCurrentPage((prev) => Math.min(prev + 1, totalPages - 1))}
            disabled={currentPage >= totalPages - 1}
            className="inline-migration-records-pagination-btn"
          >
            Next →
          </button>
        </div>
      )}
    </>
  );

  return (
    <div className="inline-migration-records">
      <div
        className="inline-migration-records-header"
        onClick={() => setIsCollapsed(!isCollapsed)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setIsCollapsed(!isCollapsed);
          }
        }}
      >
        <div className="inline-migration-records-title">
          <span className="inline-migration-records-icon"><FiBarChart2 /></span>
          <span>
            <strong>Migration Records</strong>
            <span className="inline-migration-records-count">
              {' '}({hasApiPagination
                ? `Showing records ${rowOffset + 1}–${Math.min(rowOffset + (records?.length ?? 0), totalRecordsCount ?? 0)} of ${totalRecordsCount}`
                : totalRecordsCount != null
                  ? `${records?.length ?? 0} of ${totalRecordsCount} records`
                  : `${displayedCount} records`})
            </span>
          </span>
        </div>
        <div className="inline-migration-records-toggle">
          {isCollapsed ? <FiChevronDown /> : <FiChevronUp />}
        </div>
      </div>

      {!isCollapsed && (
        <div className="inline-migration-records-content">
          {!migrationTableExpanded && renderMigrationTableBlock(false)}
          {migrationTableExpanded &&
            createPortal(
              <div className="inline-migration-records-overlay-root">
                <div
                  className="inline-migration-records-overlay-backdrop"
                  onClick={() => setMigrationTableExpanded(false)}
                  aria-hidden
                />
                <div
                  className="inline-migration-records-overlay-panel"
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="inline-migration-records-overlay-heading"
                >
                  <div className="inline-migration-records-overlay-topbar">
                    <h2 id="inline-migration-records-overlay-heading" className="inline-migration-records-overlay-heading">
                      Migration records
                    </h2>
                    <button
                      type="button"
                      className="inline-migration-records-overlay-close"
                      onClick={() => setMigrationTableExpanded(false)}
                      aria-label="Close expanded table"
                    >
                      <FiX aria-hidden />
                    </button>
                  </div>
                  <div className="inline-migration-records-overlay-body">
                    {renderMigrationTableBlock(true)}
                  </div>
                </div>
              </div>,
              document.body
            )}
        </div>
      )}
    </div>
  );
};

export default InlineMigrationRecords;

