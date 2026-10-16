import React, { useState } from 'react';
import ConnectorAssessmentPanel from './ConnectorAssessmentPanel';
import ConnectorSchemaPanel from './ConnectorSchemaPanel';
import './SalesforceOrgConnectorTabs.css';

/**
 * Data assessment + schema graph for a Google Sheets connection (full-width tabs).
 */
export default function GoogleSheetsConnectorTabs({ connectionId, accountEmail }) {
  const [activeTab, setActiveTab] = useState('assessment');
  const [assessmentActivity, setAssessmentActivity] = useState(null);

  const runningAssessment =
    assessmentActivity &&
    ['queued', 'running', 'paused'].includes(assessmentActivity.status);
  const assessmentPct = assessmentActivity?.progress?.percent;

  const sectionTitle = accountEmail
    ? `Google Sheets Health — ${accountEmail}`
    : 'Google Sheets Health';

  return (
    <div className="sf-org-tabs">
      <h3 className="sf-org-tabs-section-title">{sectionTitle}</h3>
      <div className="sf-org-tabs-bar" role="tablist" aria-label="Google Sheets health views">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'assessment'}
          className={`sf-org-tab-btn ${activeTab === 'assessment' ? 'active' : ''}`}
          onClick={() => setActiveTab('assessment')}
        >
          Data assessment
          {runningAssessment && (
            <span className="sf-org-tab-badge">
              {typeof assessmentPct === 'number' ? `${assessmentPct}%` : '…'}
            </span>
          )}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'schema'}
          className={`sf-org-tab-btn ${activeTab === 'schema' ? 'active' : ''}`}
          onClick={() => setActiveTab('schema')}
        >
          Schema graph
        </button>
      </div>
      <div
        className={`sf-org-tabs-panel${activeTab !== 'assessment' ? ' sf-org-tabs-panel--hidden' : ''}`}
        role="tabpanel"
        hidden={activeTab !== 'assessment'}
      >
        <ConnectorAssessmentPanel
          connectorType="google_sheets"
          connectorId={connectionId}
          connectorDisplayName={accountEmail}
          embedded
          onJobActivityChange={setAssessmentActivity}
        />
      </div>
      <div
        className={`sf-org-tabs-panel${activeTab !== 'schema' ? ' sf-org-tabs-panel--hidden' : ''}`}
        role="tabpanel"
        hidden={activeTab !== 'schema'}
      >
        <ConnectorSchemaPanel
          connectorType="google_sheets"
          connectorId={connectionId}
          embedded
          showSyncControls={false}
          showManualSync
          showScheduleAndStatus={false}
        />
      </div>
    </div>
  );
}
