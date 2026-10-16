/**
 * Shared helpers for flow-detail migration rules validation (modal + post-save review).
 */

export function snapshotHasEditorContent(s) {
  if (!s || typeof s !== 'object') return false;
  if (Array.isArray(s.extraction_transformation_fields) && s.extraction_transformation_fields.length > 0) {
    return true;
  }
  if (Array.isArray(s.mapping_transformation_fields) && s.mapping_transformation_fields.length > 0) {
    return true;
  }
  const conds = s.filter_rules && s.filter_rules.conditions;
  if (Array.isArray(conds) && conds.length > 0) return true;
  const df = s.deduplication_rules && s.deduplication_rules.fields;
  if (Array.isArray(df) && df.length > 0) return true;
  const p = s.missing_field_preference;
  if (p && typeof p === 'object' && (p.option != null || Object.keys(p).length > 0)) return true;
  const dc = s.data_cleanup_rules;
  if (dc && typeof dc === 'object' && !Array.isArray(dc) && Object.keys(dc).length > 0) return true;
  return false;
}

/**
 * After connection or mapping/query change, open the review modal when rules need attention
 * or when saved rules exist and should be reviewed against the new source shape.
 */
export function shouldOpenMigrationRulesReviewModal(data) {
  if (!data || data.status !== 'success') return false;
  if (data.skipped === 'file_source') return false;
  const issues = Array.isArray(data.issues) ? data.issues : [];
  if (issues.length > 0) return true;
  return snapshotHasEditorContent(data.snapshot);
}
