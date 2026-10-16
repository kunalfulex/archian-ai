/**
 * migration_rules JSON keys — mapping vs extraction stage.
 *
 * - mapping_transformation: field split/combine from the mapping panel (saved on flows)
 * - extraction_rules.transformation_rules: transforms applied during extract / data ops
 */

export const MAPPING_TRANSFORMATION_KEY = 'mapping_transformation';

/** @deprecated legacy flow/chat key — read only */
export const LEGACY_MAPPING_TRANSFORMATION_KEY = 'transformation_rules';

export function getMappingTransformation(migrationRules) {
  if (!migrationRules || typeof migrationRules !== 'object') return null;
  const mt = migrationRules[MAPPING_TRANSFORMATION_KEY];
  if (mt && typeof mt === 'object') return mt;
  const legacy = migrationRules[LEGACY_MAPPING_TRANSFORMATION_KEY];
  if (legacy && typeof legacy === 'object') return legacy;
  return null;
}

export function hasMappingTransformationFields(mappingTransformation) {
  return (
    mappingTransformation &&
    typeof mappingTransformation === 'object' &&
    Array.isArray(mappingTransformation.fields) &&
    mappingTransformation.fields.length > 0
  );
}

/** Drop legacy top-level key when canonical mapping_transformation is present (flow detail UI). */
export function normalizeMigrationRulesForDisplay(rules) {
  if (!rules || typeof rules !== 'object') return rules;
  const out = { ...rules };
  const mt = getMappingTransformation(rules);
  if (mt) {
    out[MAPPING_TRANSFORMATION_KEY] = mt;
    delete out[LEGACY_MAPPING_TRANSFORMATION_KEY];
  }
  return out;
}

/** Map-fields API / stored mapping panel shape (payload may be nested or top-level). */
export function getMappingTransformationFromMapResponse(mapping) {
  if (!mapping || typeof mapping !== 'object') return { fields: [] };
  const mt =
    mapping[MAPPING_TRANSFORMATION_KEY] ||
    mapping[LEGACY_MAPPING_TRANSFORMATION_KEY];
  if (mt && typeof mt === 'object') return mt;
  const nested = mapping.mapping;
  if (nested && typeof nested === 'object') {
    const nestedMt =
      nested[MAPPING_TRANSFORMATION_KEY] ||
      nested[LEGACY_MAPPING_TRANSFORMATION_KEY];
    if (nestedMt && typeof nestedMt === 'object') return nestedMt;
  }
  return { fields: [] };
}
