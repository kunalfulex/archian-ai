import React from 'react';
import { FiCheckCircle, FiRefreshCw } from 'react-icons/fi';
import {
  readinessInProgressMessage,
  readinessCompleteMessage,
  readinessSetupCta,
} from '../connector/connectorReadinessSse';

export default function SyncNotificationBar({ syncBanners, setSyncBanners }) {
  return (
    <div className={`sync-notification-bar ${syncBanners.some(b => b.isComplete) ? 'sync-complete' : ''}`}>
      <span className="sync-notification-icon">
        {syncBanners.some(b => b.isComplete) ? (
          <FiCheckCircle style={{ fontSize: '18px' }} />
        ) : (
          <FiRefreshCw className="sync-spinning" style={{ fontSize: '18px' }} />
        )}
      </span>
      <div className="sync-notification-content">
        {syncBanners.map((banner, idx) => (
          <div key={idx} style={{ marginBottom: idx < syncBanners.length - 1 ? '8px' : '0' }}>
            {banner.isComplete ? (
              <>
                {readinessCompleteMessage({
                  trigger: banner.trigger,
                  name: banner.name,
                  connectorType: banner.connectorType || banner.type,
                })}
                {(() => {
                  const setupCta = readinessSetupCta({
                    trigger: banner.trigger,
                    needsOrgName: banner.needsOrgName,
                    needsOrgDescription: banner.needsOrgDescription,
                  });
                  if (!setupCta.show || !banner.setupConnectionsPath) return null;
                  return (
                    <div style={{ marginTop: '8px' }}>
                      <a
                        href={banner.setupConnectionsPath}
                        className="sync-notification-cta"
                      >
                        {setupCta.label}
                      </a>
                    </div>
                  );
                })()}
              </>
            ) : (
              `${readinessInProgressMessage({
                trigger: banner.trigger,
                phase: banner.phase || 'metadata_sync',
                name: banner.name,
                stats: banner.entityStats,
                metadataProgress: banner.metadataSyncProgress,
              })}. We'll notify you when your agent is ready.`
            )}
          </div>
        ))}
      </div>
      <button
        onClick={() => {
          setSyncBanners([]);
          sessionStorage.removeItem('pending_sync');
        }}
        className="sync-notification-close"
        aria-label="Dismiss sync notification"
      >
        ×
      </button>
    </div>
  );
}
