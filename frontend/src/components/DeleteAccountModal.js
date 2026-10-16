import React, { useState } from 'react';
import './DeleteAccountModal.css';

const DeleteAccountModal = ({ isOpen, onClose, onConfirm, isLoading = false }) => {
  const [confirmationText, setConfirmationText] = useState('');
  const [reason, setReason] = useState('User requested account deletion');

  const handleConfirm = () => {
    if (confirmationText === 'DELETE') {
      onConfirm(reason);
    }
  };

  const handleClose = () => {
    setConfirmationText('');
    setReason('');
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="delete-account-modal-overlay">
      <div className="delete-account-modal">
        <div className="delete-account-modal-header">
          <div className="delete-account-modal-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" fill="currentColor"/>
            </svg>
          </div>
          <h2>Delete Account</h2>
          <button className="delete-account-modal-close" onClick={handleClose}>
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M15 5L5 15M5 5L15 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        </div>

        <div className="delete-account-modal-content">
          <div className="delete-account-warning">
            <div className="warning-header">
              <div className="warning-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </div>
              <h3>This action cannot be undone after 30 days</h3>
            </div>
            <p>
              When you delete your account:
            </p>
            <ul>
              <li>Your account will be immediately deactivated</li>
              <li>All your data will be preserved for 30 days</li>
              <li>You can reactivate your account within 30 days by signing up again</li>
              <li>After 30 days, all data will be permanently deleted</li>
            </ul>
          </div>

          <div className="delete-account-reason">
            <label htmlFor="deletion-reason">Reason for deletion (optional):</label>
            <select 
              id="deletion-reason" 
              value={reason} 
              onChange={(e) => setReason(e.target.value)}
            >
              <option value="User requested account deletion">User requested account deletion</option>
              <option value="No longer needed">No longer needed</option>
              <option value="Privacy concerns">Privacy concerns</option>
              <option value="Switching to different service">Switching to different service</option>
              <option value="Technical issues">Technical issues</option>
              <option value="Other">Other</option>
            </select>
          </div>

          <div className="delete-account-confirmation">
            <label htmlFor="confirmation-text">
              Type <strong>DELETE</strong> to confirm:
            </label>
            <input
              id="confirmation-text"
              type="text"
              value={confirmationText}
              onChange={(e) => setConfirmationText(e.target.value)}
              placeholder="Type DELETE to confirm"
              className={confirmationText === 'DELETE' ? 'valid' : 'invalid'}
            />
          </div>

          <div className="delete-account-actions">
            <button 
              className="delete-account-cancel" 
              onClick={handleClose}
              disabled={isLoading}
            >
              Cancel
            </button>
            <button 
              className="delete-account-confirm" 
              onClick={handleConfirm}
              disabled={confirmationText !== 'DELETE' || isLoading}
            >
              {isLoading ? 'Deleting Account...' : 'Delete Account'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DeleteAccountModal;
