import React from 'react';
import api from '../api';
import { setSessionToken, checkSession } from '../utils/authStorage';
import './AccountReactivationModal.css';
import { SUPPORT_EMAIL } from '../config/site';

const AccountReactivationModal = ({ 
  isOpen, 
  onClose, 
  userEmail, 
  userId, 
  googleId, 
  onReactivationSuccess 
}) => {
  const [isLoading, setIsLoading] = React.useState(false);
  const [error, setError] = React.useState('');

  const handleReactivation = async () => {
    setIsLoading(true);
    setError('');

    try {
      const response = await api.post('/api/auth/google/reactivate', {
        user_id: userId,
        google_id: googleId
      });

      if (response.data.success) {
        setSessionToken(null, response.data.email);
        await checkSession(true);
        onReactivationSuccess();
      } else {
        setError(response.data.message || 'Reactivation failed');
      }
    } catch (error) {
      console.error('Reactivation error:', error);
      setError('Failed to reactivate account. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleContactSupport = () => {
    window.open(`mailto:${SUPPORT_EMAIL}?subject=Account Reactivation Request`, '_blank');
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="reactivation-modal-overlay">
      <div className="reactivation-modal">
        <div className="reactivation-modal-header">
          <h2>Account Reactivation Required</h2>
          <button className="close-button" onClick={onClose}>
            ×
          </button>
        </div>
        
        <div className="reactivation-modal-content">
          <div className="warning-icon">⚠️</div>
          
          <p className="reactivation-message">
            Your account with email <strong>{userEmail}</strong> was previously deleted.
          </p>
          
          <p className="reactivation-question">
            Do you want to reactivate your account?
          </p>
          
          {error && (
            <div className="error-message">
              {error}
            </div>
          )}
        </div>
        
        <div className="reactivation-modal-actions">
          <button
            className="reactivate-button"
            onClick={handleReactivation}
            disabled={isLoading}
          >
            {isLoading ? 'Reactivating...' : 'Yes, Reactivate My Account'}
          </button>
          
          <button
            className="contact-support-button"
            onClick={handleContactSupport}
            disabled={isLoading}
          >
            No, Contact Support
          </button>
        </div>
        
        <div className="reactivation-modal-footer">
          <p>
            If you don't want to reactivate, please contact us at{' '}
            <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
          </p>
        </div>
      </div>
    </div>
  );
};

export default AccountReactivationModal;
