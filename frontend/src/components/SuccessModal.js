import React from 'react';
import { FiCheckCircle, FiX, FiMail } from 'react-icons/fi';
import './SuccessModal.css';

const SuccessModal = ({ isOpen, onClose, message, type = 'success', context = 'create' }) => {
  if (!isOpen) return null;

  const getIcon = () => {
    switch (type) {
      case 'success':
        return <FiCheckCircle className="success-icon" />;
      case 'error':
        return <FiX className="error-icon" />;
      default:
        return <FiCheckCircle className="success-icon" />;
    }
  };

  const getTitle = () => {
    switch (type) {
      case 'success':
        return 'Success!';
      case 'error':
        return 'Error';
      default:
        return 'Success!';
    }
  };

  const isEmailNotification = message && message.includes('email notification');

  return (
    <div className="success-modal-overlay">
      <div className="success-modal">
        <div className="success-modal-header">
          <div className="success-modal-title">
            {getIcon()}
            <h3>{getTitle()}</h3>
          </div>
          <button className="success-close-btn" onClick={onClose}>
            <FiX />
          </button>
        </div>

        <div className="success-modal-content">
          <div className="success-message">
            <p className={type === 'error' ? 'error-description' : 'success-description'}>
              {message}
            </p>
          </div>

          {isEmailNotification && (
            <div className="success-info">
              <div className="success-info-header">
                <FiMail className="info-icon" />
                <h4>What happens next?</h4>
              </div>
              <div className="success-steps">
                <div className="success-step">
                  <span className="step-icon">🔍</span>
                  <span className="step-text">Archian AI is analyzing your database structure</span>
                </div>
                <div className="success-step">
                  <span className="step-icon">⏱️</span>
                  <span className="step-text">This process typically takes 2-5 minutes</span>
                </div>
                <div className="success-step">
                  <span className="step-icon">📧</span>
                  <span className="step-text">You'll receive an email when analysis is complete</span>
                </div>
                <div className="success-step">
                  <span className="step-icon">🚀</span>
                  <span className="step-text">Then you can start extracting data with natural language</span>
                </div>
              </div>
            </div>
          )}

          {!isEmailNotification && type === 'success' && context !== 'delete' && (
            <div className="success-tips">
              <h5>💡 Next Steps:</h5>
              <ul>
                <li>Your connection is now ready to use</li>
                <li>You can start extracting data from your database</li>
                <li>Try asking questions like "Show me customer data"</li>
              </ul>
            </div>
          )}
        </div>

        <div className="success-modal-actions">
          <button className="success-ok-btn" onClick={onClose}>
            Got it!
          </button>
        </div>
      </div>
    </div>
  );
};

export default SuccessModal;
