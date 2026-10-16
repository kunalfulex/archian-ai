import { createContext, useContext, useEffect, useState } from 'react';
import api from '../api';

// Used when /features is unreachable (e.g. an older backend), so the cloud UI keeps working.
const FALLBACK_FEATURES = { edition: 'cloud', billing: true };

const FeaturesContext = createContext({ ...FALLBACK_FEATURES, loading: false });

let featuresPromise = null;

export const fetchFeatures = () => {
  if (!featuresPromise) {
    featuresPromise = Promise.resolve()
      .then(() => api.get('/features'))
      .then((res) => ({
        edition: res.data?.edition || FALLBACK_FEATURES.edition,
        billing: res.data?.billing !== false,
      }))
      .catch((err) => {
        console.warn('Could not load /features, assuming billing is enabled:', err);
        featuresPromise = null;
        return FALLBACK_FEATURES;
      });
  }
  return featuresPromise;
};

export const resetFeaturesCache = () => {
  featuresPromise = null;
};

export const FeaturesProvider = ({ children }) => {
  const [state, setState] = useState({ ...FALLBACK_FEATURES, loading: true });

  useEffect(() => {
    let cancelled = false;
    fetchFeatures().then((features) => {
      if (!cancelled) setState({ ...features, loading: false });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return <FeaturesContext.Provider value={state}>{children}</FeaturesContext.Provider>;
};

export const useFeatures = () => useContext(FeaturesContext);
