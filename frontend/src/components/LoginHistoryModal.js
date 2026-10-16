import React, { useState, useEffect } from 'react';
import { FiX, FiMonitor, FiSmartphone, FiTablet, FiGlobe, FiClock, FiMapPin, FiChevronLeft, FiChevronRight } from 'react-icons/fi';
import api from '../api';
import './LoginHistoryModal.css';

const LoginHistoryModal = ({ isOpen, onClose }) => {
  const [loginHistory, setLoginHistory] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalRecords, setTotalRecords] = useState(0);
  const limit = 10; // Records per page

  useEffect(() => {
    if (isOpen) {
      fetchLoginHistory(1);
    }
  }, [isOpen]);

  const fetchLoginHistory = async (page = 1) => {
    try {
      setIsLoading(true);
      setError(null);

      const response = await api.get(`/api/login-history?page=${page}&limit=${limit}`);
      
      if (response.data.success) {
        const { login_history, pagination } = response.data.data;
        setLoginHistory(login_history);
        setCurrentPage(pagination.page);
        setTotalPages(pagination.pages);
        setTotalRecords(pagination.total);
      } else {
        setError('Failed to load login history');
      }
    } catch (err) {
      console.error('Error fetching login history:', err);
      setError('Failed to load login history. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handlePageChange = (newPage) => {
    if (newPage >= 1 && newPage <= totalPages) {
      fetchLoginHistory(newPage);
    }
  };

  const getDeviceIcon = (userAgent) => {
    if (!userAgent) return FiGlobe;
    
    const ua = userAgent.toLowerCase();
    if (ua.includes('mobile') || ua.includes('android') || ua.includes('iphone')) {
      return FiSmartphone;
    } else if (ua.includes('tablet') || ua.includes('ipad')) {
      return FiTablet;
    }
    return FiMonitor;
  };

  const getBrowserInfo = (userAgent) => {
    if (!userAgent || userAgent === 'unknown') return 'Unknown Browser';
    
    const ua = userAgent.toLowerCase();
    if (ua.includes('chrome')) return 'Chrome';
    if (ua.includes('firefox')) return 'Firefox';
    if (ua.includes('safari') && !ua.includes('chrome')) return 'Safari';
    if (ua.includes('edge')) return 'Edge';
    if (ua.includes('opera')) return 'Opera';
    
    return 'Unknown Browser';
  };

  const getOSInfo = (userAgent) => {
    if (!userAgent || userAgent === 'unknown') return 'Unknown OS';
    
    const ua = userAgent.toLowerCase();
    if (ua.includes('windows')) return 'Windows';
    if (ua.includes('mac')) return 'macOS';
    if (ua.includes('linux')) return 'Linux';
    if (ua.includes('android')) return 'Android';
    if (ua.includes('ios') || ua.includes('iphone') || ua.includes('ipad')) return 'iOS';
    
    return 'Unknown OS';
  };

  const formatTimestamp = (timestamp) => {
    if (!timestamp) return 'Unknown';
    
    const date = new Date(timestamp);
    const now = new Date();
    const diffInHours = (now - date) / (1000 * 60 * 60);
    
    if (diffInHours < 1) {
      const diffInMinutes = Math.floor((now - date) / (1000 * 60));
      return `${diffInMinutes} minutes ago`;
    } else if (diffInHours < 24) {
      return `${Math.floor(diffInHours)} hours ago`;
    } else {
      return date.toLocaleDateString() + ' at ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="login-history-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>
            <FiClock />
            Login History
          </h2>
          <button className="close-btn" onClick={onClose}>
            <FiX />
          </button>
        </div>

        <div className="modal-content">
          {error && (
            <div className="error-message">
              <span>{error}</span>
            </div>
          )}

          {isLoading ? (
            <div className="loading-state">
              <div className="spinner"></div>
              <p>Loading login history...</p>
            </div>
          ) : (
            <>
              <div className="history-summary">
                <p>Showing {loginHistory.length} of {totalRecords} login sessions</p>
              </div>

              <div className="history-list">
                {loginHistory.length === 0 ? (
                  <div className="empty-state">
                    <FiClock />
                    <h3>No Login History</h3>
                    <p>No login sessions found for your account.</p>
                  </div>
                ) : (
                  loginHistory.map((session, index) => {
                    const DeviceIcon = getDeviceIcon(session.user_agent);
                    return (
                      <div key={session.id || index} className="history-item">
                        <div className="device-info">
                          <div className="device-icon">
                            <DeviceIcon />
                          </div>
                          <div className="device-details">
                            <h4>{getBrowserInfo(session.user_agent)} on {getOSInfo(session.user_agent)}</h4>
                            <p className="ip-address">
                              <FiMapPin />
                              {session.ip_address || 'Unknown IP'}
                            </p>
                          </div>
                        </div>
                        
                        <div className="session-info">
                          <div className="timestamp">
                            <FiClock />
                            {formatTimestamp(session.login_timestamp)}
                          </div>
                          <div className={`status-badge ${session.login_status}`}>
                            {session.login_status === 'success' ? 'Successful' : 'Failed'}
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>

              {totalPages > 1 && (
                <div className="pagination">
                  <button
                    className="page-btn"
                    onClick={() => handlePageChange(currentPage - 1)}
                    disabled={currentPage === 1}
                  >
                    <FiChevronLeft />
                    Previous
                  </button>
                  
                  <span className="page-info">
                    Page {currentPage} of {totalPages}
                  </span>
                  
                  <button
                    className="page-btn"
                    onClick={() => handlePageChange(currentPage + 1)}
                    disabled={currentPage === totalPages}
                  >
                    Next
                    <FiChevronRight />
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default LoginHistoryModal;
