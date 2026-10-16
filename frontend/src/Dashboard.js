import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiDatabase, FiCheckCircle, FiAlertCircle, FiTrendingUp, FiShare2, FiRefreshCw } from 'react-icons/fi';
import api from './api';
import './DataTablePage.css';
import UsageIndicator from './components/UsageIndicator';
import { useUserTimezone } from './hooks/useUserTimezone';
import { useFeatures } from './hooks/useFeatures';
import { formatDateTimeForUser } from './utils/timezoneUtils';

const Dashboard = () => {
  const navigate = useNavigate();
  const { userTimezone } = useUserTimezone();
  const { billing } = useFeatures();
  const [dashboardStats, setDashboardStats] = useState({
    total_migrations: 0,
    success_rate: 0,
    total_records: 0,
    failed_records: 0,
    reusable_flows: 0
  });
  const [latestSuccessfulRecords, setLatestSuccessfulRecords] = useState([]);
  const [recentFailedRecords, setRecentFailedRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [salesforceInstance, setSalesforceInstance] = useState('');
  const hasInitialized = useRef(false); // Use ref to prevent duplicate calls

  useEffect(() => {
    // Prevent multiple executions using ref
    if (hasInitialized.current) {
      return;
    }
    hasInitialized.current = true;
    
    fetchDashboardStats();
    fetchLatestSuccessfulRecords();
    fetchRecentFailedRecords();
    fetchSalesforceInstance();
  }, []);

  const fetchSalesforceInstance = async () => {
    try {
      const response = await api.get('/user-connection');
      if (response.data && response.data.status === 'success' && response.data.connection) {
        setSalesforceInstance(response.data.connection.instance_url);
      }
    } catch (error) {
      console.error('Error fetching Salesforce instance:', error);
    }
  };

  const fetchDashboardStats = async () => {
    try {
      setLoading(true);
      setError(null);
      
      const response = await api.get('/dashboard-stats');
      
      if (response.data && response.data.status === 'success') {
        setDashboardStats(response.data.dashboard_stats);
      }
    } catch (error) {
      console.error('Error fetching dashboard stats:', error);
      setError('Failed to load dashboard statistics');
    } finally {
      setLoading(false);
    }
  };

  const fetchLatestSuccessfulRecords = async () => {
    try {
      const response = await api.get('/latest-successful-migrations?limit=10');
      if (response.data && response.data.status === 'success') {
        setLatestSuccessfulRecords(response.data.records || []);
      }
    } catch (error) {
      console.error('Error fetching latest successful records:', error);
    }
  };

  const fetchRecentFailedRecords = async () => {
    try {
      const response = await api.get('/recent-failed-migrations?limit=10');
      if (response.data && response.data.status === 'success') {
        setRecentFailedRecords(response.data.records || []);
      }
    } catch (error) {
      console.error('Error fetching recent failed records:', error);
    }
  };

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

  // Simple function to display source data in readable format
  const getReadableData = (data) => {
    if (!data || typeof data !== 'object') return 'N/A';
    
    const readableFields = Object.entries(data)
      .filter(([key, value]) => value != null && value !== '' && value !== '-' && typeof value !== 'object')
      .map(([key, value]) => `${key}: ${value}`);
    
    return readableFields.length > 0 ? readableFields.join(' | ') : 'N/A';
  };

  if (loading) {
    return (
      <div className="dashboard-container">
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <p>Loading dashboard...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="dashboard-container">
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <h3>Error Loading Dashboard</h3>
          <p style={{ color: 'red' }}>{error}</p>
          <button 
            onClick={fetchDashboardStats}
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
    <div className="dashboard-container">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px', padding: '40px 40px 0 40px' }}>
        <div>
          <h2>Migration Dashboard</h2>
          <p style={{ margin: '8px 0 0 0' }}>Overview of your data pipeline runs and migration success rates.</p>
        </div>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
          <button 
            onClick={fetchDashboardStats}
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
            title="Refresh Dashboard"
          >
            <FiRefreshCw size={20} />
          </button>
        </div>
      </div>

      {/* Usage Indicator */}
      <div style={{ padding: '0 40px' }}>
        {billing && <UsageIndicator />}
      </div>

      {/* Dashboard Metrics */}
      <div style={{ 
        display: 'flex', 
        gap: '16px', 
        marginBottom: '32px',
        overflowX: 'auto',
        paddingBottom: '8px',
        paddingLeft: '40px',
        paddingRight: '40px',
        justifyContent: 'space-between'
      }}>
        {/* Success Rate Card */}
        <div style={{
          backgroundColor: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: '10px',
          padding: '14px',
          boxShadow: '0 2px 4px rgba(0, 0, 0, 0.05)',
          textAlign: 'center',
          minWidth: '140px',
          flex: '1',
          maxWidth: '180px',
          transition: 'all 0.2s ease',
          cursor: 'pointer',
          userSelect: 'none'
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = 'translateY(-2px)';
          e.currentTarget.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.1)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = 'translateY(0)';
          e.currentTarget.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.05)';
        }}>
          <div style={{ 
            backgroundColor: '#10b981', 
            borderRadius: '50%', 
            width: '36px', 
            height: '36px', 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            margin: '0 auto 8px auto' 
          }}>
            <FiTrendingUp size={16} color="white" />
          </div>
          <h3 style={{ margin: '0 0 3px 0', fontSize: '22px', fontWeight: '700', color: '#10b981', userSelect: 'none' }}>
            {dashboardStats.success_rate}%
          </h3>
          <p style={{ margin: 0, color: '#6b7280', fontSize: '11px', fontWeight: '500', userSelect: 'none' }}>
            Success Rate
          </p>
        </div>

        {/* Flows Card (formerly Reusable Flows) */}
        <div 
          style={{
            backgroundColor: '#fff',
            border: '1px solid #e5e7eb',
            borderRadius: '10px',
            padding: '14px',
            boxShadow: '0 2px 4px rgba(0, 0, 0, 0.05)',
            textAlign: 'center',
            minWidth: '140px',
            flex: '1',
            maxWidth: '180px',
            transition: 'all 0.2s ease',
            cursor: 'pointer',
            userSelect: 'none'
          }}
          onClick={() => navigate('/flows')}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'translateY(-2px)';
            e.currentTarget.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.1)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.05)';
          }}>
          <div style={{ 
            backgroundColor: '#f97316', 
            borderRadius: '50%', 
            width: '36px', 
            height: '36px', 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            margin: '0 auto 8px auto' 
          }}>
            <FiShare2 size={16} color="white" />
          </div>
          <h3 style={{ margin: '0 0 3px 0', fontSize: '22px', fontWeight: '700', color: '#f97316', userSelect: 'none' }}>
            {dashboardStats.reusable_flows}
          </h3>
          <p style={{ margin: 0, color: '#6b7280', fontSize: '11px', fontWeight: '500', userSelect: 'none' }}>
            Flows
          </p>
        </div>

        {/* Total Migrations Card */}
        <div style={{
          backgroundColor: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: '10px',
          padding: '14px',
          boxShadow: '0 2px 4px rgba(0, 0, 0, 0.05)',
          textAlign: 'center',
          minWidth: '140px',
          flex: '1',
          maxWidth: '180px',
          transition: 'all 0.2s ease',
          cursor: 'pointer',
          userSelect: 'none'
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = 'translateY(-2px)';
          e.currentTarget.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.1)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = 'translateY(0)';
          e.currentTarget.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.05)';
        }}>
          <div style={{ 
            backgroundColor: '#3b82f6', 
            borderRadius: '50%', 
            width: '36px', 
            height: '36px', 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            margin: '0 auto 8px auto' 
          }}>
            <FiDatabase size={16} color="white" />
          </div>
          <h3 style={{ margin: '0 0 3px 0', fontSize: '22px', fontWeight: '700', color: '#3b82f6', userSelect: 'none' }}>
            {dashboardStats.total_migrations}
          </h3>
          <p style={{ margin: 0, color: '#6b7280', fontSize: '11px', fontWeight: '500', userSelect: 'none' }}>
            Total Migrations
          </p>
        </div>

        {/* Success Records Card (formerly Total Records) */}
        <div 
          style={{
            backgroundColor: '#fff',
            border: '1px solid #e5e7eb',
            borderRadius: '10px',
            padding: '14px',
            boxShadow: '0 2px 4px rgba(0, 0, 0, 0.05)',
            textAlign: 'center',
            minWidth: '140px',
            flex: '1',
            maxWidth: '180px',
            transition: 'all 0.2s ease',
            cursor: 'pointer',
            userSelect: 'none'
          }}
          onClick={() => navigate('/successful-migrations')}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'translateY(-2px)';
            e.currentTarget.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.1)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.05)';
          }}>
          <div style={{ 
            backgroundColor: '#8b5cf6', 
            borderRadius: '50%', 
            width: '36px', 
            height: '36px', 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            margin: '0 auto 8px auto' 
          }}>
            <FiCheckCircle size={16} color="white" />
          </div>
          <h3 style={{ margin: '0 0 3px 0', fontSize: '22px', fontWeight: '700', color: '#8b5cf6', userSelect: 'none' }}>
            {dashboardStats.total_records - dashboardStats.failed_records}
          </h3>
          <p style={{ margin: 0, color: '#6b7280', fontSize: '11px', fontWeight: '500', userSelect: 'none' }}>
            Success Records
          </p>
        </div>

        {/* Failed Records Card */}
        <div 
          style={{
            backgroundColor: '#fff',
            border: '1px solid #e5e7eb',
            borderRadius: '10px',
            padding: '14px',
            boxShadow: '0 2px 4px rgba(0, 0, 0, 0.05)',
            textAlign: 'center',
            minWidth: '140px',
            flex: '1',
            maxWidth: '180px',
            transition: 'all 0.2s ease',
            cursor: 'pointer',
            userSelect: 'none'
          }}
          onClick={() => navigate('/failed-migrations')}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'translateY(-2px)';
            e.currentTarget.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.1)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow = '0 2px 4px rgba(0, 0, 0, 0.05)';
          }}>
          <div style={{ 
            backgroundColor: '#ef4444', 
            borderRadius: '50%', 
            width: '36px', 
            height: '36px', 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            margin: '0 auto 8px auto' 
          }}>
            <FiAlertCircle size={16} color="white" />
          </div>
          <h3 style={{ margin: '0 0 3px 0', fontSize: '22px', fontWeight: '700', color: '#ef4444', userSelect: 'none' }}>
            {dashboardStats.failed_records}
          </h3>
          <p style={{ margin: 0, color: '#6b7280', fontSize: '11px', fontWeight: '500', userSelect: 'none' }}>
            Failed Records
          </p>
        </div>
      </div>

      {/* Latest Successful Migrations Section */}
      <div style={{ marginBottom: '32px', padding: '0 40px' }}>
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'space-between', 
          marginBottom: '16px' 
        }}>
          <h3 style={{ margin: 0, fontSize: '18px', fontWeight: '600', color: '#1f2937' }}>
            Latest Successful Migrations
          </h3>
          <button 
            onClick={() => navigate('/successful-migrations')}
            className="secondary-button"
            style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
          >
            View All
            <FiTrendingUp size={14} />
          </button>
        </div>
        
        <div style={{
          backgroundColor: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: '8px',
          padding: '16px',
          boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)'
        }}>
          {latestSuccessfulRecords.length > 0 ? (
            <div style={{ overflowX: 'auto' }}>
              <table className="editable-table results-table" role="grid">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Status</th>
                    <th>Destination</th>
                    <th>Name</th>
                    <th>View Record</th>
                    <th>Migrated At</th>
                    <th>Record Data</th>
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const rows = [];
                    
                    
                    // Process each record and create merged rows
                    latestSuccessfulRecords.slice(0, 10).forEach((record, recordIndex) => {
                      // Parse source_data if it's a string, otherwise use as is
                      let sourceData = {};
                      try {
                        if (typeof record.source_data === 'string') {
                          sourceData = JSON.parse(record.source_data);
                        } else if (record.source_data) {
                          sourceData = record.source_data;
                        }
                      } catch (error) {
                        console.error('Error parsing source_data:', error);
                        sourceData = {};
                      }
                      
                      // Parse salesforce_results to get object types and statuses
                      let objectTypes = [];
                      let parsedResults = {};
                      let recordStatus = 'Success'; // Default fallback
                      const isMetadata = record.batch_type === 'metadata';
                      
                      try {
                        if (record.salesforce_results) {
                          parsedResults = typeof record.salesforce_results === 'string' ? JSON.parse(record.salesforce_results) : record.salesforce_results;
                          if (isMetadata) {
                            // Metadata: flat shape { id, action, success, view_link }
                            recordStatus = (parsedResults.action && String(parsedResults.action).charAt(0).toUpperCase() + String(parsedResults.action).slice(1)) || 'Success';
                          } else {
                            objectTypes = Object.keys(parsedResults).filter(key => key !== 'error');
                            for (const objType of objectTypes) {
                              const result = parsedResults[objType];
                              if (result && result.status) {
                                recordStatus = result.status.charAt(0).toUpperCase() + result.status.slice(1);
                                break;
                              }
                            }
                          }
                        }
                      } catch (error) {
                        console.error('Error parsing salesforce_results:', error);
                        objectTypes = [];
                      }
                      
                      // Name column: for metadata use source_data label/field; for data use object types
                      const displayName = isMetadata
                        ? (sourceData.label || sourceData.field_api_name || (sourceData.object_api_name && sourceData.field_api_name ? `${sourceData.object_api_name}.${sourceData.field_api_name}` : 'Custom field') || 'N/A')
                        : null;
                      
                      // View Record link: for metadata use view_link; for data build from id per object
                      const metadataViewLink = isMetadata ? (parsedResults.view_link || null) : null;
                      
                      // Create a single merged row for the record
                      rows.push(
                        <tr key={record.id}>
                          <td>{recordIndex + 1}</td>
                          <td className="status-cell">
                            {/* Determine badge styling based on status */}
                            {(() => {
                              let badgeStyle = {};
                              if (recordStatus.toLowerCase() === 'created') {
                                badgeStyle = {
                                  backgroundColor: '#d1fae5',
                                  color: '#065f46',
                                  border: '1px solid #a7f3d0'
                                };
                              } else if (recordStatus.toLowerCase() === 'updated') {
                                badgeStyle = {
                                  backgroundColor: '#dbeafe',
                                  color: '#1e40af',
                                  border: '1px solid #93c5fd'
                                };
                              } else {
                                // Default success styling for other statuses
                                badgeStyle = {
                                  backgroundColor: '#fef3c7',
                                  color: '#92400e',
                                  border: '1px solid #fcd34d'
                                };
                              }
                              
                              return (
                                <span className="status-badge" style={badgeStyle}>
                                  {recordStatus}
                                </span>
                              );
                            })()}
                          </td>
                          <td>{getDestinationLabel(record)}</td>
                          <td className="object-cell">
                            {isMetadata ? (
                              <div>{displayName}</div>
                            ) : objectTypes.length > 0 ? (
                              <div>
                                {objectTypes.map((objType, idx) => (
                                  <div key={objType} style={{ marginBottom: idx < objectTypes.length - 1 ? '4px' : '0' }}>
                                    {objType}
                                  </div>
                                ))}
                              </div>
                            ) : (
                              'N/A'
                            )}
                          </td>
                          <td className="view-record-cell">
                            {isMetadata ? (
                              metadataViewLink ? (
                                <a
                                  href={metadataViewLink}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  style={{ color: '#3b82f6', textDecoration: 'none', fontSize: '14px' }}
                                >
                                  View Record
                                </a>
                              ) : (
                                '-'
                              )
                            ) : objectTypes.length > 0 ? (
                              <div>
                                {objectTypes.map((objType, idx) => {
                                  const recordId = parsedResults[objType]?.id;
                                  const destinationType = record.destination_type || 'salesforce';
                                  let linkUrl = null;
                                  
                                  if (recordId) {
                                    if (destinationType === 'google_sheets') {
                                      linkUrl = `https://docs.google.com/spreadsheets/d/${recordId}`;
                                    } else {
                                      linkUrl = `${salesforceInstance}/${recordId}`;
                                    }
                                  }
                                  
                                  return linkUrl ? (
                                    <div key={objType} style={{ marginBottom: idx < objectTypes.length - 1 ? '4px' : '0' }}>
                                      <a
                                        href={linkUrl}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        style={{ color: '#3b82f6', textDecoration: 'none', fontSize: '14px' }}
                                      >
                                        View Record
                                      </a>
                                    </div>
                                  ) : (
                                    <div key={objType} style={{ marginBottom: idx < objectTypes.length - 1 ? '4px' : '0' }}>-</div>
                                  );
                                })}
                              </div>
                            ) : (
                              '-'
                            )}
                          </td>
                          <td className="date-cell">
                            {record.migrated_at ? formatDateTimeForUser(record.migrated_at, userTimezone, 'table') : 'N/A'}
                          </td>
                          <td className="key-fields-cell">
                            {getReadableData(sourceData)}
                          </td>
                        </tr>
                      );
                    });
                    
                    return rows;
                  })()}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '24px', color: '#6b7280' }}>
              No successful migrations yet
            </div>
          )}
        </div>
      </div>

      {/* Latest Failed Migrations Section */}
      <div style={{ marginBottom: '32px', padding: '0 40px' }}>
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'space-between', 
          marginBottom: '16px' 
        }}>
          <h3 style={{ margin: 0, fontSize: '18px', fontWeight: '600', color: '#1f2937' }}>
            Latest Failed Migrations
          </h3>
          <button 
            onClick={() => navigate('/failed-migrations')}
            className="secondary-button"
            style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
          >
            View All
            <FiAlertCircle size={14} />
          </button>
        </div>
        
        <div style={{
          backgroundColor: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: '8px',
          padding: '16px',
          boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)'
        }}>
          {recentFailedRecords.length > 0 ? (
            <div style={{ overflowX: 'auto' }}>
              <table className="editable-table results-table" role="grid">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Status</th>
                    <th>Destination</th>
                    <th>Name</th>
                    <th>Error</th>
                    <th>Failed Date</th>
                    <th>Record Data</th>
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const rows = [];
                    let rowIndex = 0;
                    
                    recentFailedRecords.slice(0, 10).forEach((record, recordIndex) => {
                      // Parse source_data if it's a string, otherwise use as is
                      let sourceData = {};
                      try {
                        if (typeof record.source_data === 'string') {
                          sourceData = JSON.parse(record.source_data);
                        } else if (record.source_data) {
                          sourceData = record.source_data;
                        }
                      } catch (error) {
                        console.error('Error parsing source_data:', error);
                        sourceData = {};
                      }
                      
                      // Parse salesforce_results to check for multiple objects
                      let objectTypes = [];
                      let parsedResults = {};
                      let failedObjects = [];
                      let successfulObjects = [];
                      
                      try {
                        if (record.salesforce_results) {
                          parsedResults = typeof record.salesforce_results === 'string' ? JSON.parse(record.salesforce_results) : record.salesforce_results;
                          objectTypes = Object.keys(parsedResults).filter(key => key !== 'error');
                          
                          // Separate successful and failed objects
                          objectTypes.forEach(objType => {
                            const result = parsedResults[objType];
                            if (result && result.status === 'error') {
                              failedObjects.push(objType);
                            } else if (result && (result.status === 'created' || result.status === 'updated')) {
                              successfulObjects.push(objType);
                            }
                          });
                        }
                      } catch (error) {
                        console.error('Error parsing salesforce_results:', error);
                        objectTypes = [record.object_type || 'N/A'];
                        failedObjects = objectTypes;
                      }
                      
                      // Only show failed objects in the failed migrations section
                      if (failedObjects.length > 0) {
                        failedObjects.forEach((objType, objIndex) => {
                          // Get the specific error message for this object
                          let objectErrorMessage = record.error_message || 'Unknown error';
                          if (parsedResults[objType] && parsedResults[objType].message) {
                            objectErrorMessage = parsedResults[objType].message;
                          }
                          
                          rows.push(
                            <tr key={`${record.id}-${objType}`}>
                              <td>{rowIndex + 1}</td>
                              <td className="status-cell">
                                <span className="status-badge" style={{
                                  backgroundColor: '#fee2e2',
                                  color: '#dc2626'
                                }}>
                                  Failed
                                </span>
                              </td>
                              <td>
                                {getDestinationLabel(record)}
                              </td>
                              <td className="object-cell">{objType}</td>
                              <td className="error-cell">
                                {objectErrorMessage}
                              </td>
                              <td className="date-cell">
                                {(record.failed_at || record.created_at) ? formatDateTimeForUser(record.failed_at || record.created_at, userTimezone, 'table') : 'N/A'}
                              </td>
                              <td className="key-fields-cell">
                                {getReadableData(sourceData)}
                              </td>
                            </tr>
                          );
                          rowIndex++;
                        });
                      } else if (objectTypes.length === 0) {
                        // Fallback for records without parsed results
                        rows.push(
                          <tr key={record.id}>
                            <td>{rowIndex + 1}</td>
                            <td className="status-cell">
                              <span className="status-badge" style={{
                                backgroundColor: '#fee2e2',
                                color: '#dc2626'
                              }}>
                                Failed
                              </span>
                            </td>
                            <td>
                              {getDestinationLabel(record)}
                            </td>
                            <td className="object-cell">{record.object_type || 'N/A'}</td>
                            <td className="error-cell">
                              {record.error_message || 'Unknown error'}
                            </td>
                            <td className="date-cell">
                              {(record.failed_at || record.created_at) ? formatDateTimeForUser(record.failed_at || record.created_at, userTimezone, 'table') : 'N/A'}
                            </td>
                            <td className="key-fields-cell">
                              {getReadableData(sourceData)}
                            </td>
                          </tr>
                        );
                        rowIndex++;
                      }
                    });
                    
                    return rows;
                  })()}
                </tbody>
              </table>
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '24px', color: '#6b7280' }}>
              No failed migrations yet
            </div>
          )}
        </div>
      </div>

    </div>
  );
};

export default Dashboard;

