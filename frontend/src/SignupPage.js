import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import api from './api';
import { checkSession } from './utils/authStorage';
import TurnstileField from './components/TurnstileField';
import { TURNSTILE_SITE_KEY, useTurnstile } from './hooks/useTurnstile';
import './SignupPage.css';

const SignupPage = () => {
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    confirmPassword: '',
    firstName: '',
    lastName: ''
  });
  const [timezone, setTimezone] = useState('America/Los_Angeles|Los Angeles, USA'); // Default timezone
  const [signingUp, setSigningUp] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [passwordStrength, setPasswordStrength] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const { turnstileToken, containerRef, resetTurnstile, siteKeyConfigured } = useTurnstile();
  const navigate = useNavigate();

  // Function to detect browser timezone and convert to IANA format with display name
  const detectBrowserTimezone = () => {
    try {
      const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      
      // Map IANA timezones to display names
      const timezoneDisplayNames = {
        'America/Los_Angeles': 'Los Angeles, USA',
        'America/San_Francisco': 'San Francisco, USA',
        'America/Seattle': 'Seattle, USA',
        'America/Vancouver': 'Vancouver, Canada',
        'America/Denver': 'Denver, USA',
        'America/Phoenix': 'Phoenix, USA',
        'America/Chicago': 'Chicago, USA',
        'America/Dallas': 'Dallas, USA',
        'America/New_York': 'New York, USA',
        'America/Toronto': 'Toronto, Canada',
        'America/Miami': 'Miami, USA',
        'Europe/London': 'London, UK',
        'Europe/Dublin': 'Dublin, Ireland',
        'Europe/Paris': 'Paris, France',
        'Europe/Berlin': 'Berlin, Germany',
        'Europe/Rome': 'Rome, Italy',
        'Europe/Madrid': 'Madrid, Spain',
        'Asia/Tokyo': 'Tokyo, Japan',
        'Asia/Shanghai': 'Shanghai, China',
        'Asia/Kolkata': 'Mumbai, India',
        'Australia/Sydney': 'Sydney, Australia',
        'Australia/Melbourne': 'Melbourne, Australia',
        'Pacific/Auckland': 'Auckland, New Zealand'
      };
      
      const displayName = timezoneDisplayNames[browserTimezone] || browserTimezone.replace('_', ' ');
      return `${browserTimezone}|${displayName}`;
      
    } catch (error) {
      console.warn('Failed to detect timezone, using default:', error);
      return 'America/Los_Angeles|Los Angeles, USA'; // Fallback to Los Angeles
    }
  };

  useEffect(() => {
    checkSession().then((valid) => {
      if (valid) {
        navigate('/chat');
      }
    });
    
    // Detect and set browser timezone on component mount
    const detectedTimezone = detectBrowserTimezone();
    setTimezone(detectedTimezone);
  }, [navigate]);

  const checkPasswordStrength = (password) => {
    if (password.length === 0) return '';
    if (password.length < 8) return 'weak';
    
    const hasUpper = /[A-Z]/.test(password);
    const hasLower = /[a-z]/.test(password);
    const hasNumber = /\d/.test(password);
    const hasSpecial = /[!@#$%^&*(),.?":{}|<>]/.test(password);
    
    const criteria = [hasUpper, hasLower, hasNumber, hasSpecial].filter(Boolean).length;
    
    if (criteria >= 4) return 'strong';
    if (criteria >= 3) return 'medium';
    return 'weak';
  };

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
    
    // Check password strength on password input
    if (name === 'password') {
      setPasswordStrength(checkPasswordStrength(value));
    }
  };

  const handleGoogleLogin = async () => {
    try {
      // Get Google OAuth URL from backend
      const response = await api.get('/api/auth/google/url');
      
      if (response.data.success) {
        // Redirect to Google OAuth
        window.location.href = response.data.auth_url;
      } else {
        setError('Failed to initialize Google login');
      }
    } catch (error) {
      console.error('Google login error:', error);
      setError('Failed to initialize Google login');
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSigningUp(true);
    setError('');
    setSuccess('');

    // Validation
    if (formData.password !== formData.confirmPassword) {
      setError('Passwords do not match');
      setSigningUp(false);
      return;
    }

    if (!turnstileToken && TURNSTILE_SITE_KEY) {
      setError('Please complete the verification below.');
      setSigningUp(false);
      return;
    }

    // Strong password validation
    if (formData.password.length < 8) {
      setError('Password must be at least 8 characters long');
      setSigningUp(false);
      return;
    }

    // Check for at least one uppercase, one lowercase, and one number
    const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$/;
    if (!passwordRegex.test(formData.password)) {
      setError('Password must contain at least one uppercase letter, one lowercase letter, and one number');
      setSigningUp(false);
      return;
    }

    try {
      const payload = {
        email: formData.email,
        password: formData.password,
        first_name: formData.firstName,
        last_name: formData.lastName,
        timezone: timezone
      };
      if (turnstileToken) payload.turnstile_token = turnstileToken;
      const response = await api.post('/api/signup', payload);

      if (response.data.success) {
        if (response.data.message === 'Account reactivated successfully') {
          setSuccess('Account reactivated successfully! Please sign in.');
        } else {
          setSuccess('Account created successfully! Please sign in.');
        }
        setTimeout(() => {
          navigate('/login');
        }, 2000);
      }
    } catch (err) {
      console.error(err);
      const detail = err.response?.data?.detail;
      const errorMessage = Array.isArray(detail) ? (detail[0]?.msg || detail[0]) : detail;
      setError(typeof errorMessage === 'string' ? errorMessage : 'Error creating account. Please try again.');
      resetTurnstile();
    } finally {
      setSigningUp(false);
    }
  };

  return (
    <div className="signup-page">
      <div className="signup-container">
        <div className="icon-container">
          <img src="/icon.svg" alt="archian ai"/>
        </div>
        <h2 className="archian-brand-title">archian ai</h2>
        
        {/* Google Login Button */}
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
        
        <form onSubmit={handleSubmit}>
          <div className="form-row">
            <input
              type="text"
              name="firstName"
              placeholder="First Name"
              value={formData.firstName}
              onChange={handleInputChange}
              required
              className="input-field"
            />
            <input
              type="text"
              name="lastName"
              placeholder="Last Name"
              value={formData.lastName}
              onChange={handleInputChange}
              required
              className="input-field"
            />
          </div>
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
          
          {/* Password Requirements Display - Only show when user starts typing */}
          {formData.password.length > 0 && (
            <div className="password-requirements">
              <div className="requirements-list">
                <div className={`requirement ${formData.password.length >= 8 ? 'met' : 'unmet'}`}>
                  <span className="requirement-icon">
                    {formData.password.length >= 8 ? (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20,6 9,17 4,12"></polyline>
                      </svg>
                    ) : (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                      </svg>
                    )}
                  </span>
                  At least 8 characters
                </div>
                <div className={`requirement ${/[A-Z]/.test(formData.password) ? 'met' : 'unmet'}`}>
                  <span className="requirement-icon">
                    {/[A-Z]/.test(formData.password) ? (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20,6 9,17 4,12"></polyline>
                      </svg>
                    ) : (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                      </svg>
                    )}
                  </span>
                  One uppercase letter
                </div>
                <div className={`requirement ${/[a-z]/.test(formData.password) ? 'met' : 'unmet'}`}>
                  <span className="requirement-icon">
                    {/[a-z]/.test(formData.password) ? (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20,6 9,17 4,12"></polyline>
                      </svg>
                    ) : (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                      </svg>
                    )}
                  </span>
                  One lowercase letter
                </div>
                <div className={`requirement ${/\d/.test(formData.password) ? 'met' : 'unmet'}`}>
                  <span className="requirement-icon">
                    {/\d/.test(formData.password) ? (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20,6 9,17 4,12"></polyline>
                      </svg>
                    ) : (
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="18" y1="6" x2="6" y2="18"></line>
                        <line x1="6" y1="6" x2="18" y2="18"></line>
                      </svg>
                    )}
                  </span>
                  One number
                </div>
              </div>
            </div>
          )}
          {passwordStrength && (
            <div className={`password-strength ${passwordStrength}`}>
              Password strength: <span className="strength-text">{passwordStrength.toUpperCase()}</span>
            </div>
          )}
          <div className="password-field-container">
            <input
              type={showConfirmPassword ? "text" : "password"}
              name="confirmPassword"
              placeholder="Confirm Password"
              value={formData.confirmPassword}
              onChange={handleInputChange}
              required
              className="input-field"
            />
            <button
              type="button"
              className="password-toggle-btn"
              onClick={() => setShowConfirmPassword(!showConfirmPassword)}
              tabIndex="-1"
            >
              {showConfirmPassword ? (
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
          <button type="submit" disabled={signingUp || (siteKeyConfigured && !turnstileToken)} className="submit-button">
            {signingUp ? 'Creating Account...' : 'Create Account'}
          </button>
          {error && <div className="error-message">{error}</div>}
          {success && <div className="success-message">{success}</div>}
        </form>
        <div className="login-link">
          Already have an account? <Link to="/login">Sign in</Link>
        </div>
      </div>
    </div>
  );
};

export default SignupPage;
