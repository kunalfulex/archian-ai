/**
 * Optional marketing site + support contact for self-host / hosted builds.
 * Set REACT_APP_SITE_ORIGIN and REACT_APP_SUPPORT_EMAIL at build time.
 * API base URL remains REACT_APP_API_URL in api.js.
 */
const trimSlash = (value) => String(value || '').replace(/\/$/, '');

export const SITE_ORIGIN = trimSlash(process.env.REACT_APP_SITE_ORIGIN || '');
export const SUPPORT_EMAIL = (process.env.REACT_APP_SUPPORT_EMAIL || '').trim();

export const productUrl = SITE_ORIGIN ? `${SITE_ORIGIN}/product` : '#';
export const privacyUrl = SITE_ORIGIN ? `${SITE_ORIGIN}/privacy` : '#';
export const termsUrl = SITE_ORIGIN ? `${SITE_ORIGIN}/terms` : '#';
