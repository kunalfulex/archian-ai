/**
 * Client-side intent_state for extraction_completed chat metadata.
 * Mirrors backend create_intent_state so architect/classifier see active session in-thread.
 */

function normalizeSourceType(sourceType) {
  const raw = (sourceType || '').toLowerCase();
  if (raw === 'gsheets') return 'google_sheets';
  return raw || 'unknown';
}

function normalizeDestinationType(destination) {
  const raw = (destination || '').toLowerCase();
  if (raw === 'gsheets') return 'google_sheets';
  return raw || 'unknown';
}

/**
 * Canonical intent snapshot from planner/classification payloads (P5.5).
 * Prefers intent_state; ignores legacy migration_intent when intent_state is present.
 */
export function readIntentStateFromClassification(classification) {
  if (!classification || typeof classification !== 'object') return null;
  const snap = classification.intent_state;
  if (snap && typeof snap === 'object' && Object.keys(snap).length > 0) {
    return snap;
  }
  return null;
}

export function readIntentStateFromPlannerPending(pending) {
  if (!pending || typeof pending !== 'object') return null;
  return (
    (pending.intent_state && typeof pending.intent_state === 'object' && pending.intent_state) ||
    readIntentStateFromClassification(pending.classification) ||
    null
  );
}

/** Merge intent_state onto planner_pending for chat metadata and resume API. */
export function enrichPlannerPendingWithIntentState(pending) {
  if (!pending || typeof pending !== 'object') return pending;
  const intentState = readIntentStateFromPlannerPending(pending);
  if (!intentState) return pending;
  const classification = pending.classification
    ? { ...pending.classification, intent_state: intentState }
    : pending.classification;
  return {
    ...pending,
    intent_state: intentState,
    ...(classification ? { classification } : {}),
  };
}

export function buildIntentStateFromExtraction({
  jobId,
  sourceType,
  sourceName,
  destination,
  destinationName,
  extractionRules,
  extractionVersion,
}) {
  if (!jobId || !sourceType) return null;
  const expiresAt = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
  const activeSource = normalizeSourceType(sourceType);
  const dest = normalizeDestinationType(destination);
  return {
    active_intent: dest && dest !== 'unknown' ? 'migrate' : 'extract',
    active_source: activeSource,
    active_destination: dest,
    source_name: sourceName || null,
    destination_name: destinationName || null,
    job_id: jobId,
    active_job_id: jobId,
    state: 'extraction_completed',
    created_at: new Date().toISOString(),
    expires_at: expiresAt,
    is_active: true,
    ...(extractionRules ? { extraction_rules: extractionRules } : {}),
    ...(extractionVersion != null ? { active_version: extractionVersion } : {}),
  };
}
