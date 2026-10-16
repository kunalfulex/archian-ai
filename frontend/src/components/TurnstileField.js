import React from 'react';

const TurnstileField = ({ containerRef, siteKeyConfigured }) => {
  if (siteKeyConfigured) {
    return (
      <div
        className="turnstile-container"
        ref={containerRef}
        aria-label="Verification"
      />
    );
  }

  if (process.env.NODE_ENV === 'development') {
    return (
      <p className="turnstile-dev-hint" style={{ fontSize: 12, color: '#6b7280', margin: '8px 0' }}>
        Turnstile disabled locally — set REACT_APP_TURNSTILE_SITE_KEY in .env and restart the dev server.
      </p>
    );
  }

  return null;
};

export default TurnstileField;
