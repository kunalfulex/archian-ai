// Cookie-based auth helpers (session JWT is httpOnly — not readable from JS).

const USER_EMAIL_KEY = 'user_email';
const LEGACY_SESSION_KEY = 'token';
const LEGACY_PRE_MFA_KEY = 'temp_token';

const API_BASE = process.env.REACT_APP_API_URL || '';

let sessionCache = {
  valid: false,
  email: null,
  userId: null,
  checkedAt: 0,
  negativeCheckedAt: 0,
};

/** CSRF cookie is set with the session; skip probe when neither it nor legacy token exists. */
export function hasLikelySession() {
  return Boolean(getCsrfToken()) || Boolean(localStorage.getItem(LEGACY_SESSION_KEY));
}

export function getCsrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)edison_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/** Headers for fetch() calls (includes CSRF on mutating requests). */
export function buildAuthHeaders(extra = {}, method = 'GET') {
  const headers = { ...extra };
  const mutating = !['GET', 'HEAD', 'OPTIONS'].includes(String(method).toUpperCase());
  if (mutating) {
    const csrf = getCsrfToken();
    if (csrf) {
      headers['X-CSRF-Token'] = csrf;
    }
  }
  return headers;
}

function clearLegacyTokenStorage() {
  localStorage.removeItem(LEGACY_SESSION_KEY);
  localStorage.removeItem(LEGACY_PRE_MFA_KEY);
  sessionStorage.removeItem(LEGACY_PRE_MFA_KEY);
}

export function invalidateSessionCache() {
  sessionCache = {
    valid: false,
    email: null,
    userId: null,
    checkedAt: 0,
    negativeCheckedAt: 0,
  };
}

export async function checkSession(force = false) {
  if (!force && sessionCache.valid && Date.now() - sessionCache.checkedAt < 30000) {
    return true;
  }
  if (
    !force &&
    !sessionCache.valid &&
    sessionCache.negativeCheckedAt &&
    Date.now() - sessionCache.negativeCheckedAt < 5000
  ) {
    return false;
  }
  if (!force && !hasLikelySession()) {
    invalidateSessionCache();
    return false;
  }
  try {
    const response = await fetch(`${API_BASE}/api/verify-token`, {
      method: 'GET',
      credentials: 'include',
    });
    if (!response.ok) {
      invalidateSessionCache();
      sessionCache.negativeCheckedAt = Date.now();
      return false;
    }
    const data = await response.json();
    if (!data.authenticated) {
      invalidateSessionCache();
      sessionCache.negativeCheckedAt = Date.now();
      return false;
    }
    sessionCache = {
      valid: true,
      email: data.email || null,
      userId: data.user_id || null,
      checkedAt: Date.now(),
      negativeCheckedAt: 0,
    };
    if (data.email) {
      localStorage.setItem(USER_EMAIL_KEY, data.email);
    }
    clearLegacyTokenStorage();
    return true;
  } catch {
    invalidateSessionCache();
    sessionCache.negativeCheckedAt = Date.now();
    return false;
  }
}

export async function hasValidSession() {
  return checkSession(false);
}

export function getCachedUserEmail() {
  return sessionCache.email || localStorage.getItem(USER_EMAIL_KEY);
}

export function setUserEmail(email) {
  if (email) {
    localStorage.setItem(USER_EMAIL_KEY, email);
    sessionCache.email = email;
  }
}

export async function clearAuthTokens() {
  invalidateSessionCache();
  clearLegacyTokenStorage();
  localStorage.removeItem(USER_EMAIL_KEY);
  try {
    await fetch(`${API_BASE}/api/logout`, {
      method: 'POST',
      credentials: 'include',
      headers: buildAuthHeaders({ 'Content-Type': 'application/json' }, 'POST'),
    });
  } catch {
    // Best-effort cookie clear
  }
}

/** @deprecated Session JWT is httpOnly; use checkSession() instead. */
export function getSessionToken() {
  return null;
}

/** Pre-MFA is httpOnly; presence is inferred from successful login redirect. */
export function getPreMfaToken() {
  return null;
}

export function setPreMfaToken() {
  clearLegacyTokenStorage();
}

export function setSessionToken(_token, email) {
  clearLegacyTokenStorage();
  if (email) {
    setUserEmail(email);
  }
  invalidateSessionCache();
}

export function clearSessionToken() {
  invalidateSessionCache();
}

export function clearPreMfaToken() {
  clearLegacyTokenStorage();
}

export function isTokenExpired() {
  return true;
}

export { USER_EMAIL_KEY };
