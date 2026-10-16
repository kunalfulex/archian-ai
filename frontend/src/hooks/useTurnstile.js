import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';

const TURNSTILE_SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
export const TURNSTILE_SITE_KEY = process.env.REACT_APP_TURNSTILE_SITE_KEY || '';

let turnstileScriptPromise = null;

function loadTurnstileScript() {
  if (window.turnstile) {
    return Promise.resolve(window.turnstile);
  }

  if (!turnstileScriptPromise) {
    turnstileScriptPromise = new Promise((resolve, reject) => {
      const finish = () => {
        if (window.turnstile) {
          resolve(window.turnstile);
          return true;
        }
        return false;
      };

      if (finish()) return;

      let script = document.querySelector(`script[src="${TURNSTILE_SCRIPT_URL}"]`);
      if (!script) {
        script = document.createElement('script');
        script.src = TURNSTILE_SCRIPT_URL;
        script.async = true;
        script.defer = true;
        script.onload = () => finish();
        script.onerror = () => {
          turnstileScriptPromise = null;
          reject(new Error('Turnstile script failed to load'));
        };
        document.body.appendChild(script);
      } else {
        script.addEventListener('load', () => finish(), { once: true });
      }

      // SPA navigation: script tag may exist but `load` already fired before we subscribed
      const started = Date.now();
      const poll = window.setInterval(() => {
        if (finish()) {
          window.clearInterval(poll);
        } else if (Date.now() - started > 10000) {
          window.clearInterval(poll);
          turnstileScriptPromise = null;
          reject(new Error('Turnstile API not available'));
        }
      }, 50);
    });
  }

  return turnstileScriptPromise;
}

export function useTurnstile() {
  const location = useLocation();
  const containerRef = useRef(null);
  const widgetIdRef = useRef(null);
  const [turnstileToken, setTurnstileToken] = useState('');

  useLayoutEffect(() => {
    if (!TURNSTILE_SITE_KEY) return undefined;

    let cancelled = false;

    const mountWidget = async () => {
      try {
        await loadTurnstileScript();
      } catch {
        return;
      }

      const container = containerRef.current;
      if (cancelled || !container || !window.turnstile) return;

      if (widgetIdRef.current != null) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch {
          /* ignore */
        }
        widgetIdRef.current = null;
      }

      container.replaceChildren();

      widgetIdRef.current = window.turnstile.render(container, {
        sitekey: TURNSTILE_SITE_KEY,
        callback: (token) => {
          if (!cancelled) setTurnstileToken(token);
        },
        'expired-callback': () => {
          if (!cancelled) setTurnstileToken('');
        },
        'error-callback': () => {
          if (!cancelled) setTurnstileToken('');
        },
      });
    };

    mountWidget();

    return () => {
      cancelled = true;
      if (widgetIdRef.current != null && window.turnstile) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch {
          /* ignore */
        }
        widgetIdRef.current = null;
      }
      setTurnstileToken('');
    };
  }, [location.pathname, location.key]);

  const resetTurnstile = useCallback(() => {
    if (widgetIdRef.current != null && window.turnstile) {
      try {
        window.turnstile.reset(widgetIdRef.current);
      } catch {
        /* ignore */
      }
    }
    setTurnstileToken('');
  }, []);

  return {
    turnstileToken,
    containerRef,
    resetTurnstile,
    siteKeyConfigured: Boolean(TURNSTILE_SITE_KEY),
  };
}
