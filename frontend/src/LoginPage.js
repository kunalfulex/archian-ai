import React, { useState, useEffect } from 'react';
import { useNavigate, Link, useSearchParams } from 'react-router-dom';
import api from './api';
import { checkSession } from './utils/authStorage';
import TurnstileField from './components/TurnstileField';
import { TURNSTILE_SITE_KEY, useTurnstile } from './hooks/useTurnstile';
import './LoginPage.css';

const LoginPage = () => {
  const [searchParams] = useSearchParams();
  const nextUrl = searchParams.get('next');
  const [formData, setFormData] = useState({
    email: '',
    password: ''
  });
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const navigate = useNavigate();
  const { turnstileToken, containerRef, resetTurnstile, siteKeyConfigured } = useTurnstile();

  useEffect(() => {
    checkSession().then((valid) => {
      if (valid) {
        navigate(nextUrl && nextUrl.startsWith('/') ? nextUrl : '/chat');
      }
    });
  }, [navigate, nextUrl]);

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setSending(true);
    setError('');

    if (!turnstileToken && TURNSTILE_SITE_KEY) {
      setError('Please complete the verification below.');
      setSending(false);
      return;
    }

    try {
      const payload = { ...formData };
      if (turnstileToken) payload.turnstile_token = turnstileToken;

      const loginResponse = await api.post('/api/login', payload);
      
      if (loginResponse.data.success) {
        const codeResponse = await api.post('/api/send-code', { email: formData.email });
        
        if (codeResponse.data.success) {
          const verifyUrl = nextUrl ? `/verify?email=${encodeURIComponent(formData.email)}&next=${encodeURIComponent(nextUrl)}` : `/verify?email=${encodeURIComponent(formData.email)}`;
          navigate(verifyUrl);
        } else {
          setError('Login successful but failed to send verification code. Please try again.');
        }
      } else {
        setError('Invalid email or password. Please try again.');
      }
    } catch (err) {
      console.error(err);
      if (err.response?.status === 401) {
        setError('Invalid email or password. Please try again.');
      } else if (err.response?.status === 403) {
        setError(err.response.data.detail || 'Account access denied. Please contact support.');
      } else if (err.response?.status === 400) {
        const detail = err.response.data.detail;
        setError(typeof detail === 'string' ? detail : 'Verification failed. Please try again.');
      } else {
        setError('Login failed. Please try again.');
      }
      resetTurnstile();
    } finally {
      setSending(false);
    }
  };

  const handleGoogleLogin = async () => {
    try {
      const response = await api.get('/api/auth/google/url');
      
      if (response.data.success) {
        window.location.href = response.data.auth_url;
      } else {
        setError('Failed to initialize Google login');
      }
    } catch (error) {
      console.error('Google login error:', error);
      setError('Failed to initialize Google login');
    }
  };

  return (
    <div className="login-page">
      <div className="login-container">
        <div className="icon-container">
          <img src="/icon.svg" alt="archian ai"/>
        </div>
        <h3 className="archian-brand-title">archian ai</h3>
        
        <button 
          onClick={handleGoogleLogin} 
          className="google-login-button"
          type="button"
        >
          <div className="google-button-content">
            <div className="google-icon">
              <svg width="18" height="18" viewBox="0 0 18 18">
                <path fill="#4285F4" d="M17.64,9.20454545 C17.64,8.56636364 17.5827273,7.95272727 17.4763636,7.36363636 L9,7.36363636 L9,10.845 L13.8436364,10.845 C13.635,11.97 13.0009091,12.9231818 12.0477273,13.5613636 L12.0477273,15.8195455 L14.9563636,15.8195455 C16.6581818,14.2527273 17.64,11.9454545 17.64,9.20454545 Z"/>
                <path fill="#34A853" d="M9,18 C11.43,18 13.4672727,17.1940909 14.9563636,15.8195455 L12.0477273,13.5613636 C11.2418182,14.1013636 10.2109091,14.4204545 9,14.4204545 C6.65590909,14.4204545 4.67181818,12.8863636 3.96409091,10.71 L0.957272727,10.71 L0.957272727,13.0418182 C2.43818182,15.9831818 5.48181818,18 9,18 Z"/>
                <path fill="#FBBC05" d="M3.96409091,10.71 C3.78409091,10.17 3.68181818,9.59318182 3.68181818,9 C3.68181818,8.40681818 3.78409091,7.83 3.96409091,7.29 L3.96409091,4.95818182 L0.957272727,4.95818182 C0.347727273,6.17318182 0,7.54772727 0,9 C0,10.4522727 0.347727273,11.8268182 0.957272727,13.0418182 L3.96409091,10.71 Z"/>
                <path fill="#EA4335" d="M9,3.57954545 C10.3213636,3.57954545 11.5077273,4.03363636 12.4404545,4.92545455 L15.0218182,2.34409091 C13.4631818,0.891818182 11.4259091,0 9,0 C5.48181818,0 2.43818182,2.01681818 0.957272727,4.95818182 L3.96409091,7.29 C4.67181818,5.11363636 6.65590909,3.57954545 9,3.57954545 Z"/>
              </svg>
            </div>
            <span>Sign in with Google</span>
          </div>
        </button>

        <div className="separator">
          <span>OR</span>
        </div>

        <form onSubmit={handleLogin}>
          <input
            type="email"
            name="email"
            placeholder="you@example.com"
            value={formData.email}
            onChange={handleInputChange}
            required
            className="input-field"
          />
          <div className="password-field-container">
            <input
              type={showPassword ? "text" : "password"}
              name="password"
              placeholder="Password"
              value={formData.password}
              onChange={handleInputChange}
              required
              className="input-field"
            />
            <button
              type="button"
              className="password-toggle-btn"
              onClick={() => setShowPassword(!showPassword)}
              tabIndex="-1"
            >
              {showPassword ? (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                  <line x1="1" y1="1" x2="23" y2="23"/>
                </svg>
              ) : (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                  <circle cx="12" cy="12" r="3"/>
                </svg>
              )}
            </button>
          </div>
          <TurnstileField containerRef={containerRef} siteKeyConfigured={siteKeyConfigured} />
          <button
            type="submit"
            disabled={sending || (siteKeyConfigured && !turnstileToken)}
            className="submit-button"
          >
            {sending ? 'Signing in...' : 'Sign in'}
          </button>
          {error && <div className="error-message">{error}</div>}
        </form>
        
        <div className="signup-link">
          Don't have an account? <Link to="/signup">Sign up</Link>
        </div>
        <div className="forgot-password-link">
          <Link to="/forgot-password">Forgot your password?</Link>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
