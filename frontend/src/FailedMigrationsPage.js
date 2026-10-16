import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiAlertCircle, FiArrowLeft, FiRefreshCw, FiDownload } from 'react-icons/fi';
import api from './api';
import { useUserTimezone } from './hooks/useUserTimezone';
import { formatDateTimeForUser } from './utils/timezoneUtils';
import DataTable from './components/DataTable';
import AlertModal from './components/AlertModal';
import './DataTablePage.css';
import './components/DataTable.css'; // Import DataTable CSS only where it's used

const FailedMigrationsPage = () => {
  const navigate = useNavigate();
  const { userTimezone } = useUserTimezone();
  const [failedRecords, setFailedRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [search, setSearch] = useState('');
  const [totalCount, setTotalCount] = useState(0);
  const [isExporting, setIsExporting] = useState(false);
  const [alertModal, setAlertModal] = useState({ isOpen: false, title: '', message: '', type: 'info' });

  const showAlert = (title, message, type = 'info') => {
    setAlertModal({ isOpen: true, title, message, type });
  };
  const closeAlert = () => setAlertModal({ isOpen: false, title: '', message: '', type: 'info' });

  useEffect(() => {
    fetchFailedMigrations(page, pageSize, search);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetch on mount/pagination only
  }, [page, pageSize, search]);

  const fetchFailedMigrations = async (pageNum = page, pageSizeNum = pageSize, searchTerm = search) => {
    try {
      setLoading(true);
      setError(null);

      const params = new URLSearchParams({
        page: String(pageNum),
        page_size: String(pageSizeNum),
      });
      if (searchTerm && searchTerm.trim()) {
        params.set('search', searchTerm.trim());
      }
      const response = await api.get(`/failed-migrations?${params.toString()}`);

      if (response.data && response.data.status === 'success') {
        setFailedRecords(response.data.records || []);
        setTotalCount(response.data.total_count ?? 0);
      }
    } catch (err) {
      console.error('Error fetching failed migrations:', err);
      setError('Failed to load failed migrations');
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = () => {
    fetchFailedMigrations(page, pageSize, search);
  };

  const handleExportAll = async () => {
    if (totalCount === 0 || isExporting) return;
    setIsExporting(true);
    try {
      const startRes = await api.post('/migration-records/export', null, {
        params: { status: 'failed' },
      });
      const taskId = startRes.data?.task_id;
      const largeExport = startRes.data?.large_export;
      if (!taskId) throw new Error('No task ID returned');
      if (largeExport) {
        showAlert('Export Started', 'You\'ll receive a CSV file via email when ready (large dataset).', 'info');
        return;
      }
      const pollInterval = 1500;
      const maxAttempts = 120; // 3 min
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise((r) => setTimeout(r, pollInterval));
        const statusRes = await api.get(`/migration-export-status/${taskId}`);
        const s = statusRes.data?.status;
        if (s === 'completed') {
          const token = statusRes.data?.download_token;
          if (token) {
            const downloadRes = await api.get('/migration-export-download', {
              params: { token },
              responseType: 'blob',
            });
            const url = window.URL.createObjectURL(new Blob([downloadRes.data]));
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', `all-failed-migrations.xlsx`);
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
          }
          break;
        } else if (s === 'failed') {
          throw new Error(statusRes.data?.error_message || 'Export failed');
        }
      }
    } catch (err) {
      console.error('Export failed:', err);
      showAlert('Export Failed', err.response?.data?.detail || err.message || 'Export failed. Please try again.', 'error');
    } finally {
      setIsExporting(false);
    }
  };

  const handleSearchChange = (value) => {
    setSearch(value);
    setPage(1);
  };

  const handlePageChange = (newPage) => {
    setPage(newPage);
  };

  const handlePageSizeChange = (newSize) => {
    setPageSize(newSize);
    setPage(1);
  };



  // Define table columns
  const getDestinationLabel = (record) => {
    if (!record) return 'Salesforce';
    const type = (record.destination_type || 'salesforce').toLowerCase();
    const name = record.destination_name;

    if (type === 'google_sheets') {
      return name || 'Google Sheets';
    }

    if (type === 'sql' || type === 'database') {
      return name || 'Database Connection';
    }

    return name || 'Salesforce';
  };

  const columns = useMemo(() => [
    {
      Header: '#',
      accessor: 'id',
      Cell: ({ row }) => (page - 1) * pageSize + row.index + 1,
      width: 'auto',
      sortable: false
    },
    {
      Header: 'Status',
      accessor: 'failed_status',
      id: 'failed_status',
      Cell: ({ row }) => (
        <span className="status-badge" style={{
          backgroundColor: '#fee2e2',
          color: '#dc2626',
          border: '1px solid #fca5a5'
        }}>
          Failed
        </span>
      ),
      width: 'auto'
    },
    {
      Header: 'Destination',
      accessor: 'destination_type',
      Cell: ({ row }) => getDestinationLabel(row.original),
      width: 'auto'
    },
    {
      Header: 'Name',
      accessor: 'salesforce_object_types',
      id: 'salesforce_object_types',
      Cell: ({ row }) => {
        const value = row.original.salesforce_results;
        if (!value) return 'N/A';
        
        try {
          const results = typeof value === 'string' ? JSON.parse(value) : value;
          const objectTypes = Object.keys(results).filter(key => key !== 'error');
          
          if (objectTypes.length === 0) return 'N/A';
          
          return (
            <div>
              {objectTypes.map((objType, idx) => (
                <div key={objType} style={{ marginBottom: idx < objectTypes.length - 1 ? '4px' : 0 }}>
                  {objType}
                </div>
              ))}
            </div>
          );
        } catch (error) {
          console.error('Error parsing salesforce_results for object type:', error);
          return 'N/A';
        }
      },
      width: 'auto'
    },
    {
      Header: 'Error',
      accessor: 'error_message',
      Cell: ({ value }) => value || 'Unknown error',
      width: 'auto'
    },
    {
      Header: 'Failed Date',
      accessor: 'failed_at',
      Cell: ({ value }) => value ? formatDateTimeForUser(value, userTimezone, 'table') : 'N/A',
      width: 'auto'
    },
    {
      Header: 'Record Data',
      accessor: 'source_data',
      Cell: ({ value }) => {
        if (!value) return 'N/A';
        
        let data;
        
        // Handle both string and object formats
        if (typeof value === 'string') {
          try {
            data = JSON.parse(value);
          } catch (e) {
            // If it's not JSON, treat it as plain text
            return value;
          }
        } else if (typeof value === 'object') {
          data = value;
        } else {
          return 'N/A';
        }
        
        // Extract readable fields
        const readableFields = Object.entries(data)
          .filter(([key, val]) => val && val !== '' && val !== '-')
          .map(([key, val]) => `${key}: ${val}`);
        
        if (readableFields.length === 0) return 'N/A';
        
        // Join fields to show complete data
        return readableFields.join(' | ');
      },
      width: 'auto',
      className: 'record-data-cell'
    }
  ], [userTimezone, page, pageSize]);

  if (loading) {
    return (
      <div className="table-wrapper">
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <p>Loading failed migrations...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="table-wrapper">
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <h3>Error Loading Failed Migrations</h3>
          <p style={{ color: 'red' }}>{error}</p>
          <button 
            onClick={handleRefresh}
            className="migrate-button"
            style={{ marginTop: '16px' }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="failed-migrations-page">
      {/* Header */}
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'space-between', 
        marginBottom: '24px',
        padding: '40px 40px 0 40px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <button 
            onClick={() => navigate('/dashboard')}
            className="secondary-button"
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center',
              width: '48px',
              height: '48px',
              borderRadius: '50%',
              padding: '0',
              minWidth: '48px'
            }}
            title="Back to Dashboard"
          >
            <FiArrowLeft size={20} />
          </button>
          <h2>All Failed Migrations</h2>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button 
              onClick={handleExportAll}
              disabled={totalCount === 0 || isExporting}
              className="migrate-button"
              style={{ 
                display: 'flex', 
                alignItems: 'center', 
                gap: '6px',
                padding: '8px 14px',
                borderRadius: '8px',
                minWidth: 'auto',
                opacity: (totalCount === 0 || isExporting) ? 0.6 : 1
              }}
              title={totalCount === 0 ? 'No records to export' : 'Export all failed migrations'}
            >
              <FiDownload size={18} /> {isExporting ? 'Exporting...' : 'Export All'}
            </button>
            <button 
              onClick={handleRefresh}
              className="migrate-button"
              style={{ 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center',
                width: '48px',
                height: '48px',
                borderRadius: '50%',
                padding: '0',
                minWidth: '48px'
              }}
              title="Refresh Failed Migrations"
            >
              <FiRefreshCw size={20} />
            </button>
          </div>
          {isExporting && (
            <span style={{ fontSize: '12px', color: '#6b7280' }}>
              A download link will also be emailed to you when ready.
            </span>
          )}
        </div>
      </div>

      {/* Summary */}
      <div style={{ 
        backgroundColor: '#fef2f2', 
        border: '1px solid #ef4444', 
        borderRadius: '8px', 
        padding: '16px', 
        marginBottom: '24px',
        marginLeft: '40px',
        marginRight: '40px'
      }}>
        <p style={{ margin: 0, color: '#991b1b', fontSize: '14px' }}>
          Showing {totalCount.toLocaleString()} failed migrations. Each row reflects the destination that reported the error (Salesforce, Google Sheets, or Database).
        </p>
      </div>

      {/* Data Table Section */}
      <div style={{ padding: '0 40px 40px 40px' }}>
        {failedRecords.length > 0 ? (
          <>
            <div style={{
              marginBottom: '8px',
              fontSize: '14px',
              color: '#6b7280',
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}>
              <span>💡</span>
              <span>Table can be scrolled horizontally to view complete record data</span>
            </div>
          <DataTable
            columns={columns}
            data={failedRecords}
            pageSizeOptions={[25, 50, 100]}
            defaultPageSize={50}
            searchable={true}
            serverSide={true}
            totalCount={totalCount}
            page={page}
            pageSize={pageSize}
            onPageChange={handlePageChange}
            onPageSizeChange={handlePageSizeChange}
            onSearchChange={handleSearchChange}
          />
          </>
        ) : (
          <div style={{ textAlign: 'center', padding: '40px', color: '#6b7280' }}>
            <FiAlertCircle size={48} style={{ marginBottom: '16px', color: '#ef4444' }} />
            <h3>No Failed Migrations</h3>
            <p>Great! All your migrations have been successful so far.</p>
          </div>
        )}
      </div>
      <AlertModal
        isOpen={alertModal.isOpen}
        onClose={closeAlert}
        title={alertModal.title}
        message={alertModal.message}
        type={alertModal.type}
      />
    </div>
  );
};

export default FailedMigrationsPage;
