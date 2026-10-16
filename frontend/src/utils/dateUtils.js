/**
 * Utility functions for formatting dates in a human-readable format
 */

/**
 * Formats a date string into a human-readable format with relative time for recent dates
 * @param {string} dateString - ISO date string or date object
 * @returns {string} Human-readable date string
 */
export const formatHumanReadableDate = (dateString) => {
  if (!dateString) return 'N/A';
  try {
    const date = new Date(dateString);
    const now = new Date();
    const diffInMs = now - date;
    const diffInDays = Math.floor(diffInMs / (1000 * 60 * 60 * 24));
    const diffInHours = Math.floor(diffInMs / (1000 * 60 * 60));
    const diffInMinutes = Math.floor(diffInMs / (1000 * 60));

    // If it's today, show relative time
    if (diffInDays === 0) {
      if (diffInHours === 0) {
        if (diffInMinutes === 0) {
          return 'Just now';
        }
        return `${diffInMinutes} minute${diffInMinutes === 1 ? '' : 's'} ago`;
      }
      return `${diffInHours} hour${diffInHours === 1 ? '' : 's'} ago`;
    }
    
    // If it's yesterday
    if (diffInDays === 1) {
      return 'Yesterday';
    }
    
    // If it's within a week, show day name
    if (diffInDays < 7) {
      return date.toLocaleDateString(undefined, { 
        weekday: 'long',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
      });
    }
    
    // If it's within a month, show relative days
    if (diffInDays < 30) {
      return `${diffInDays} day${diffInDays === 1 ? '' : 's'} ago`;
    }
    
    // For older dates, show full date
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true
    });
  } catch (error) {
    return dateString;
  }
};

