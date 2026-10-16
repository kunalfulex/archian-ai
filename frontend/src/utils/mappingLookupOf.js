/**
 * lookup_of on mapping entries must be BaseObject.LookupField or omitted — never "".
 */

export function applyLookupOfToMappingEntry(entry, lookupValue) {
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
    return entry;
  }
  const next = { ...entry };
  const trimmed = String(lookupValue ?? '').trim();
  if (trimmed) {
    next.lookup_of = trimmed;
  } else {
    delete next.lookup_of;
  }
  return next;
}

/** Strip empty/invalid lookup_of from all mapping entries before persist or migrate. */
export function stripEmptyLookupOfFromMapping(mapping) {
  if (!mapping || typeof mapping !== 'object') {
    return mapping || {};
  }
  const out = {};
  for (const [key, config] of Object.entries(mapping)) {
    if (Array.isArray(config)) {
      out[key] = config.map((item) =>
        item && typeof item === 'object' && !Array.isArray(item)
          ? applyLookupOfToMappingEntry(item, item.lookup_of)
          : item,
      );
    } else if (config && typeof config === 'object' && !Array.isArray(config)) {
      out[key] = applyLookupOfToMappingEntry(config, config.lookup_of);
    } else {
      out[key] = config;
    }
  }
  return out;
}
