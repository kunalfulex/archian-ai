import React, { useState } from 'react';
import { FiEye, FiEyeOff, FiX, FiCheck, FiAlertTriangle } from 'react-icons/fi';
import api from '../api';
import './ChangePasswordModal.css';

const ChangePasswordModal = ({ isOpen, onClose, onSuccess }) => {
  const [formData, setFormData] = useState({
    currentPassword: '',
    newPassword: '',
    confirmPassword: ''
  });
  
  const [showPasswords, setShowPasswords] = useState({
    current: false,
    new: false,
    confirm: false
  });
  
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
    
    // Clear any previous messages
    setError(null);
    setSuccess(null);
  };

  const togglePasswordVisibility = (field) => {
    setShowPasswords(prev => ({
      ...prev,
      [field]: !prev[field]
    }));
  };

  const validateForm = () => {
    if (!formData.currentPassword.trim()) {
      setError('Current password is required');
      return false;
    }
    
    if (!formData.newPassword.trim()) {
      setError('New password is required');
      return false;
    }
    
    if (formData.newPassword.length < 8) {
      setError('New password must be at least 8 characters long');
      return false;
    }
    
    if (formData.newPassword === formData.currentPassword) {
      setError('New password must be different from current password');
      return false;
    }
    
    if (formData.newPassword !== formData.confirmPassword) {
      setError('New passwords do not match');
      return false;
    }
    
    return true;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    if (!validateForm()) {
      return;
    }
    
    try {
      setIsLoading(true);
      setError(null);
      
      const response = await api.post('/api/change-password', {
        current_password: formData.currentPassword,
        new_password: formData.newPassword
      });
      
      if (response.data.success) {
        setSuccess('Password changed successfully!');
        setFormData({
          currentPassword: '',
          newPassword: '',
          confirmPassword: ''
        });
        
        // Close modal after 2 seconds
        setTimeout(() => {
          onSuccess();
          onClose();
        }, 2000);
      } else {
        setError(response.data.message || 'Failed to change password');
      }
    } catch (err) {
      console.error('Error changing password:', err);
      if (err.response?.data?.detail) {
        setError(err.response.data.detail);
      } else {
        setError('Failed to change password. Please try again.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleClose = () => {
    if (!isLoading) {
      setFormData({
        currentPassword: '',
        newPassword: '',
        confirmPassword: ''
      });
      setError(null);
      setSuccess(null);
      onClose();
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={handleClose}>
      <div className="change-password-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Change Password</h3>
          <button 
            type="button" 
            className="close-button" 
            onClick={handleClose}
            disabled={isLoading}
          >
            <FiX />
          </button>
        </div>
        
        <form onSubmit={handleSubmit} className="password-form">
          {error && (
            <div className="error-message">
              <FiAlertTriangle />
              <span>{error}</span>
            </div>
          )}
          
          {success && (
            <div className="success-message">
              <FiCheck />
              <span>{success}</span>
            </div>
          )}
          
          <div className="form-group">
            <label htmlFor="currentPassword">Current Password *</label>
            <div className="password-input-wrapper">
              <input
                type={showPasswords.current ? 'text' : 'password'}
                id="currentPassword"
                name="currentPassword"
                value={formData.currentPassword}
                onChange={handleInputChange}
                disabled={isLoading}
                required
                placeholder="Enter your current password"
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => togglePasswordVisibility('current')}
                disabled={isLoading}
              >
                {showPasswords.current ? <FiEyeOff /> : <FiEye />}
              </button>
            </div>
          </div>
          
          <div className="form-group">
            <label htmlFor="newPassword">New Password *</label>
            <div className="password-input-wrapper">
              <input
                type={showPasswords.new ? 'text' : 'password'}
                id="newPassword"
                name="newPassword"
                value={formData.newPassword}
                onChange={handleInputChange}
                disabled={isLoading}
                required
                placeholder="Enter your new password"
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => togglePasswordVisibility('new')}
                disabled={isLoading}
              >
                {showPasswords.new ? <FiEyeOff /> : <FiEye />}
              </button>
            </div>
            <small className="password-hint">
              Password must be at least 8 characters long
            </small>
          </div>
          
          <div className="form-group">
            <label htmlFor="confirmPassword">Confirm New Password *</label>
            <div className="password-input-wrapper">
              <input
                type={showPasswords.confirm ? 'text' : 'password'}
                id="confirmPassword"
                name="confirmPassword"
                value={formData.confirmPassword}
                onChange={handleInputChange}
                disabled={isLoading}
                required
                placeholder="Confirm your new password"
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => togglePasswordVisibility('confirm')}
                disabled={isLoading}
              >
                {showPasswords.confirm ? <FiEyeOff /> : <FiEye />}
              </button>
            </div>
          </div>
          
          <div className="form-actions">
            <button
              type="button"
              className="cancel-btn"
              onClick={handleClose}
              disabled={isLoading}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="submit-btn"
              disabled={isLoading}
            >
              {isLoading ? (
                <>
                  <div className="spinner-small"></div>
                  Changing Password...
                </>
              ) : (
                'Change Password'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default ChangePasswordModal;
