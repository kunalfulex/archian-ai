import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { FiRefreshCw, FiArrowLeft, FiChevronLeft, FiChevronRight } from 'react-icons/fi';
import api from './api';
import './DataTablePage.css';

const MigrationProgressPage = () => {
  const { jobId } = useParams();
  const navigate = useNavigate();
  
  const [migrationResults, setMigrationResults] = useState([]);
  const [currentPage, setCurrentPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [salesforceInstanceUrl, setSalesforceInstanceUrl] = useState('');
  const [destinationType, setDestinationType] = useState('salesforce');
  const [destinationName, setDestinationName] = useState('');
  const pageSize = 10;
  const hasProcessed = useRef(false); // Use ref to prevent duplicate calls

  useEffect(() => {
    if (jobId && !hasProcessed.current) {
      hasProcessed.current = true;
      getMigrationResults(jobId);
    }
  }, [jobId]);

  const getMigrationResults = async (jobId) => {
    try {
      setLoading(true);
      const response = await api.get(`/background-migration-results/${jobId}`);
      if (response.data && response.data.salesforce_results) {
        setMigrationResults(response.data.salesforce_results);
        if (response.data.job_type) {
          setDestinationType(response.data.job_type);
        }
        if (response.data.destination_name) {
          setDestinationName(response.data.destination_name);
        }
        // Set the Salesforce instance URL from the backend response
        if (response.data.salesforce_instance_url) {
          setSalesforceInstanceUrl(response.data.salesforce_instance_url);
        }
      }
    } catch (error) {
      console.error('Error getting migration results:', error);
    } finally {
      setLoading(false);
    }
  };

  const goBackToResults = () => {
    navigate('/chat');
  };

  if (loading) {
    return (
      <div className="table-wrapper">
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <p>Loading migration results...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="table-wrapper">
      <h2>Migration Results</h2>
      
      <p>
        Job ID: <strong>{jobId}</strong> | Total Records: <strong>{migrationResults.length}</strong> | Destination: <strong>{destinationName || (destinationType === 'google_sheets' ? 'Google Sheets' : destinationType === 'sql' ? 'Database' : 'Salesforce')}</strong>
      </p>

      <div style={{ marginBottom: '16px', display: 'flex', gap: '12px', alignItems: 'center' }}>
        <button
          onClick={() => getMigrationResults(jobId)}
          className="migrate-button"
          style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
        >
          <FiRefreshCw size={16} />
          Refresh Data
        </button>
        <button
          onClick={goBackToResults}
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
          title="Back to Results"
        >
          <FiArrowLeft size={20} />
        </button>
      </div>
      
      {/* Results Table */}
      {migrationResults.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="editable-table results-table" role="grid">
            <thead>
              <tr>
                <th>#</th>
                <th>Status</th>
                <th>Message</th>
                <th>Destination</th>
                <th>Name</th>
                <th>View Record</th>
                {migrationResults.length > 0 && (() => {
                  // Parse the first record's source_data to get headers
                  let firstRecordData = {};
                  try {
                    if (typeof migrationResults[0].source_data === 'string') {
                      firstRecordData = JSON.parse(migrationResults[0].source_data);
                    } else if (migrationResults[0].source_data) {
                      firstRecordData = migrationResults[0].source_data;
                    }
                  } catch (error) {
                    console.error('Error parsing first record source_data for headers:', error);
                    firstRecordData = {};
                  }
                  
                  return Object.keys(firstRecordData).map((key) => (
                    <th key={key}>{key}</th>
                  ));
                })()}
              </tr>
            </thead>
            <tbody>
              {migrationResults
                .slice(currentPage * pageSize, (currentPage + 1) * pageSize)
                .map((result, recordIdx) => {
                  const globalIdx = currentPage * pageSize + recordIdx;
                  // Parse source_data if it's a string, otherwise use as is
                  let sourceData = {};
                  try {
                    if (typeof result.source_data === 'string') {
                      sourceData = JSON.parse(result.source_data);
                    } else if (result.source_data) {
                      sourceData = result.source_data;
                    }
                  } catch (error) {
                    console.error('Error parsing source_data:', error);
                    sourceData = {};
                  }
                  
                  return (
                    <tr key={globalIdx}>
                      <td>{globalIdx + 1}</td>
                      <td>
                        <span className="status-badge" style={{
                          backgroundColor: result.status === 'created' ? '#d1fae5' : 
                                         result.status === 'updated' ? '#dbeafe' : 
                                         result.status === 'error' ? '#fee2e2' : '#f3f4f6',
                          color: result.status === 'created' ? '#065f46' : 
                                 result.status === 'updated' ? '#1e40af' : 
                                 result.status === 'error' ? '#dc2626' : '#374151'
                        }}>
                          {result.status}
                        </span>
                      </td>
                      <td style={{ color: result.status === 'error' ? 'red' : 'inherit' }}>
                        {result.message || '-'}
                      </td>
                      <td>{destinationName || (destinationType === 'google_sheets' ? 'Google Sheets' : destinationType === 'sql' ? 'Database' : 'Salesforce')}</td>
                      <td>
                        {result.object_name || result.destination || destinationName || 'Unknown'}
                      </td>
                      <td>
                        {result.salesforce_id ? (
                          salesforceInstanceUrl ? (
                            <a
                              href={`${salesforceInstanceUrl}/${result.salesforce_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{ color: '#3b82f6', textDecoration: 'none' }}
                            >
                              View Record
                            </a>
                          ) : (
                            <span style={{ color: '#6b7280', fontStyle: 'italic' }}>
                              Instance URL not available
                            </span>
                          )
                        ) : result.sheet_name ? (
                          <a
                            href={`https://docs.google.com/spreadsheets/d/${result.spreadsheet_id || ''}/edit`}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ color: '#3b82f6', textDecoration: 'none' }}
                          >
                            View Record
                          </a>
                        ) : (
                          '-' 
                        )}
                      </td>
                      {migrationResults.length > 0 && (() => {
                        // Get the first record's parsed source_data to determine headers
                        let firstRecordData = {};
                        try {
                          if (typeof migrationResults[0].source_data === 'string') {
                            firstRecordData = JSON.parse(migrationResults[0].source_data);
                          } else if (migrationResults[0].source_data) {
                            firstRecordData = migrationResults[0].source_data;
                          }
                        } catch (error) {
                          console.error('Error parsing first record source_data:', error);
                          firstRecordData = {};
                        }
                        
                        return Object.keys(firstRecordData).map((key) => (
                          <td key={key}>
                            {sourceData[key] || '-'}
                          </td>
                        ));
                      })()}
                    </tr>
                  );
                })}
            </tbody>
          </table>

          {/* Pagination Controls */}
          {migrationResults.length > pageSize && (
            <div style={{ marginTop: '20px', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <button
                onClick={() => setCurrentPage((prev) => Math.max(prev - 1, 0))}
                disabled={currentPage === 0}
                className="pagination-btn"
                style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
              >
                <FiChevronLeft size={16} />
                Previous
              </button>
              <span style={{ margin: '0 1rem' }}>
                Page {currentPage + 1} of {Math.ceil(migrationResults.length / pageSize)}
              </span>
              <button
                onClick={() => setCurrentPage((prev) => prev + 1)}
                disabled={(currentPage + 1) * pageSize >= migrationResults.length}
                className="pagination-btn"
                style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
              >
                Next
                <FiChevronRight size={16} />
              </button>
            </div>
          )}
        </div>
      )}

      {/* No Results Message */}
      {migrationResults.length === 0 && !loading && (
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <h3>No Migration Results Found</h3>
          <p>No records were found for this migration job.</p>
        </div>
      )}
    </div>
  );
};

export default MigrationProgressPage;
