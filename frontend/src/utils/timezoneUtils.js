/**
 * Frontend timezone utility functions for Archian AI
 * Converts UTC timestamps to user's local timezone for display
 * Supports IANA timezones with DST and legacy UTC offset format
 */

/**
 * Parse timezone string and return IANA timezone and display name.
 * Supports two formats:
 * 1. IANA format: "America/Los_Angeles|Los Angeles, USA" -> {iana: "America/Los_Angeles", display: "Los Angeles, USA"}
 * 2. Legacy UTC offset format: "UTC-8 (Pacific Time)" -> {iana: null, display: "UTC-8 (Pacific Time)"}
 * 
 * @param {string} timezoneStr - Timezone string from user profile
 * @returns {object} Object with iana and display properties
 */
export const parseTimezoneString = (timezoneStr) => {
  try {
    if (!timezoneStr) {
      return { iana: 'America/Los_Angeles', display: 'Los Angeles, USA', offset: null };
    }
    
    // Check if it's IANA format (contains | separator)
    if (timezoneStr.includes('|')) {
      const parts = timezoneStr.split('|');
      const iana = parts[0].trim();
      const display = parts[1].trim() || iana;
      return { iana, display, offset: null }; // Offset will be calculated dynamically
    }
    
    // Legacy UTC offset format
    if (timezoneStr.includes('UTC+') || timezoneStr.includes('UTC-')) {
      const offset = parseTimezoneOffset(timezoneStr);
      return { iana: null, display: timezoneStr, offset };
    }
    
    // If it's just an IANA timezone without display name
    return { iana: timezoneStr, display: timezoneStr, offset: null };
  } catch (error) {
    console.warn('Failed to parse timezone string, using default:', error);
    return { iana: 'America/Los_Angeles', display: 'Los Angeles, USA', offset: null };
  }
};

/**
 * Parse timezone string like "UTC-8 (Pacific Time)" and return offset in hours (legacy support)
 * @param {string} timezoneStr - Timezone string from user profile
 * @returns {number} UTC offset in hours (e.g., -8 for UTC-8, +5.5 for UTC+5:30)
 */
export const parseTimezoneOffset = (timezoneStr) => {
  try {
    if (!timezoneStr) return -8; // Default to Pacific Time
    
    // Extract UTC offset from string like "UTC-8 (Pacific Time)" or "UTC+5:30 (India)"
    if (timezoneStr.includes('UTC+')) {
      const offsetStr = timezoneStr.split('UTC+')[1].split(' ')[0];
      // Handle offsets with minutes like "5:30" -> 5.5 hours
      if (offsetStr.includes(':')) {
        const [hours, minutes] = offsetStr.split(':').map(Number);
        return hours + (minutes / 60.0);
      } else {
        return parseFloat(offsetStr);
      }
    } else if (timezoneStr.includes('UTC-')) {
      const offsetStr = timezoneStr.split('UTC-')[1].split(' ')[0];
      // Handle offsets with minutes like "5:30" -> -5.5 hours
      if (offsetStr.includes(':')) {
        const [hours, minutes] = offsetStr.split(':').map(Number);
        return -(hours + (minutes / 60.0));
      } else {
        return -parseFloat(offsetStr);
      }
    } else {
      return -8; // Default to Pacific Time if parsing fails
    }
  } catch (error) {
    console.warn('Failed to parse timezone offset, using default:', error);
    return -8; // Default to Pacific Time if parsing fails
  }
};

/**
 * Get UTC offset in hours for a given timezone string at a specific date.
 * Works with both IANA timezones (DST-aware) and legacy UTC offset format.
 * 
 * @param {string} timezoneStr - Timezone string (IANA or legacy format)
 * @param {Date} date - Date to calculate offset for (defaults to now)
 * @returns {number} UTC offset in hours
 */
export const getTimezoneOffset = (timezoneStr, date = new Date()) => {
  const parsed = parseTimezoneString(timezoneStr);
  
  // If legacy format, return the fixed offset
  if (parsed.offset !== null) {
    return parsed.offset;
  }
  
  // For IANA timezones, calculate offset dynamically (DST-aware)
  if (parsed.iana) {
    try {
      // Use a simpler approach: format the same UTC timestamp in both UTC and target timezone
      // and calculate the difference
      const utcTimestamp = date.getTime();
      
      // Format in UTC
      const utcParts = new Intl.DateTimeFormat('en', {
        timeZone: 'UTC',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).formatToParts(new Date(utcTimestamp));
      
      // Format in target timezone
      const tzParts = new Intl.DateTimeFormat('en', {
        timeZone: parsed.iana,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).formatToParts(new Date(utcTimestamp));
      
      // Create Date objects from the parts (treating them as UTC components)
      const utcDate = new Date(Date.UTC(
        parseInt(utcParts.find(p => p.type === 'year').value),
        parseInt(utcParts.find(p => p.type === 'month').value) - 1,
        parseInt(utcParts.find(p => p.type === 'day').value),
        parseInt(utcParts.find(p => p.type === 'hour').value),
        parseInt(utcParts.find(p => p.type === 'minute').value),
        parseInt(utcParts.find(p => p.type === 'second').value)
      ));
      
      const tzDate = new Date(Date.UTC(
        parseInt(tzParts.find(p => p.type === 'year').value),
        parseInt(tzParts.find(p => p.type === 'month').value) - 1,
        parseInt(tzParts.find(p => p.type === 'day').value),
        parseInt(tzParts.find(p => p.type === 'hour').value),
        parseInt(tzParts.find(p => p.type === 'minute').value),
        parseInt(tzParts.find(p => p.type === 'second').value)
      ));
      
      // The difference is the timezone offset
      const offsetMs = tzDate.getTime() - utcDate.getTime();
      return offsetMs / (1000 * 60 * 60);
    } catch (error) {
      console.warn('Failed to get IANA timezone offset, using default:', error);
      return -8;
    }
  }
  
  return -8; // Default fallback
};

/**
 * Convert UTC timestamp to user's local timezone
 * @param {string|Date} utcTimestamp - UTC timestamp string or Date object
 * @param {string} userTimezone - User's timezone string from profile
 * @returns {Date} Localized Date object
 */
export const convertUTCToUserTimezone = (utcTimestamp, userTimezone) => {
  try {
    if (!utcTimestamp) return null;
    
    // Parse the UTC timestamp
    const date = new Date(utcTimestamp);
    if (isNaN(date.getTime())) return null;
    
    // Parse timezone string (supports both IANA and legacy formats)
    const parsed = parseTimezoneString(userTimezone);
    
    let localDate;
    
    if (parsed.iana) {
      // Use IANA timezone (DST-aware) with Intl API
      try {
        // Create a formatter for the IANA timezone
        const formatter = new Intl.DateTimeFormat('en-US', {
          timeZone: parsed.iana,
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false
        });
        
        // Format the UTC date in the target timezone
        const parts = formatter.formatToParts(date);
        const year = parseInt(parts.find(p => p.type === 'year').value);
        const month = parseInt(parts.find(p => p.type === 'month').value) - 1; // JS months are 0-indexed
        const day = parseInt(parts.find(p => p.type === 'day').value);
        const hour = parseInt(parts.find(p => p.type === 'hour').value);
        const minute = parseInt(parts.find(p => p.type === 'minute').value);
        const second = parseInt(parts.find(p => p.type === 'second').value);
        
        // Create a Date object representing the local time (stored as UTC components)
        localDate = new Date(Date.UTC(year, month, day, hour, minute, second));
        
        // Calculate the actual offset at this specific date (DST-aware)
        const offsetMs = date.getTime() - localDate.getTime();
        const offsetHours = offsetMs / (1000 * 60 * 60);
        
        localDate._timezoneOffset = offsetHours;
        localDate._timezoneString = parsed.display || parsed.iana;
        localDate._ianaTimezone = parsed.iana;
      } catch (error) {
        console.warn('Failed to use IANA timezone, falling back to offset:', error);
        // Fallback to offset-based calculation
        const offsetHours = getTimezoneOffset(userTimezone, date);
        const offsetMs = offsetHours * 60 * 60 * 1000;
        localDate = new Date(date.getTime() + offsetMs);
        localDate._timezoneOffset = offsetHours;
        localDate._timezoneString = parsed.display || userTimezone;
      }
    } else {
      // Legacy format: use fixed offset
      const offsetHours = parsed.offset || parseTimezoneOffset(userTimezone);
      const offsetMs = offsetHours * 60 * 60 * 1000;
      localDate = new Date(date.getTime() + offsetMs);
      localDate._timezoneOffset = offsetHours;
      localDate._timezoneString = parsed.display || userTimezone;
    }
    
    return localDate;
    
  } catch (error) {
    console.warn('Timezone conversion failed, returning original timestamp:', error);
    return new Date(utcTimestamp);
  }
};

/**
 * Format datetime in user's timezone for display
 * @param {string|Date} dt - UTC timestamp or Date object
 * @param {string} userTimezone - User's timezone string
 * @param {string} formatStr - Format string for display
 * @returns {string} Formatted datetime string in user's timezone
 */
export const formatDateTimeForUser = (dt, userTimezone, formatStr = 'default') => {
  try {
    if (!dt) return 'N/A';
    
    const localDate = convertUTCToUserTimezone(dt, userTimezone);
    if (!localDate) return 'N/A';
    
    // Debug logging
    // console.log(`Formatting: UTC ${dt} -> ${userTimezone} = ${localDate.toISOString()}`);
    
    // Use different formats based on the formatStr parameter
    switch (formatStr) {
      case 'short':
        return localDate.toLocaleDateString(undefined, {
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
          hour12: true
        });
        
      case 'detailed':
        return localDate.toLocaleDateString(undefined, {
          year: 'numeric',
          month: 'long',
          day: 'numeric',
          weekday: 'long',
          hour: 'numeric',
          minute: '2-digit',
          second: '2-digit',
          hour12: true
        });
        
      case 'table':
        return localDate.toLocaleString('en-US', {
          year: 'numeric',
          month: 'numeric',
          day: 'numeric',
          hour: 'numeric',
          minute: 'numeric',
          hour12: true
        });
        
      default:
        // Format with "at" separator for better readability
        // Since JavaScript's toLocaleDateString uses browser timezone, we need to format manually
        // using the UTC components we calculated with the offset
        const year = localDate.getUTCFullYear();
        const monthIndex = localDate.getUTCMonth();
        const monthNames = ['January', 'February', 'March', 'April', 'May', 'June', 
                           'July', 'August', 'September', 'October', 'November', 'December'];
        const month = monthNames[monthIndex];
        const day = localDate.getUTCDate();
        const hours = localDate.getUTCHours();
        const minutes = localDate.getUTCMinutes();
        
        // Format time with AM/PM
        const period = hours >= 12 ? 'PM' : 'AM';
        const displayHours = hours % 12 || 12;
        const displayMinutes = minutes.toString().padStart(2, '0');
        
        return `${month} ${day}, ${year} at ${displayHours}:${displayMinutes} ${period}`;
    }
    
  } catch (error) {
    console.warn('DateTime formatting failed:', error);
    // Fallback to original formatting
    try {
      const date = new Date(dt);
      return date.toLocaleString('en-US', {
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
        hour: 'numeric',
        minute: 'numeric',
        hour12: true
      });
    } catch (fallbackError) {
      return 'N/A';
    }
  }
};

