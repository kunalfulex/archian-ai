import React, { useState } from 'react';
import SalesforceOrgSchemaPanel from './SalesforceOrgSchemaPanel';
import ConnectorAssessmentPanel from './ConnectorAssessmentPanel';
import ConnectorSchemaPanel from './ConnectorSchemaPanel';
import './SalesforceOrgConnectorTabs.css';

/**
 * Tabbed data assessment, schema graph, and object list for a single Salesforce connection.
 * connectorId = salesforce_tokens.id (token UUID); orgId retained for schema APIs that accept either.
 */
export default function SalesforceOrgConnectorTabs({
  connectorId,
  orgId,
  orgDisplayName,
  showOrgInTitle,
}) {
  const effectiveConnectorId = connectorId || orgId;
  const schemaRef = orgId || connectorId;
  const [activeTab, setActiveTab] = useState('assessment');
  const [assessmentActivity, setAssessmentActivity] = useState(null);

  const runningAssessment =
    assessmentActivity &&
    ['queued', 'running', 'paused'].includes(assessmentActivity.status);
  const assessmentPct = assessmentActivity?.progress?.percent;

  const sectionTitle =
    showOrgInTitle && orgDisplayName
      ? `Salesforce Org Health — ${orgDisplayName}`
      : 'Salesforce Org Health';

  return (
    <div className="sf-org-tabs">
      <h3 className="sf-org-tabs-section-title">{sectionTitle}</h3>
      <div className="sf-org-tabs-bar" role="tablist" aria-label="Salesforce org health views">
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
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'objects'}
          className={`sf-org-tab-btn ${activeTab === 'objects' ? 'active' : ''}`}
          onClick={() => setActiveTab('objects')}
        >
          Object list
        </button>
      </div>
      <div
        className={`sf-org-tabs-panel${activeTab !== 'assessment' ? ' sf-org-tabs-panel--hidden' : ''}`}
        role="tabpanel"
        hidden={activeTab !== 'assessment'}
      >
        <ConnectorAssessmentPanel
          connectorType="salesforce"
          connectorId={effectiveConnectorId}
          connectorDisplayName={orgDisplayName}
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
          connectorType="salesforce"
          connectorId={effectiveConnectorId}
          embedded
          showSyncControls={false}
          showManualSync
          showScheduleAndStatus={false}
        />
      </div>
      <div
        className={`sf-org-tabs-panel${activeTab !== 'objects' ? ' sf-org-tabs-panel--hidden' : ''}`}
        role="tabpanel"
        hidden={activeTab !== 'objects'}
      >
        <SalesforceOrgSchemaPanel orgId={schemaRef} />
      </div>
    </div>
  );
}
