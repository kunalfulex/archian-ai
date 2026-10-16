// src/api.js

import axios from 'axios';
import {
  buildAuthHeaders,
  clearAuthTokens,
  invalidateSessionCache,
} from './utils/authStorage';

/** Cancel in-flight migration and extraction jobs. Fire-and-forget. */
export const cancelJobs = async (jobIds = []) => {
  if (!jobIds?.length) return;
  try {
    await api.post('/edison/cancel-jobs', { job_ids: [...jobIds] });
  } catch (e) {
    console.warn('Cancel jobs request failed:', e);
  }
};

const api = axios.create({
  baseURL: process.env.REACT_APP_API_URL,
  withCredentials: true,
});

api.interceptors.request.use(
  (config) => {
    const method = (config.method || 'get').toUpperCase();
    const extra = buildAuthHeaders(config.headers || {}, method);
    config.headers = { ...config.headers, ...extra };
    return config;
  },
  (error) => Promise.reject(error)
);

api.interceptors.response.use(
  (response) => {
    if (response?.data?.authenticated) {
      invalidateSessionCache();
    }
    return response;
  },
  (error) => {
    if (error.response?.status === 401) {
      clearAuthTokens();

      window.dispatchEvent(new CustomEvent('logout'));

      const showSessionExpiredMessage = () => {
        const alertDiv = document.createElement('div');
        alertDiv.style.cssText = `
          position: fixed;
          top: 20px;
          left: 50%;
          transform: translateX(-50%);
          background: #fef2f2;
          border: 1px solid #fecaca;
          color: #dc2626;
          padding: 16px 24px;
          border-radius: 8px;
          box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
          z-index: 9999;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
          font-size: 14px;
          max-width: 400px;
          text-align: center;
        `;
        alertDiv.innerHTML = `
          <div style="display: flex; align-items: center; gap: 8px;">
            <span style="font-size: 16px;">⚠️</span>
            <span>Your session has expired. Please log in again.</span>
          </div>
        `;
        document.body.appendChild(alertDiv);
        setTimeout(() => {
          if (alertDiv.parentNode) {
            alertDiv.parentNode.removeChild(alertDiv);
          }
        }, 3000);
      };

      showSessionExpiredMessage();
      setTimeout(() => {
        window.location.href = '/login';
      }, 1000);

      console.warn('Session expired. Redirecting to login page.');
    }

    if (error.response?.status === 403 && error.response?.data?.detail?.includes('CSRF')) {
      console.warn('CSRF validation failed — refresh the page and try again.');
    }

    return Promise.reject(error);
  }
);

export default api;
