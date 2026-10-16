import React from 'react';
import { FiPlayCircle } from 'react-icons/fi';
import { productUrl } from '../config/site';

export const rotatingExamples = [
  "How should I import my customer data into Salesforce?",
  "Where do I store people contacts in Salesforce?",
  "How do I import a list of company accounts info?",
  "What's the right object for storing Sales prospect data?"
];

export default function ChatWelcomeSection({ isMobile, exampleIndex, hasSalesforceConnection }) {
  return (
    <div className={`welcome-section ${isMobile ? 'mobile' : ''}`}>
      <h2 className={`welcome-title ${isMobile ? 'mobile' : ''}`}>
        welcome to archian ai
      </h2>

      <p className={`welcome-description ${isMobile ? 'mobile' : ''}`}>
        archian ai helps you build and run data pipelines.
        Just upload a document or connect your application and describe your data — archian ai will extract, transform, map, and migrate everything for you.
      </p>

      <div style={{ marginTop: '16px', marginBottom: '16px' }}>
        <a 
          href={productUrl} 
          target="_blank" 
          rel="noopener noreferrer"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            color: '#6366f1',
            textDecoration: 'none',
            fontSize: '14px',
            fontWeight: '500',
            padding: '8px 16px',
            border: '1px solid #6366f1',
            borderRadius: '6px',
            transition: 'all 0.2s ease',
            backgroundColor: 'transparent'
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = '#6366f1';
            e.currentTarget.style.color = '#ffffff';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'transparent';
            e.currentTarget.style.color = '#6366f1';
          }}
        >
          <FiPlayCircle size={18} />
          <span>Watch Product Demo</span>
        </a>
      </div>

      <div className="try-asking-container">
        <p className="try-asking-label">
          We work with:
        </p>
        <p className="try-asking-example">
          Files (PDF, CSV, XLSX, TXT, Images), Google Sheets, Salesforce, Databases (SQL, PostgreSQL)
        </p>
      </div>

      <div className="try-asking-container">
        <p className="try-asking-label">
          Try asking:
        </p>
        <p className="try-asking-example">
          {rotatingExamples[exampleIndex]}
        </p>
      </div>

      {/* Subtle connection hint - only show if no Salesforce connection */}
      {!hasSalesforceConnection && (
        <div className="connection-hint-container">
          <span className="connection-hint-text">For personalized guidance, connect your Salesforce org in the</span> 
          <button
            onClick={() => window.location.href = '/connections'}
            className="connection-hint-button"
          >
            Connections tab
          </button>
        </div>
      )}


    </div>
  );
}
