import React from 'react';
import { FiTrash2, FiX, FiAlertTriangle } from 'react-icons/fi';
import './DeleteFlowModal.css';

const DeleteFlowModal = ({ isOpen, onClose, onConfirm, flowName, isLoading = false }) => {
  if (!isOpen) return null;

  return (
    <div className="delete-flow-modal-overlay" onClick={onClose}>
      <div className="delete-flow-modal" onClick={(e) => e.stopPropagation()}>
        <div className="delete-flow-modal-header">
          <div className="delete-flow-modal-icon">
            <FiTrash2 />
          </div>
          <h2>Delete Flow</h2>
          <button className="delete-flow-modal-close" onClick={onClose}>
            <FiX />
          </button>
        </div>

        <div className="delete-flow-modal-content">
          <div className="delete-flow-warning">
            <div className="warning-header">
              <div className="warning-icon remove-margin">
                <FiAlertTriangle />
              </div>
              <h3>This action cannot be undone</h3>
            </div>
            <p>
              Are you sure you want to delete the flow <strong>"{flowName}"</strong>?
            </p>
            <p>
              This will permanently delete all associated data including:
            </p>
            <ul>
              <li>Extracted schema</li>
              <li>Field mappings</li>
              <li>Flow configuration</li>
            </ul>
          </div>

          <div className="delete-flow-actions">
            <button 
              className="delete-flow-cancel" 
              onClick={onClose}
              disabled={isLoading}
            >
              Cancel
            </button>
            <button 
              className="delete-flow-confirm" 
              onClick={onConfirm}
              disabled={isLoading}
            >
              {isLoading ? 'Deleting...' : 'Delete Flow'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DeleteFlowModal;

