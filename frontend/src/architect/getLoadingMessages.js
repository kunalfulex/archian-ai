export function getLoadingMessages(userMessage, hasFile) {
  if (hasFile) {
    return [
      'Analyzing your file',
      'Extracting data structure',
      'Processing document',
      'Identifying data patterns',
      'Preparing extraction'
    ];
  }

  const messageLower = (userMessage || '').toLowerCase();

  // Check for extraction-related keywords
  if (messageLower.includes('extract') || messageLower.includes('get data') || messageLower.includes('retrieve')) {
    if (messageLower.includes('salesforce') || messageLower.includes('sf')) {
      return [
        'Analyzing Salesforce request',
        'Identifying Salesforce objects',
        'Generating SOQL query',
        'Preparing data extraction'
      ];
    }
    if (messageLower.includes('sql') || messageLower.includes('database') || messageLower.includes('table')) {
      return [
        'Analyzing SQL request',
        'Identifying database tables',
        'Generating SQL query',
        'Preparing data extraction'
      ];
    }
    if (messageLower.includes('google sheets') || messageLower.includes('spreadsheet')) {
      return [
        'Analyzing Google Sheets request',
        'Identifying spreadsheet structure',
        'Preparing data extraction'
      ];
    }
    return [
      'Analyzing extraction request',
      'Identifying data sources',
      'Preparing extraction'
    ];
  }

  // Check for migration-related keywords
  if (messageLower.includes('migrate') || messageLower.includes('move') || messageLower.includes('transfer')) {
    return [
      'Analyzing migration request',
      'Identifying source and destination',
      'Preparing data mapping',
      'Planning migration strategy'
    ];
  }

  // Check for mapping-related keywords
  if (messageLower.includes('map') || messageLower.includes('field mapping')) {
    return [
      'Analyzing mapping request',
      'Identifying field relationships',
      'Preparing field mappings'
    ];
  }

  // Default messages for general questions
  return [
    'Analyzing your request',
    'Processing your question',
    'Generating response',
    'Thinking'
  ];
}
