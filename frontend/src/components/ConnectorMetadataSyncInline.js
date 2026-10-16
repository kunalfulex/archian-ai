import React from 'react';
import {
  DEFAULT_SYNC_FREQUENCY,
  DEFAULT_SYNC_TIME,
  useConnectorSchemaSync,
} from '../hooks/useConnectorSchemaSync';
import './ConnectorMetadataSyncInline.css';

const FREQUENCIES = [
  { value: '', label: 'Off' },
  { value: 'daily', label: 'Daily' },
  { value: 'hourly', label: 'Hourly' },
];

function formatScheduleLabel(freq, timeOfDay, scheduleEnabled) {
  if (!scheduleEnabled || !freq) {
    return `Not enabled (default: Daily at ${DEFAULT_SYNC_TIME})`;
  }
  const timeLabel = timeOfDay || DEFAULT_SYNC_TIME;
  const freqLabel = FREQUENCIES.find((f) => f.value === freq)?.label || freq;
  if (freq === 'hourly') return 'Hourly';
  if (freq === 'daily') return `${freqLabel} at ${timeLabel}`;
  return freqLabel;
}

/**
 * Compact metadata sync UI for connection cards and org settings.
 * @param {'status' | 'schedule' | 'compact-schedule' | 'account-panel'} variant
 */
export default function ConnectorMetadataSyncInline({
  connectorType,
  connectorId,
  variant = 'status',
  inlineRow = false,
}) {
  const sync = useConnectorSchemaSync(connectorType, connectorId);

  if (!connectorId) return null;

  if (sync.unavailable) {
    return (
      <p className="sf-metadata-sync-muted">
        Metadata auto-sync is not available in this environment.
      </p>
    );
  }

  if (variant === 'status') {
    const asOf = sync.graphSummary?.as_of || sync.schedule?.last_success_at;

    const statusRow = (
      <div className="sf-connection-meta-row">
        <span className="sf-connection-meta-label">Metadata sync</span>
        <span className="sf-connection-meta-value">
          {sync.loading ? (
            <span className="sf-metadata-sync-loading">Loading…</span>
          ) : asOf ? (
            <>
              Synced{' '}
              <span className="sf-metadata-sync-asof">
                {new Date(asOf).toLocaleString()}
              </span>
            </>
          ) : (
            'Not synced yet'
          )}
        </span>
      </div>
    );

    if (inlineRow) {
      return (
        <div className="sf-metadata-sync-status sf-metadata-sync-status--inline">
          {statusRow}
          {sync.error && <p className="sf-metadata-sync-error">{sync.error}</p>}
        </div>
      );
    }

    return (
      <div className="sf-metadata-sync-status">
        <div className="sf-metadata-sync-status-grid">{statusRow}</div>
        {sync.error && <p className="sf-metadata-sync-error">{sync.error}</p>}
      </div>
    );
  }

  if (variant === 'account-panel') {
    const needsTime =
      sync.draftFreq && !['hourly', 'every_15_minutes', ''].includes(sync.draftFreq);
    const asOf = sync.graphSummary?.as_of || sync.schedule?.last_success_at;
    const entityNoun = connectorType === 'google_sheets' ? 'tabs' : 'objects';

    return (
      <div className="sf-metadata-sync-account-panel">
        {sync.loading ? (
          <p className="sf-metadata-sync-muted">Loading sync settings…</p>
        ) : (
          <div className="sf-metadata-sync-account-panel-grid">
            <div className="sf-metadata-sync-account-col sf-metadata-sync-account-col--schedule">
              <span className="sf-metadata-sync-col-label">Auto-sync schedule</span>
              <div
                className={`sf-metadata-sync-schedule-row${
                  needsTime ? '' : ' sf-metadata-sync-schedule-row--freq-only'
                }`}
              >
                <label className="sf-metadata-sync-field">
                  <span className="sf-metadata-sync-field-label">Frequency</span>
                  <select
                    className="form-select"
                    value={sync.draftFreq}
                    disabled={sync.savingSchedule}
                    onChange={(e) => sync.setDraftFreq(e.target.value)}
                  >
                    {FREQUENCIES.map((f) => (
                      <option key={f.value || 'off'} value={f.value}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                </label>
                {needsTime ? (
                  <label className="sf-metadata-sync-field">
                    <span className="sf-metadata-sync-field-label">Time of day</span>
                    <input
                      type="time"
                      className="form-input"
                      value={sync.timeOfDay}
                      disabled={sync.savingSchedule}
                      onChange={(e) => sync.setTimeOfDay(e.target.value)}
                    />
                  </label>
                ) : null}
              </div>
              <button
                type="button"
                className="btn btn-secondary sf-metadata-sync-save-schedule-btn"
                disabled={sync.savingSchedule}
                onClick={() => sync.saveSchedule()}
              >
                {sync.savingSchedule ? 'Saving…' : 'Save schedule'}
              </button>
            </div>

            <div className="sf-metadata-sync-account-col sf-metadata-sync-account-col--status">
              <span className="sf-metadata-sync-col-label">Sync status</span>
              <div className="sf-metadata-sync-status-body">
                {asOf ? (
                  <p className="sf-metadata-sync-status-line">
                    As of: {new Date(asOf).toLocaleString()}
                  </p>
                ) : (
                  <p className="sf-metadata-sync-status-line sf-metadata-sync-muted">
                    Not synced yet
                  </p>
                )}
                {sync.nodeCount != null && (
                  <p className="sf-metadata-sync-status-stat">
                    {sync.nodeCount} {entityNoun}
                  </p>
                )}
                {sync.edgeCount != null && (
                  <p className="sf-metadata-sync-status-stat">
                    {sync.edgeCount} relationships
                  </p>
                )}
                {sync.graphSummary?.scope && (
                  <p className="sf-metadata-sync-status-scope">
                    Scope: {sync.graphSummary.scope}
                  </p>
                )}
              </div>
            </div>
          </div>
        )}
        {!sync.scheduleEnabled &&
          !sync.loading &&
          sync.draftFreq === DEFAULT_SYNC_FREQUENCY && (
            <p className="sf-metadata-sync-muted sf-metadata-sync-default-note">
              Default schedule is daily at {DEFAULT_SYNC_TIME}. Save schedule to enable.
            </p>
          )}
        {sync.error && <p className="sf-metadata-sync-error">{sync.error}</p>}
      </div>
    );
  }

  if (variant === 'compact-schedule') {
    const needsTime =
      sync.draftFreq && !['hourly', 'every_15_minutes', ''].includes(sync.draftFreq);

    return (
      <div className="sf-metadata-sync-compact">
        <div className="sf-metadata-sync-compact-row">
          <span className="sf-metadata-sync-compact-label">Auto-sync metadata</span>
          {sync.loading ? (
            <span className="sf-metadata-sync-muted">Loading…</span>
          ) : (
            <div
              className={`sf-metadata-sync-compact-fields${
                needsTime ? '' : ' sf-metadata-sync-compact-fields--freq-only'
              }`}
            >
              <select
                className="form-select sf-metadata-sync-compact-select"
                value={sync.draftFreq}
                disabled={sync.savingSchedule}
                onChange={(e) => sync.setDraftFreq(e.target.value)}
                aria-label="Auto-sync frequency"
              >
                {FREQUENCIES.map((f) => (
                  <option key={f.value || 'off'} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
              {needsTime ? (
                <input
                  type="time"
                  className="form-input sf-metadata-sync-compact-time"
                  value={sync.timeOfDay}
                  disabled={sync.savingSchedule}
                  onChange={(e) => sync.setTimeOfDay(e.target.value)}
                  aria-label="Auto-sync time of day"
                />
              ) : null}
              <button
                type="button"
                className="sf-metadata-sync-compact-save"
                disabled={sync.savingSchedule}
                onClick={() => sync.saveSchedule()}
              >
                {sync.savingSchedule ? 'Saving…' : 'Apply'}
              </button>
            </div>
          )}
        </div>
        {!sync.loading && (
          <p className="sf-metadata-sync-compact-note">
            {sync.draftFreq
              ? formatScheduleLabel(sync.draftFreq, sync.timeOfDay, true)
              : 'Off'}
            {!sync.scheduleEnabled || sync.draftFreq !== sync.schedule?.schedule_frequency
              ? ' · Apply to save'
              : ''}
          </p>
        )}
        {sync.error && <p className="sf-metadata-sync-error">{sync.error}</p>}
      </div>
    );
  }

  if (variant === 'schedule') {
    const needsTime =
      sync.draftFreq && !['hourly', 'every_15_minutes', ''].includes(sync.draftFreq);

    return (
      <div className="sf-metadata-sync-schedule">
        <h4 className="sf-metadata-sync-schedule-title">Auto-sync metadata</h4>
        <p className="sf-metadata-sync-schedule-hint">
          Keeps object relationships up to date. Default is daily at 2:00 AM in your timezone.
        </p>
        {sync.loading ? (
          <p className="sf-metadata-sync-muted">Loading schedule…</p>
        ) : (
          <div className="sf-metadata-sync-schedule-fields">
            <div
              className={`sf-metadata-sync-schedule-row${
                needsTime ? '' : ' sf-metadata-sync-schedule-row--freq-only'
              }`}
            >
              <label className="sf-metadata-sync-field">
                <span className="sf-metadata-sync-field-label">Frequency</span>
                <select
                  className="form-select"
                  value={sync.draftFreq}
                  disabled={sync.savingSchedule}
                  onChange={(e) => sync.setDraftFreq(e.target.value)}
                >
                  {FREQUENCIES.map((f) => (
                    <option key={f.value || 'off'} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </label>
              {needsTime ? (
                <label className="sf-metadata-sync-field">
                  <span className="sf-metadata-sync-field-label">Time of day</span>
                  <input
                    type="time"
                    className="form-input"
                    value={sync.timeOfDay}
                    disabled={sync.savingSchedule}
                    onChange={(e) => sync.setTimeOfDay(e.target.value)}
                  />
                </label>
              ) : null}
              <button
                type="button"
                className="btn btn-secondary sf-metadata-sync-save-btn"
                disabled={sync.savingSchedule}
                onClick={() => sync.saveSchedule()}
              >
                {sync.savingSchedule ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
        {!sync.scheduleEnabled && !sync.loading && sync.draftFreq === DEFAULT_SYNC_FREQUENCY && (
          <p className="sf-metadata-sync-muted sf-metadata-sync-default-note">
            Previewing default ({DEFAULT_SYNC_FREQUENCY} at {DEFAULT_SYNC_TIME}). Click Save to
            enable.
          </p>
        )}
        {sync.error && <p className="sf-metadata-sync-error">{sync.error}</p>}
      </div>
    );
  }

  return null;
}
