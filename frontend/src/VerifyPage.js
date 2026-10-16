// src/pages/VerifyPage.js

import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { FiArrowLeft } from 'react-icons/fi';
import './VerifyPage.css';
import api from './api';
import { checkSession, clearPreMfaToken, setUserEmail } from './utils/authStorage';

const VerifyPage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const queryParams = new URLSearchParams(location.search);
  const email = queryParams.get('email');
  const nextUrl = queryParams.get('next');

  const [code, setCode] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState('');

  const handleVerifyCode = async (e) => {
    e.preventDefault();
    setVerifying(true);
    setError('');

    try {
      const response = await api.post('/api/verify-code', { email, code });

      if (response.data.success) {
        setUserEmail(response.data.email || email);
        await checkSession(true);
        const redirectTo = nextUrl && nextUrl.startsWith('/') ? nextUrl : '/chat';
        navigate(redirectTo);
      } else {
        setError('Invalid code. Please try again.');
      }
    } catch (err) {
      console.error(err);
      if (err.response?.status === 401) {
        if (err.response.data.detail?.includes('temporary token')) {
          setError('Login session expired. Please login again.');
          clearPreMfaToken();
          navigate('/login');
        } else {
          setError('Invalid verification code. Please try again.');
        }
      } else {
        setError('Error verifying code. Please try again.');
      }
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="verify-page">
      <h2>Enter Verification Code</h2>
      <p>We sent a 6-digit code to <b>{email}</b></p>
      <form className="align-center" onSubmit={handleVerifyCode}>
        <input
          type="text"
          placeholder="Enter 6-digit code"
          value={code}
          required
          onChange={(e) => setCode(e.target.value)}
          maxLength="6"
          className="input-field"
        />
        <button type="submit" disabled={verifying} className="submit-button">
          {verifying ? 'Verifying...' : 'Verify Code'}
        </button>
        {error && <div className="error-message">{error}</div>}
      </form>

      <div className="back-to-login">
        <button
          type="button"
          className="back-button"
          onClick={() => {
            clearPreMfaToken();
            navigate('/login');
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            padding: '0',
            minWidth: '48px',
          }}
          title="Back to Login"
        >
          <FiArrowLeft size={20} />
        </button>
      </div>
    </div>
  );
};

export default VerifyPage;
