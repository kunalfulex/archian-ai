# Date Formatting Utilities

This directory contains utility functions for formatting dates in a human-readable format across the Archian AI frontend application.

## Available Functions

### `formatHumanReadableDate(dateString)`

The main function that provides intelligent, context-aware date formatting:

- **Just now**: For dates within the last minute
- **X minutes ago**: For dates within the last hour
- **X hours ago**: For dates within the last 24 hours
- **Yesterday**: For dates exactly 1 day ago
- **Day names**: For dates within the last week (e.g., "Monday at 2:30 PM")
- **X days ago**: For dates within the last month
- **Full date**: For older dates (e.g., "January 15, 2024 at 10:30 AM")

### `formatDateTimeForUser(dt, userTimezone, formatStr)`

Formats datetime in user's timezone for display (used in Dashboard, FlowsPage, SuccessfulMigrationsPage, FailedMigrationsPage).

## Usage Examples

```javascript
import { formatHumanReadableDate } from './utils/dateUtils';

// In your component
const MyComponent = () => {
  const createdDate = '2024-01-15T10:30:00Z';
  
  return (
    <div>
      <p>Created: {formatHumanReadableDate(createdDate)}</p>
    </div>
  );
};
```

## Current Implementation

The date formatting utilities are currently used in:

1. **FlowsPage.js** - For displaying flow creation and update dates
2. **ConnectionsPage.js** - For displaying Salesforce connection timestamps

## Benefits

- **User-friendly**: Shows relative time for recent dates
- **Context-aware**: Adapts format based on how old the date is
- **Consistent**: Same formatting logic across all components
- **Maintainable**: Centralized date formatting logic
- **Localized**: Uses browser's locale settings for date formatting

## Future Enhancements

- Add support for different locales
- Add timezone handling
- Add custom format options
- Add date range formatting (e.g., "Jan 15-20, 2024")
