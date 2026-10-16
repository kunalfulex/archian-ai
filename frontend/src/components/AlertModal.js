import React from 'react';
import { FiAlertTriangle, FiCheckCircle, FiInfo, FiX } from 'react-icons/fi';
import './AlertModal.css';

const AlertModal = ({ 
  isOpen, 
  onClose, 
  title, 
  message, 
  type = 'error', // 'error', 'success', 'info', 'warning'
  showCloseButton = true 
}) => {
  if (!isOpen) return null;

  const getIcon = () => {
    switch (type) {
      case 'success':
        return <FiCheckCircle className="alert-icon success" />;
      case 'info':
        return <FiInfo className="alert-icon info" />;
      case 'warning':
        return <FiAlertTriangle className="alert-icon warning" />;
      default:
        return <FiAlertTriangle className="alert-icon error" />;
    }
  };

  const getTitle = () => {
    if (title) return title;
    switch (type) {
      case 'success':
        return 'Success';
      case 'info':
        return 'Information';
      case 'warning':
        return 'Warning';
      default:
        return 'Error';
    }
  };

  return (
    <div className="alert-modal-overlay" onClick={onClose}>
      <div className="alert-modal" onClick={(e) => e.stopPropagation()}>
        <div className="alert-modal-header">
          <div className="alert-modal-title">
            {getIcon()}
            <h3>{getTitle()}</h3>
          </div>
          {showCloseButton && (
            <button className="alert-close-btn" onClick={onClose}>
              <FiX />
            </button>
          )}
        </div>

        <div className="alert-modal-content">
          <p className="alert-message">{message}</p>
        </div>

        <div className="alert-modal-actions">
          <button className="alert-ok-btn" onClick={onClose}>
            OK
          </button>
        </div>
      </div>
    </div>
  );
};

export default AlertModal;
