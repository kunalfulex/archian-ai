import React, { useEffect, useState, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from './api';
import { setSessionToken, checkSession } from './utils/authStorage';
import AccountReactivationModal from './components/AccountReactivationModal';
import './GoogleOAuthCallback.css';

const GoogleOAuthCallback = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState('Processing...');
  const [error, setError] = useState('');
  const hasProcessed = useRef(false); // Use ref to persist across re-renders
  
  // Reactivation modal state
  const [showReactivationModal, setShowReactivationModal] = useState(false);
  const [reactivationData, setReactivationData] = useState(null);

  // Detect browser timezone (same function as SignupPage.js)
  const detectBrowserTimezone = () => {
    try {
      const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      
      // Convert IANA timezone to UTC offset format
      const now = new Date();
      const utcOffset = -now.getTimezoneOffset() / 60; // Convert minutes to hours
      
      // Format as UTC+X or UTC-X
      const offsetString = utcOffset >= 0 ? `UTC+${utcOffset}` : `UTC${utcOffset}`;
      
      // Map common timezone names
      const timezoneNames = {
        'America/Los_Angeles': 'Pacific Time',
        'America/Denver': 'Mountain Time',
        'America/Chicago': 'Central Time',
        'America/New_York': 'Eastern Time',
        'Europe/London': 'GMT',
        'Europe/Paris': 'Central European',
        'Europe/Berlin': 'Central European',
        'Asia/Tokyo': 'Japan Time',
        'Asia/Shanghai': 'China Time',
        'Asia/Kolkata': 'India Time',
        'Australia/Sydney': 'Australian Eastern'
      };
      
      const timezoneName = timezoneNames[browserTimezone] || 'Local Time';
      return `${offsetString} (${timezoneName})`;
      
    } catch (error) {
      console.warn('Failed to detect timezone, using default:', error);
      return 'UTC-8 (Pacific Time)'; // Fallback to Pacific Time
    }
  };

  useEffect(() => {
    const handleCallback = async () => {
      try {
        const code = searchParams.get('code');
        const state = searchParams.get('state');

        if (!code) {
          setError('No authorization code received from Google');
          return;
        }

        // Prevent multiple executions using ref
        if (hasProcessed.current) {
          return;
        }
        hasProcessed.current = true;

        setStatus('Authenticating with Google...');

        // Detect timezone from browser
        const timezone = detectBrowserTimezone();

        // Call backend to exchange code for tokens, including timezone
        const response = await api.get('/api/auth/google/callback', {
          params: { code, state, timezone }
        });

        // Check if this is a Google Sheets OAuth request (state contains ':')
        const isGoogleSheetsRequest = state && state.includes(':');

        if (response.data.success) {
          if (isGoogleSheetsRequest) {
            setStatus('Google Sheets connected successfully!');
            const redirectUrl = response.data.redirect_url || '/connections';
            navigate(redirectUrl);
          } else {
            // Regular Google login - store JWT token and redirect to chat
            setSessionToken(null, response.data.email);
            await checkSession(true);
            setStatus('Authentication successful!');
            navigate('/chat');
          }
        } else if (response.data.needs_reactivation) {
          // Show reactivation modal
          setReactivationData({
            userEmail: response.data.user_email,
            userId: response.data.user_id,
            googleId: response.data.google_id
          });
          setShowReactivationModal(true);
          setStatus('Account reactivation required');
        } else {
          setError(response.data.message || 'Authentication failed');
        }
      } catch (error) {
        console.error('Google OAuth callback error:', error);
        setError('Authentication failed. Please try again.');
      }
    };

    // Only run if we have the code parameter and haven't processed yet
    const code = searchParams.get('code');
    if (code && !hasProcessed.current) {
      handleCallback();
    }
  }, [searchParams, navigate]); // Run when searchParams change, but prevent multiple executions

  return (
    <div className="google-callback-container">
      <div className="google-callback-content">
        <div className="google-logo">
          <svg viewBox="0 0 24 24" width="48" height="48">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
          </svg>
        </div>
        
        <h2>Google Authentication</h2>
        
        {error ? (
          <div className="error-message">
            <p>{error}</p>
            <button 
              onClick={() => navigate('/login')}
              className="retry-button"
            >
              Try Again
            </button>
          </div>
        ) : (
          <div className="success-message">
            <p>{status}</p>
            <div className="loading-spinner"></div>
          </div>
        )}
      </div>
      
      {/* Account Reactivation Modal */}
      <AccountReactivationModal
        isOpen={showReactivationModal}
        onClose={() => setShowReactivationModal(false)}
        userEmail={reactivationData?.userEmail}
        userId={reactivationData?.userId}
        googleId={reactivationData?.googleId}
        onReactivationSuccess={() => {
          setShowReactivationModal(false);
          navigate('/chat');
        }}
      />
    </div>
  );
};

export default GoogleOAuthCallback;
