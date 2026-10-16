export function mapServerMessageToEntry(message) {
  // Check if this is a mapping_started event (can be 'assistant' or 'data_event' role)
  if (message?.metadata?.event_type === 'mapping_started') {
    // Only use message.id (UUID) - don't fall back to mapping_id as it's not a valid UUID
    if (!message?.id) {
      // If no message ID, we can't update this message, so skip it or use a temporary ID
      // But we should still create the entry for display purposes
      return {
        id: `temp-${message?.metadata?.mapping_id || Date.now()}`,
        type: 'mapping_panel',
        text: message?.content || '',
        timestamp: message?.created_at ? new Date(message.created_at).getTime() : Date.now(),
        metadata: message?.metadata || null,
        mappingId: message?.metadata?.mapping_id,
        isLoading: false,
        isUnavailable: false,
        canUpdate: false, // Mark that this entry cannot be updated
      };
    }
    return {
      id: message.id, // Always use the actual UUID from database
      type: 'mapping_panel',
      text: message?.content || '',
      timestamp: message?.created_at ? new Date(message.created_at).getTime() : Date.now(),
      metadata: message?.metadata || null,
      mappingId: message?.metadata?.mapping_id,
      isLoading: false, // Will be set to true when restoring
      isUnavailable: false, // Will be set to true if restoration fails
      canUpdate: true, // Mark that this entry can be updated
    };
  }

  // Check if this is a migration_started event
  if (message?.metadata?.event_type === 'migration_started') {
    return {
      id: message?.id || `migration-${message?.metadata?.job_id || message?.metadata?.batch_id}`,
      type: 'migration_status',
      text: message?.content || '',
      timestamp: message?.created_at ? new Date(message.created_at).getTime() : Date.now(),
      metadata: message?.metadata || null,
      jobId: message?.metadata?.job_id || message?.metadata?.batch_id,
      batchId: message?.metadata?.batch_id,
      isLoading: false,
      isUnavailable: false,
    };
  }

  // Keep other data_event messages as system messages (for context)
  // Preview entries will be added separately from extraction results
  // Regular message mapping
  const metadata = message?.metadata || null;
  return {
    id: message?.id,
    type: message?.role === 'user' ? 'user' : 
          message?.role === 'data_event' ? 'system' : 'system',
    text: message?.content || '',
    timestamp: message?.created_at ? new Date(message.created_at).getTime() : Date.now(),
    metadata,
    plannerResolved: Boolean(metadata?.planner_resolved || metadata?.plannerResolved),
  };
}
