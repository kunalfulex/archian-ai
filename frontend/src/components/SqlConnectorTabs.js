import React, { useState } from 'react';
import ConnectorAssessmentPanel from './ConnectorAssessmentPanel';
import ConnectorSchemaPanel from './ConnectorSchemaPanel';
import './SalesforceOrgConnectorTabs.css';

/**
 * Data assessment + schema graph for a SQL database connector (full-width tabs).
 */
export default function SqlConnectorTabs({ connectorId, connectorName, tableNames = [] }) {
  const [activeTab, setActiveTab] = useState('assessment');
  const [assessmentActivity, setAssessmentActivity] = useState(null);

  const runningAssessment =
    assessmentActivity &&
    ['queued', 'running', 'paused'].includes(assessmentActivity.status);
  const assessmentPct = assessmentActivity?.progress?.percent;

  const sectionTitle = connectorName
    ? `SQL Connection Health — ${connectorName}`
    : 'SQL Connection Health';

  return (
    <div className="sf-org-tabs">
      <h3 className="sf-org-tabs-section-title">{sectionTitle}</h3>
      <div className="sf-org-tabs-bar" role="tablist" aria-label="SQL connection health views">
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
          connectorType="sql"
          connectorId={connectorId}
          connectorDisplayName={connectorName}
          embedded
          availableEntities={tableNames}
          onJobActivityChange={setAssessmentActivity}
        />
      </div>
      <div
        className={`sf-org-tabs-panel${activeTab !== 'schema' ? ' sf-org-tabs-panel--hidden' : ''}`}
        role="tabpanel"
        hidden={activeTab !== 'schema'}
      >
        <ConnectorSchemaPanel
          connectorType="sql"
          connectorId={connectorId}
          embedded
          showSyncControls={false}
          showManualSync
          showScheduleAndStatus={false}
        />
      </div>
    </div>
  );
}
