import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { FiArrowLeft } from 'react-icons/fi';
import api from './api';
import './ForgotPasswordPage.css';

const ForgotPasswordPage = () => {
  const [step, setStep] = useState('email'); // 'email', 'verify', 'newPassword'
  const [email, setEmail] = useState('');
  const [verificationCode, setVerificationCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const navigate = useNavigate();

  const handleSendCode = async (e) => {
    e.preventDefault();
    setSending(true);
    setError('');

    try {
      const response = await api.post('/api/forgot-password/send-code', { email });
      if (response.data.success) {
        setStep('verify');
        setSuccess('Verification code sent to your email!');
      } else {
        setError('Failed to send verification code. Please try again.');
      }
    } catch (err) {
      console.error(err);
      setError(err.response?.data?.detail || 'Error sending verification code. Please try again.');
    } finally {
      setSending(false);
    }
  };

  const handleVerifyCode = async (e) => {
    e.preventDefault();
    setVerifying(true);
    setError('');

    try {
      const response = await api.post('/api/forgot-password/verify-code', { 
        email, 
        verification_code: verificationCode 
      });
      if (response.data.success) {
        setStep('newPassword');
        setSuccess('Code verified! Please set your new password.');
      } else {
        setError('Invalid verification code. Please try again.');
      }
    } catch (err) {
      console.error(err);
      setError(err.response?.data?.detail || 'Error verifying code. Please try again.');
    } finally {
      setVerifying(false);
    }
  };

  const handleUpdatePassword = async (e) => {
    e.preventDefault();
    setUpdating(true);
    setError('');

    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      setUpdating(false);
      return;
    }

    if (newPassword.length < 6) {
      setError('Password must be at least 6 characters long.');
      setUpdating(false);
      return;
    }

    try {
      const response = await api.post('/api/forgot-password/update-password', {
        email,
        verification_code: verificationCode,
        new_password: newPassword
      });
      if (response.data.success) {
        setSuccess('Password updated successfully! Redirecting to login...');
        setTimeout(() => {
          navigate('/login');
        }, 2000);
      } else {
        setError('Failed to update password. Please try again.');
      }
    } catch (err) {
      console.error(err);
      setError(err.response?.data?.detail || 'Error updating password. Please try again.');
    } finally {
      setUpdating(false);
    }
  };

  const renderEmailStep = () => (
    <form onSubmit={handleSendCode}>
      <h3>Reset Your Password</h3>
      <p>Enter your registered email address to receive a verification code.</p>
      
      <input
        type="email"
        placeholder="Enter your email address"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
        className="input-field"
      />
      
      <button type="submit" disabled={sending} className="submit-button">
        {sending ? 'Sending...' : 'Send Verification Code'}
      </button>
    </form>
  );

  const renderVerifyStep = () => (
    <form onSubmit={handleVerifyCode}>
      <h3>Enter Verification Code</h3>
      <p>We've sent a verification code to {email}</p>
      
      <input
        type="text"
        placeholder="Enter verification code"
        value={verificationCode}
        onChange={(e) => setVerificationCode(e.target.value)}
        required
        className="input-field"
      />
      
      <button type="submit" disabled={verifying} className="submit-button">
        {verifying ? 'Verifying...' : 'Verify Code'}
      </button>
      
      <button 
        type="button" 
        onClick={() => setStep('email')} 
        className="back-button"
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
        title="Back to Email"
      >
        <FiArrowLeft size={20} />
      </button>
    </form>
  );

  const renderNewPasswordStep = () => (
    <form onSubmit={handleUpdatePassword}>
      <h3>Set New Password</h3>
      <p>Enter your new password below.</p>
      
      <input
        type="password"
        placeholder="New password"
        value={newPassword}
        onChange={(e) => setNewPassword(e.target.value)}
        required
        className="input-field"
      />
      
      <input
        type="password"
        placeholder="Confirm new password"
        value={confirmPassword}
        onChange={(e) => setConfirmPassword(e.target.value)}
        required
        className="input-field"
      />
      
      <button type="submit" disabled={updating} className="submit-button">
        {updating ? 'Updating...' : 'Update Password'}
      </button>
      
      <button 
        type="button" 
        onClick={() => setStep('verify')} 
        className="back-button"
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
        title="Back to Verification"
      >
        <FiArrowLeft size={20} />
      </button>
    </form>
  );

  return (
    <div className="forgot-password-page">
      <div className="forgot-password-container">
        <div className="icon-container">
          <img src="/icon.svg" alt="archian ai"/>
        </div>
        
        {step === 'email' && renderEmailStep()}
        {step === 'verify' && renderVerifyStep()}
        {step === 'newPassword' && renderNewPasswordStep()}
        
        {error && <div className="error-message">{error}</div>}
        {success && <div className="success-message">{success}</div>}
        
        <div className="back-to-login">
          <Link 
            to="/login"
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center',
              width: '48px',
              height: '48px',
              borderRadius: '50%',
              padding: '0',
              minWidth: '48px',
              textDecoration: 'none',
              color: 'inherit'
            }}
            title="Back to Login"
          >
            <FiArrowLeft size={20} />
          </Link>
        </div>
      </div>
    </div>
  );
};

export default ForgotPasswordPage;
