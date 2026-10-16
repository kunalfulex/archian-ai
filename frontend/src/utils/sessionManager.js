// src/utils/sessionManager.js

import { checkSession } from './authStorage';

export async function checkSessionAndWarn() {
  const valid = await checkSession(true);
  return valid;
}

export function setupSessionMonitoring() {
  checkSessionAndWarn();

  const interval = setInterval(async () => {
    const isValid = await checkSession(true);
    if (!isValid) {
      clearInterval(interval);
    }
  }, 60000);

  return interval;
}

/** Legacy export — expiry is enforced server-side on httpOnly cookies. */
export function isTokenExpired() {
  return false;
}

export function getTokenExpirationTime() {
  return null;
}

export function getMinutesUntilExpiry() {
  return 60;
}

export function showSessionWarning(minutesLeft) {
  if (document.getElementById('session-warning')) {
    return;
  }

  const warningDiv = document.createElement('div');
  warningDiv.id = 'session-warning';
  warningDiv.style.cssText = `
    position: fixed;
    top: 20px;
    right: 20px;
    background: #fef3c7;
    border: 1px solid #f59e0b;
    color: #92400e;
    padding: 16px 24px;
    border-radius: 8px;
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
    z-index: 9998;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
    font-size: 14px;
    max-width: 350px;
  `;
  warningDiv.innerHTML = `
    <div style="font-weight: 600;">Session expiring soon</div>
    <div>Please save your work (${minutesLeft} min).</div>
  `;
  document.body.appendChild(warningDiv);
  setTimeout(() => warningDiv.remove(), 10000);
}
