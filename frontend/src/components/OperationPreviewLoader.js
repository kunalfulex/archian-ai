import React from 'react';
import { PHASE_STATUS } from '../architect/operationPhaseRegistry';

function StatusIcon({ status }) {
  if (status === PHASE_STATUS.DONE) {
    return (
      <span style={{ color: '#059669', marginRight: 8, fontWeight: 600 }} aria-hidden>
        ✓
      </span>
    );
  }
  if (status === PHASE_STATUS.ERROR) {
    return (
      <span style={{ color: '#dc2626', marginRight: 8 }} aria-hidden>
        ✕
      </span>
    );
  }
  if (status === PHASE_STATUS.RUNNING) {
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
          animation: 'operation-preview-spin 0.8s linear infinite',
          flexShrink: 0,
        }}
        aria-hidden
      />
    );
  }
  return (
    <span style={{ color: '#9ca3af', marginRight: 8 }} aria-hidden>
      ○
    </span>
  );
}

/**
 * Unified pipeline loader for architect turns (classification → extract → preview).
 */
export default function OperationPreviewLoader({
  preview,
  title = 'Working on your request',
  footerMessage = null,
}) {
  const phases = preview?.phases || [];
  if (!phases.length && !footerMessage) return null;

  return (
    <>
      <style>{`
        @keyframes operation-preview-spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
      <div
        className="operation-preview-loader"
        style={{
          margin: '12px 0',
          padding: '10px 12px',
          borderRadius: 8,
          backgroundColor: 'rgba(99, 102, 241, 0.06)',
          border: '1px solid rgba(99, 102, 241, 0.2)',
          fontSize: 13,
          textAlign: 'left',
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 8, color: '#374151' }}>{title}</div>
        <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          {phases.map((phase) => (
            <li
              key={phase.id}
              style={{
                marginBottom: 6,
                display: 'flex',
                alignItems: 'flex-start',
                color: phase.status === PHASE_STATUS.PENDING ? '#9ca3af' : '#374151',
              }}
            >
              <StatusIcon status={phase.status} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div>{phase.label || phase.id}</div>
                {phase.detail ? (
                  <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>{phase.detail}</div>
                ) : null}
                {phase.status === PHASE_STATUS.RUNNING &&
                phase.progress != null &&
                phase.progress > 0 ? (
                  <div
                    style={{
                      marginTop: 4,
                      height: 4,
                      borderRadius: 2,
                      backgroundColor: 'rgba(99, 102, 241, 0.15)',
                      overflow: 'hidden',
                    }}
                  >
                    <div
                      style={{
                        height: '100%',
                        width: `${Math.min(100, phase.progress)}%`,
                        backgroundColor: '#6366f1',
                        transition: 'width 0.3s ease',
                      }}
                    />
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
        {footerMessage ? (
          <div style={{ marginTop: 8, color: '#6b7280', fontSize: 12 }}>{footerMessage}</div>
        ) : null}
      </div>
    </>
  );
}
