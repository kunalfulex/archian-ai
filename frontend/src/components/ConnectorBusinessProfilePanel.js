import React, { useCallback, useEffect, useState } from 'react';
import api from '../api';
import './ConnectorBusinessProfilePanel.css';

/**
 * User-editable connector description + read-only AI business profile summary.
 * Used for SQL and Google Sheets connections (Salesforce uses Org Settings instead).
 */
const ConnectorBusinessProfilePanel = ({
  connectorType,
  connectorInstanceId,
  title = 'About this connection',
}) => {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [userDescription, setUserDescription] = useState('');
  const [aiProfile, setAiProfile] = useState(null);
  const [profileStatus, setProfileStatus] = useState('pending');
  const [entityStats, setEntityStats] = useState(null);
  const [error, setError] = useState('');

  const loadProfile = useCallback(async () => {
    if (!connectorType || !connectorInstanceId) return;
    setLoading(true);
    setError('');
    try {
      const { data } = await api.get(
        `/api/labeling/connectors/${connectorType}/${connectorInstanceId}/profile`
      );
      setUserDescription(data.user_description || '');
      setAiProfile(data.ai_profile || null);
      setProfileStatus(data.profile_status || 'pending');
      setEntityStats(data.entity_labeling_stats || null);
    } catch (e) {
      setError('Could not load connection profile.');
    } finally {
      setLoading(false);
    }
  }, [connectorType, connectorInstanceId]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const handleSave = async () => {
    setSaving(true);
    setError('');
    try {
      await api.patch(
        `/api/labeling/connectors/${connectorType}/${connectorInstanceId}/description`,
        { user_description: userDescription }
      );
      await loadProfile();
    } catch (e) {
      setError('Could not save description.');
    } finally {
      setSaving(false);
    }
  };

  if (!connectorType || !connectorInstanceId) return null;

  return (
    <div className="connector-business-profile-panel">
      <h4 className="cbp-title">{title}</h4>
      {loading ? (
        <p className="cbp-muted">Loading…</p>
      ) : (
        <>
          <label className="cbp-label" htmlFor="connector-user-description">
            Your description{' '}
            <span className="cbp-hint">(optional — helps Archian AI understand this system)</span>
          </label>
          <textarea
            id="connector-user-description"
            className="cbp-textarea"
            rows={3}
            value={userDescription}
            onChange={(e) => setUserDescription(e.target.value)}
            placeholder="e.g. Production field service org for UK operations"
          />
          <button
            type="button"
            className="cbp-save-btn"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? 'Saving…' : 'Save description'}
          </button>

          {aiProfile?.synopsis && (
            <div className="cbp-ai-block">
              <div className="cbp-ai-label">What Archian AI learned</div>
              <p className="cbp-ai-synopsis">{aiProfile.synopsis}</p>
              {Array.isArray(aiProfile.business_domains) && aiProfile.business_domains.length > 0 && (
                <div className="cbp-tags">
                  {aiProfile.business_domains.map((d) => (
                    <span key={d} className="cbp-tag">{d}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {!aiProfile?.synopsis && profileStatus !== 'ready' && (
            <p className="cbp-muted">
              Archian AI is learning about this connection in the background
              {entityStats?.total > 0
                ? ` (${entityStats.ready || 0}/${entityStats.total} entities read).`
                : '.'}
            </p>
          )}

          {error && <p className="cbp-error">{error}</p>}
        </>
      )}
    </div>
  );
};

export default ConnectorBusinessProfilePanel;
