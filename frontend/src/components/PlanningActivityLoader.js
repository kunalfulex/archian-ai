import React from 'react';
import {
  PLANNING_ACTIVITY_STATUS,
  displayStatusForPlanningItem,
  enforceSingleRunningSpinner,
} from '../architect/reducePlanningActivity';

function StatusIcon({ status }) {
  if (status === PLANNING_ACTIVITY_STATUS.DONE) {
    return (
      <span style={{ color: '#059669', marginRight: 8, fontWeight: 600 }} aria-hidden>
        ✓
      </span>
    );
  }
  if (status === PLANNING_ACTIVITY_STATUS.ERROR || status === PLANNING_ACTIVITY_STATUS.FAILED) {
    return (
      <span style={{ color: '#dc2626', marginRight: 8 }} aria-hidden>
        ✕
      </span>
    );
  }
  if (status === PLANNING_ACTIVITY_STATUS.RUNNING) {
    return (
      <span
        style={{
          display: 'inline-block',
          width: 12,
          height: 12,
          marginRight: 8,
          border: '2px solid rgba(99, 102, 241, 0.25)',
          borderTopColor: '#6366f1',
          borderRadius: '50%',
          animation: 'planning-activity-spin 0.8s linear infinite',
          flexShrink: 0,
        }}
        aria-hidden
      />
    );
  }
  return <span style={{ width: 20, marginRight: 8, flexShrink: 0 }} aria-hidden />;
}

/**
 * Append-only planning loader — milestones appear as scope/plan is discovered.
 */
export default function PlanningActivityLoader({ preview, footerMessage = null }) {
  const items = enforceSingleRunningSpinner(preview?.items || []);
  if (!items.length && !footerMessage) return null;

  return (
    <>
      <style>{`
        @keyframes planning-activity-spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
      <div
        className="planning-activity-loader"
        style={{
          marginTop: 8,
          padding: '10px 12px',
          borderRadius: 8,
          backgroundColor: 'rgba(99, 102, 241, 0.06)',
          border: '1px solid rgba(99, 102, 241, 0.12)',
          fontSize: 14,
        }}
      >
        <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          {items.map((item) => {
            const displayStatus = displayStatusForPlanningItem(items, item);
            return (
              <li
                key={item.activityId}
                style={{
                  marginBottom: 6,
                  display: 'flex',
                  alignItems: 'flex-start',
                  color:
                    displayStatus === PLANNING_ACTIVITY_STATUS.RUNNING
                      ? '#374151'
                      : '#4b5563',
                }}
              >
                <StatusIcon status={displayStatus} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div>{item.label}</div>
                  {item.detail ? (
                    <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>
                      {item.detail}
                    </div>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
        {footerMessage ? (
          <div style={{ marginTop: 8, color: '#6b7280', fontSize: 12 }}>{footerMessage}</div>
        ) : null}
      </div>
    </>
  );
}
