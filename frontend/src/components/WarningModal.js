import React from 'react';
import { FiAlertTriangle, FiRefreshCw, FiX, FiExternalLink } from 'react-icons/fi';
import './WarningModal.css';

const WarningModal = ({ isOpen, onClose, errorMessage, onReconnect, customButtons = false, confirmText = "Reconnect Now", confirmIcon = FiRefreshCw, customTitle = "Salesforce Connection Issue", showTips = true, hideConnectionsButton = false }) => {
  if (!isOpen) return null;

  const handleReconnect = () => {
    if (onReconnect) {
      onReconnect();
    }
    onClose();
  };

  const handleGoToConnections = () => {
    // Navigate to connections page
    window.location.href = '/connections';
  };

  return (
    <div className="warning-modal-overlay">
      <div className="warning-modal">
        <div className="warning-modal-header">
          <div className="warning-modal-title">
            <FiAlertTriangle className="warning-icon" />
            <h3>{customTitle}</h3>
          </div>
          <button className="warning-close-btn" onClick={onClose}>
            <FiX />
          </button>
        </div>

        <div className="warning-modal-content">
          <div className="warning-message">
            <p className="warning-description">
              {errorMessage || 'There was an issue with your Salesforce connection.'}
            </p>
          </div>

          {!customButtons && (
            <div className="warning-solution">
              <div className="warning-solution-header">
                <FiRefreshCw className="solution-icon" />
                <h4>How to Fix This</h4>
              </div>
              <div className="warning-steps">
                <div className="warning-step">
                  <span className="step-number">1</span>
                  <span className="step-text">Go to the Connections tab</span>
                </div>
                <div className="warning-step">
                  <span className="step-number">2</span>
                  <span className="step-text">Disconnect your current Salesforce connection</span>
                </div>
                <div className="warning-step">
                  <span className="step-number">3</span>
                  <span className="step-text">Reconnect with proper OAuth scopes</span>
                </div>
              </div>
            </div>
          )}

          {showTips && !customButtons && (
            <div className="warning-tips">
              <h5>💡 Tips:</h5>
              <ul>
                <li>Make sure your External Client App has the required OAuth scopes</li>
                <li>Check that your Salesforce org is accessible</li>
                <li>Verify your External Client App credentials are correct</li>
              </ul>
            </div>
          )}
        </div>

        <div className="warning-modal-actions">
          <button className="warning-cancel-btn" onClick={onClose}>
            Cancel
          </button>
          {customButtons ? (
            <button className={`warning-reconnect-btn ${confirmText.includes('Delete') ? 'delete-action' : ''}`} onClick={handleReconnect}>
              {confirmIcon ? React.createElement(confirmIcon) : <FiRefreshCw />}
              {confirmText}
            </button>
          ) : (
            <>
              <button className="warning-reconnect-btn" onClick={handleReconnect}>
                <FiRefreshCw />
                Reconnect Now
              </button>
              {!hideConnectionsButton && (
                <button className="warning-connections-btn" onClick={handleGoToConnections}>
                  <FiExternalLink />
                  Go to Connections
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default WarningModal;
