/**
 * Edit combine/split transformations in the mapping panel.
 * Keeps mapping shape and transformation_rules.fields in sync (mirrors backend contract).
 */

const COMBINE_FLAG_KEYS = ['is_combined_field', 'combine_group', 'combine_order'];
const SPLIT_FLAG_KEYS = ['is_split_field'];

function stripKeys(entry, keys) {
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return entry;
  const out = { ...entry };
  keys.forEach((k) => {
    delete out[k];
  });
  return out;
}

export function stripCombineFlags(entry) {
  return stripKeys(entry, COMBINE_FLAG_KEYS);
}

export function stripSplitFlags(entry) {
  return stripKeys(entry, SPLIT_FLAG_KEYS);
}

export function normalizeTransformationRules(transformationRules) {
  const fields = transformationRules?.fields;
  return Array.isArray(fields) ? [...fields] : [];
}

/** combine_group → ordered source field keys */
export function findCombineGroups(mapping) {
  const groups = {};
  Object.entries(mapping || {}).forEach(([key, value]) => {
    if (Array.isArray(value)) return;
    if (value?.is_combined_field && value?.combine_group) {
      const groupId = value.combine_group;
      if (!groups[groupId]) groups[groupId] = [];
      groups[groupId].push(key);
    }
  });
  Object.keys(groups).forEach((groupId) => {
    groups[groupId].sort((a, b) => {
      const orderA = mapping[a]?.combine_order ?? 0;
      const orderB = mapping[b]?.combine_order ?? 0;
      return orderA - orderB;
    });
  });
  return groups;
}

/** Source keys whose mapping entry is a split array */
export function findSplitFields(mapping) {
  return Object.keys(mapping || {}).filter(
    (key) => Array.isArray(mapping[key]) && mapping[key].length > 0,
  );
}

export function findCombineRuleIndex(rules, sourceKeys) {
  const expected = new Set(sourceKeys);
  return rules.findIndex(
    (rule) =>
      rule?.action === 'combine' &&
      Array.isArray(rule.source_fields) &&
      rule.source_fields.length === sourceKeys.length &&
      rule.source_fields.every((sf) => expected.has(sf)),
  );
}

export function findSplitRuleIndex(rules, sourceKey) {
  return rules.findIndex(
    (rule) =>
      rule?.source_field === sourceKey &&
      String(rule?.action || '').startsWith('split'),
  );
}

/**
 * Remove combine grouping; each source field keeps its destination mapping.
 */
export function uncombineGroup(mapping, transformationRules, combineGroup) {
  const groups = findCombineGroups(mapping);
  const sourceKeys = groups[combineGroup];
  if (!sourceKeys?.length) {
    return {
      mapping: { ...(mapping || {}) },
      transformationRules: {
        ...(transformationRules || {}),
        fields: normalizeTransformationRules(transformationRules),
      },
    };
  }

  const newMapping = { ...(mapping || {}) };
  sourceKeys.forEach((key) => {
    newMapping[key] = stripCombineFlags(newMapping[key]);
  });

  const fields = normalizeTransformationRules(transformationRules);
  const ruleIdx = findCombineRuleIndex(fields, sourceKeys);
  if (ruleIdx >= 0) fields.splice(ruleIdx, 1);

  return {
    mapping: newMapping,
    transformationRules: { ...(transformationRules || {}), fields },
  };
}

/**
 * Collapse a split array mapping to a single destination row.
 */
export function unsplitField(mapping, transformationRules, sourceKey, { keepIndex = 0 } = {}) {
  const items = mapping?.[sourceKey];
  if (!Array.isArray(items) || items.length === 0) {
    return {
      mapping: { ...(mapping || {}) },
      transformationRules: {
        ...(transformationRules || {}),
        fields: normalizeTransformationRules(transformationRules),
      },
    };
  }

  const idx = Math.min(Math.max(0, keepIndex), items.length - 1);
  const newMapping = { ...(mapping || {}) };
  newMapping[sourceKey] = stripSplitFlags({ ...items[idx] });

  const fields = normalizeTransformationRules(transformationRules);
  const ruleIdx = findSplitRuleIndex(fields, sourceKey);
  if (ruleIdx >= 0) fields.splice(ruleIdx, 1);

  return {
    mapping: newMapping,
    transformationRules: { ...(transformationRules || {}), fields },
  };
}

/** Strip all combine/split structure and clear transformation rules. */
export function clearAllTransformations(mapping, transformationRules) {
  const newMapping = {};
  Object.entries(mapping || {}).forEach(([key, value]) => {
    if (Array.isArray(value) && value.length > 0) {
      newMapping[key] = stripSplitFlags(stripCombineFlags({ ...value[0] }));
    } else if (value && typeof value === 'object') {
      newMapping[key] = stripCombineFlags(value);
    } else {
      newMapping[key] = value;
    }
  });
  return {
    mapping: newMapping,
    transformationRules: { fields: [] },
  };
}

export function hasActiveTransformations(mapping, transformationRules) {
  if (normalizeTransformationRules(transformationRules).length > 0) return true;
  if (findSplitFields(mapping).length > 0) return true;
  return Object.keys(findCombineGroups(mapping)).length > 0;
}

/** Combine rules from transformation_rules.fields */
export function getCombineRules(transformationRules) {
  return normalizeTransformationRules(transformationRules).filter(
    (rule) => rule?.action === 'combine' && Array.isArray(rule.source_fields) && rule.source_fields.length >= 2,
  );
}

/**
 * Keys that duplicate a combine output (e.g. orphan "Name" when FirstName+LastName combine into Name).
 */
export function getOrphanCombineOutputKeys(mapping, transformationRules) {
  const orphans = new Set();
  getCombineRules(transformationRules).forEach((rule) => {
    const sources = (rule.source_fields || []).filter((sf) => mapping?.[sf]);
    if (sources.length < 2) return;
    const outputField = (rule.output_field || '').trim();
    if (!outputField) return;
    Object.keys(mapping || {}).forEach((key) => {
      if (sources.includes(key)) return;
      if (key.toLowerCase() === outputField.toLowerCase()) {
        orphans.add(key);
      }
    });
  });
  return orphans;
}

function resolveCombineDestination(rule, destination, mapping, sourceFields) {
  const { primaryKey, secondaryKey } = getDestinationKeysForConnector(destination);
  let primary =
    rule.output_object ||
    rule.output_sheet ||
    rule.output_table ||
    '';
  const secondary = rule.output_field || '';
  if (!primary && sourceFields?.length) {
    const firstEntry = mapping?.[sourceFields[0]];
    if (firstEntry && !Array.isArray(firstEntry)) {
      primary = (firstEntry[primaryKey] || '').trim();
    }
  }
  return { primaryKey, secondaryKey, primary, secondary };
}

/**
 * Apply is_combined_field flags from transformation rules when mapping lacks them
 * (e.g. after saved-flow reload or LLM returned rules without flags on every key).
 */
export function syncMappingFlagsFromTransformationRules(
  mapping,
  transformationRules,
  destination = 'salesforce',
) {
  let nextMapping = { ...(mapping || {}) };
  let changed = false;

  getCombineRules(transformationRules).forEach((rule) => {
    const sourceFields = (rule.source_fields || []).filter((sf) => nextMapping[sf] && !Array.isArray(nextMapping[sf]));
    if (sourceFields.length < 2) return;

    const alreadyFlagged = sourceFields.every((sf) => nextMapping[sf]?.is_combined_field);
    if (alreadyFlagged) return;

    const { primaryKey, secondaryKey, primary, secondary } = resolveCombineDestination(
      rule,
      destination,
      nextMapping,
      sourceFields,
    );
    if (!primary || !secondary) return;

    const combineGroup = buildCombineGroupId(primary, secondary);
    sourceFields.forEach((sf, idx) => {
      const base = stripSplitFlags(stripCombineFlags({ ...(nextMapping[sf] || {}) }));
      nextMapping[sf] = {
        ...base,
        [primaryKey]: primary,
        [secondaryKey]: secondary,
        is_combined_field: true,
        combine_group: combineGroup,
        combine_order: idx,
      };
      changed = true;
    });
  });

  const orphans = getOrphanCombineOutputKeys(nextMapping, transformationRules);
  orphans.forEach((key) => {
    if (nextMapping[key]) {
      delete nextMapping[key];
      changed = true;
    }
  });

  return changed ? nextMapping : mapping;
}

/** Keys to hide from source-field rows when a combine rule owns the destination output. */
export function getCombineSuppressedExtractionKeys(transformationRules) {
  const suppressed = new Set();
  getCombineRules(transformationRules).forEach((rule) => {
    const sources = new Set(rule.source_fields || []);
    const output = (rule.output_field || '').trim().toLowerCase();
    if (!output || sources.size < 2) return;
    suppressed.add(output);
    sources.forEach((sf) => suppressed.delete(String(sf).toLowerCase()));
  });
  return suppressed;
}

export function isCombineSuppressedSourceKey(key, transformationRules) {
  if (!key) return false;
  const lower = String(key).toLowerCase();
  return getCombineSuppressedExtractionKeys(transformationRules).has(lower);
}

/** Human label for combined source fields, e.g. "First Name + Last Name → Name" */
export function formatCombineSourcesLabel(sourceKeys, outputField) {
  const sources = (sourceKeys || [])
    .map((k) => camelCaseToLabel(k))
    .join(' + ');
  if (!outputField) return sources;
  return `${sources} → ${outputField}`;
}

function camelCaseToLabel(str) {
  return String(str || '')
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^./, (char) => char.toUpperCase());
}

/** Lookup combine rule covering a source field key */
export function findCombineRuleForSourceKey(transformationRules, sourceKey) {
  return getCombineRules(transformationRules).find((rule) =>
    (rule.source_fields || []).includes(sourceKey),
  );
}

export function getDestinationKeysForConnector(destination) {
  if (destination === 'salesforce') {
    return { primaryKey: 'object', secondaryKey: 'field' };
  }
  if (destination === 'google_sheets') {
    return { primaryKey: 'sheet', secondaryKey: 'column' };
  }
  return { primaryKey: 'table', secondaryKey: 'column' };
}

export function getMappingDestination(entry, destination) {
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
    return { primary: '', secondary: '' };
  }
  const { primaryKey, secondaryKey } = getDestinationKeysForConnector(destination);
  return {
    primary: (entry[primaryKey] || '').trim(),
    secondary: (entry[secondaryKey] || '').trim(),
  };
}

export function isEligibleForCombine(key, mapping) {
  const entry = mapping?.[key];
  if (!entry || Array.isArray(entry)) return false;
  if (entry.is_combined_field) return false;
  return true;
}

export function isEligibleForSplit(key, mapping) {
  const entry = mapping?.[key];
  if (!entry || Array.isArray(entry)) return false;
  if (entry.is_combined_field) return false;
  return true;
}

function buildCombineGroupId(primary, secondary) {
  return `${primary}.${secondary}`.toLowerCase().replace(/\s+/g, '_');
}

function removeRulesForSourceKeys(fields, sourceKeys) {
  const keySet = new Set(sourceKeys);
  return fields.filter((rule) => {
    if (rule?.source_field && keySet.has(rule.source_field)) return false;
    if (
      rule?.action === 'combine' &&
      Array.isArray(rule.source_fields) &&
      rule.source_fields.some((sf) => keySet.has(sf))
    ) {
      return false;
    }
    return true;
  });
}

/**
 * Combine multiple source fields into one destination column at migration time.
 */
export function combineSourceFields(
  mapping,
  transformationRules,
  sourceKeys,
  destinationTarget,
  options = {},
) {
  const orderedKeys = (sourceKeys || []).filter(Boolean);
  if (orderedKeys.length < 2) return null;

  const { separator = ' ', destination = 'salesforce' } = options;
  const { primary, secondary } = destinationTarget || {};
  if (!primary || !secondary) return null;

  for (const key of orderedKeys) {
    if (!isEligibleForCombine(key, mapping)) return null;
  }

  const { primaryKey, secondaryKey } = getDestinationKeysForConnector(destination);
  const combineGroup = buildCombineGroupId(primary, secondary);
  const newMapping = { ...(mapping || {}) };

  orderedKeys.forEach((key, idx) => {
    const base = stripSplitFlags(stripCombineFlags({ ...(newMapping[key] || {}) }));
    newMapping[key] = {
      ...base,
      [primaryKey]: primary,
      [secondaryKey]: secondary,
      is_combined_field: true,
      combine_group: combineGroup,
      combine_order: idx,
    };
  });

  const fields = removeRulesForSourceKeys(
    normalizeTransformationRules(transformationRules),
    orderedKeys,
  );

  const combineRule = {
    source_fields: [...orderedKeys],
    action: 'combine',
    separator,
    output_field: secondary,
  };
  if (destination === 'salesforce') {
    combineRule.output_object = primary;
  } else if (destination === 'sql') {
    combineRule.output_table = primary;
  } else if (destination === 'google_sheets') {
    combineRule.output_sheet = primary;
  }
  fields.push(combineRule);

  return {
    mapping: newMapping,
    transformationRules: { ...(transformationRules || {}), fields },
  };
}

/**
 * Split one source field into multiple destination columns.
 */
export function splitSourceField(
  mapping,
  transformationRules,
  sourceKey,
  destTargets,
  options = {},
) {
  if (!isEligibleForSplit(sourceKey, mapping)) return null;

  const targets = (destTargets || []).filter(
    (t) => t?.primary && t?.secondary,
  );
  if (targets.length < 2) return null;

  const { destination = 'salesforce', splitChar = ' ', action = 'split_2' } = options;
  const { primaryKey, secondaryKey } = getDestinationKeysForConnector(destination);
  const baseEntry = mapping[sourceKey] || {};

  const newMapping = { ...(mapping || {}) };
  newMapping[sourceKey] = targets.map((target) => {
    const item = {
      [primaryKey]: target.primary,
      [secondaryKey]: target.secondary,
      is_split_field: true,
    };
    if (baseEntry.is_duplicate_check) {
      item.is_duplicate_check = baseEntry.is_duplicate_check;
    }
    if (baseEntry.lookup_of) {
      item.lookup_of = baseEntry.lookup_of;
    }
    if (target.lookup_of) {
      item.lookup_of = target.lookup_of;
    }
    return item;
  });

  const fields = normalizeTransformationRules(transformationRules).filter(
    (rule) => rule?.source_field !== sourceKey,
  );
  fields.push({
    source_field: sourceKey,
    action,
    split_char: splitChar,
    output_fields: targets.map((t) => t.secondary),
  });

  return {
    mapping: newMapping,
    transformationRules: { ...(transformationRules || {}), fields },
  };
}

