import api from '../api';
import {
  fetchDestinationConnectionOptions,
  labelForDestinationType,
} from './destinationConnectionOptions';

export function normalizeDestType(destType) {
  const t = (destType || '').toLowerCase();
  if (t === 'gsheets') return 'google_sheets';
  return t;
}

/** True when connector type + connection name are not yet known. */
export function isDestinationConnectionUnspecified(
  destination,
  destinationName,
  { sourceName } = {},
) {
  const dest = normalizeDestType(destination);
  if (!dest || dest === 'unknown') return true;
  const dn = (destinationName || '').trim();
  if (!dn || dn.toLowerCase() === 'unknown') return true;
  // Reload/job-status often copies source workbook name into destination_name for extract-only flows.
  const src = (sourceName || '').trim();
  if (src && dn.toLowerCase() === src.toLowerCase()) return true;
  return false;
}

/** Whether data preview should show the destination picker (ignores stale false flags on reload). */
export function resolveRequiresDestinationForPreview({
  destination,
  destinationName,
  sourceName,
  requiresDestinationFlag,
} = {}) {
  if (requiresDestinationFlag === true) return true;
  return isDestinationConnectionUnspecified(destination, destinationName, { sourceName });
}

/** Build classification_snapshot fields from a manual target pick. */
export function classificationSnapshotFromDestinationTarget(destType, destTarget) {
  const t = normalizeDestType(destType);
  const v = (destTarget || '').trim();
  if (!v) return {};
  if (t === 'salesforce') return { destination_object_api_name: v };
  if (t === 'sql') return { destination_table_name: v };
  if (t === 'google_sheets') return { destination_sheet_name: v };
  return {};
}

/** Merge target fields into message/preview metadata (clears stale target keys). */
export function applyDestinationTargetToMetadata(metadata, destType, destTarget) {
  const next = { ...(metadata || {}) };
  delete next.destination_object_api_name;
  delete next.destination_table_name;
  delete next.destination_sheet_name;
  Object.assign(next, classificationSnapshotFromDestinationTarget(destType, destTarget));
  return next;
}

/** Resolved object / table / tab for a connector type. */
export function destinationTargetValueForType(
  destType,
  { destinationObjectApiName, destinationTableName, destinationSheetName } = {},
) {
  const t = normalizeDestType(destType);
  if (t === 'sql') return (destinationTableName || '').trim();
  if (t === 'google_sheets') return (destinationSheetName || '').trim();
  return (destinationObjectApiName || '').trim();
}

export function destinationTargetFromMetadata(metadata, destType) {
  return destinationTargetValueForType(destType, {
    destinationObjectApiName: metadata?.destination_object_api_name,
    destinationTableName: metadata?.destination_table_name,
    destinationSheetName: metadata?.destination_sheet_name,
  });
}

/** Build classification_snapshot for /map-fields from preview + chat metadata. */
export function buildClassificationSnapshotForMap({
  destType,
  destTarget,
  previewData,
  extractionMetadata,
}) {
  const snap = {};
  const meta = extractionMetadata || {};
  const preview = previewData || {};
  const keys = [
    'destination_object_api_name',
    'destination_table_name',
    'destination_sheet_name',
  ];
  for (const key of keys) {
    const v = (preview[key] ?? meta[key] ?? '').toString().trim();
    if (v) snap[key] = v;
  }
  if (destTarget) {
    Object.assign(snap, classificationSnapshotFromDestinationTarget(destType, destTarget));
  }
  return snap;
}

export async function fetchDestinationTargets(destType, destinationName) {
  const dest = normalizeDestType(destType);
  const name = (destinationName || '').trim();
  if (!dest || !name) {
    return { targetKind: null, targetLabel: null, targets: [] };
  }
  const res = await api.get('/api/mapping/destination-targets', {
    params: { destination: dest, destination_name: name },
  });
  const data = res.data || {};
  return {
    targetKind: data.target_kind || null,
    targetLabel: data.target_label || null,
    targets: Array.isArray(data.targets) ? data.targets : [],
  };
}

export { fetchDestinationConnectionOptions, labelForDestinationType };

export function labelForDestinationTarget(destType) {
  const t = normalizeDestType(destType);
  if (t === 'sql') return 'Table';
  if (t === 'google_sheets') return 'Sheet tab';
  return 'Object';
}
