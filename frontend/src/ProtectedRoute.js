// src/ProtectedRoute.js
import React, { useEffect, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { checkSession, clearAuthTokens } from './utils/authStorage';
import { setupSessionMonitoring } from './utils/sessionManager';

const ProtectedRoute = ({ children }) => {
  const location = useLocation();
  const [isAuthenticated, setIsAuthenticated] = useState(null);

  useEffect(() => {
    let sessionInterval = null;
    let authInterval = null;
    let cancelled = false;

    const runCheck = async () => {
      const valid = await checkSession(true);
      if (cancelled) return valid;
      if (!valid) {
        await clearAuthTokens();
        setIsAuthenticated(false);
        return false;
      }
      setIsAuthenticated(true);
      return true;
    };

    (async () => {
      const valid = await runCheck();
      if (cancelled || !valid) return;
      sessionInterval = setupSessionMonitoring();
      authInterval = setInterval(async () => {
        const stillValid = await runCheck();
        if (!stillValid && sessionInterval) {
          clearInterval(sessionInterval);
        }
      }, 30000);
    })();

    return () => {
      cancelled = true;
      if (authInterval) clearInterval(authInterval);
      if (sessionInterval) clearInterval(sessionInterval);
    };
  }, []);

  if (isAuthenticated === null) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        fontSize: '16px',
      }}>
        Checking authentication...
      </div>
    );
  }

  if (!isAuthenticated) {
    const next = location.pathname + location.search;
    return <Navigate to={next ? `/login?next=${encodeURIComponent(next)}` : '/login'} replace />;
  }

  return children;
};

export default ProtectedRoute;
