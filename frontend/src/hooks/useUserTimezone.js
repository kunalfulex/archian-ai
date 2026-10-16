import { useState, useEffect, useContext, createContext, useRef } from 'react';
import api from '../api';
import { checkSession } from '../utils/authStorage';

// Create a context for user timezone
const UserTimezoneContext = createContext();

// Custom hook to use user timezone
export const useUserTimezone = () => {
  const context = useContext(UserTimezoneContext);
  if (!context) {
    throw new Error('useUserTimezone must be used within a UserTimezoneProvider');
  }
  return context;
};

// Provider component for user timezone
export const UserTimezoneProvider = ({ children }) => {
  const [userTimezone, setUserTimezone] = useState('America/Los_Angeles|Los Angeles, USA'); // Default timezone
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const hasFetched = useRef(false); // Track if we've already fetched the timezone
  const isFetching = useRef(false); // Track if a fetch is currently in progress
  

  // Fetch user timezone from profile
  const fetchUserTimezone = async () => {
    // Check if user is logged in before making API calls
    const loggedIn = await checkSession(false);
    if (!loggedIn) {
      setUserTimezone('America/Los_Angeles|Los Angeles, USA'); // Use default
      hasFetched.current = true;
      setLoading(false);
      return;
    }

    // Prevent multiple simultaneous calls using ref
    if (isFetching.current) {
      return;
    }
    
    // Prevent multiple calls if already fetched
    if (hasFetched.current) {
      return;
    }
    
    try {
      isFetching.current = true;
      setLoading(true);
      setError(null);
      
      const response = await api.get('/profile');
      
      if (response.data.status === 'success') {
        const profile = response.data.data;
        const timezone = profile.timezone || 'America/Los_Angeles|Los Angeles, USA';
        setUserTimezone(timezone);
        hasFetched.current = true;
      } else {
        console.warn('Failed to load user profile, using default timezone');
        setUserTimezone('UTC-8 (Pacific Time)');
        hasFetched.current = true;
      }
    } catch (err) {
      console.warn('Error fetching user timezone, using default:', err);
      setError('Failed to load user timezone');
      setUserTimezone('UTC-8 (Pacific Time)'); // Fallback to default
      hasFetched.current = true;
    } finally {
      setLoading(false);
      isFetching.current = false;
    }
  };

  // Update user timezone
  const updateUserTimezone = async (newTimezone) => {
    try {
      setLoading(true);
      setError(null);
      
      const response = await api.put('/profile', {
        timezone: newTimezone
      });
      
      if (response.data.status === 'success') {
        setUserTimezone(newTimezone);
        return true;
      } else {
        setError('Failed to update timezone');
        return false;
      }
    } catch (err) {
      console.error('Error updating user timezone:', err);
      setError('Failed to update timezone');
      return false;
    } finally {
      setLoading(false);
    }
  };

  // Refresh timezone data
  const refreshTimezone = () => {
    hasFetched.current = false; // Reset the fetched flag
    fetchUserTimezone();
  };

  // Reset timezone data (for logout)
  const resetTimezone = () => {
    setUserTimezone('UTC-8 (Pacific Time)');
    hasFetched.current = false;
    isFetching.current = false;
    setError(null);
  };

  // Load user timezone on mount
  useEffect(() => {
    // Only fetch once on mount
    if (!hasFetched.current && !isFetching.current) {
      fetchUserTimezone();
    }
  }, []); // Empty dependency array - only run on mount

  // Listen for storage changes (logout events)
  useEffect(() => {
    const handleStorageChange = (e) => {
      if (e.key === 'token' && (!e.newValue || e.newValue === 'null' || e.newValue === 'undefined')) {
        resetTimezone();
      }
    };

    // Listen for storage events (from other tabs)
    window.addEventListener('storage', handleStorageChange);

    // Also listen for custom logout events
    const handleLogout = () => {
      resetTimezone();
    };

    window.addEventListener('logout', handleLogout);

    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('logout', handleLogout);
    };
  }, []);

  const value = {
    userTimezone,
    loading,
    error,
    updateUserTimezone,
    refreshTimezone,
    resetTimezone
  };

  return (
    <UserTimezoneContext.Provider value={value}>
      {children}
    </UserTimezoneContext.Provider>
  );
};

// Export the context for direct usage if needed
export { UserTimezoneContext };
