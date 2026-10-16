/**
 * Verify frontend mappingTransformEdit output shapes (run with node).
 * Used alongside backend/tests/test_mapping_transform_frontend_contract.py
 */
import {
  combineSourceFields,
  splitSourceField,
  uncombineGroup,
  unsplitField,
  clearAllTransformations,
} from '../src/utils/mappingTransformEdit.js';

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const baseMapping = {
  first_name: { object: 'Contact', field: 'FirstName' },
  last_name: { object: 'Contact', field: 'LastName' },
  full_name: { object: 'Contact', field: 'Name' },
};

const combined = combineSourceFields(
  baseMapping,
  { fields: [] },
  ['first_name', 'last_name'],
  { primary: 'Contact', secondary: 'Name' },
  { destination: 'salesforce', separator: ' ' },
);
assert(combined?.mapping?.first_name?.is_combined_field, 'combine flags');
assert(combined.transformationRules.fields[0].action === 'combine', 'combine rule');

const split = splitSourceField(
  combined.mapping,
  combined.transformationRules,
  'full_name',
  [
    { primary: 'Contact', secondary: 'FirstName' },
    { primary: 'Contact', secondary: 'LastName' },
  ],
  { destination: 'salesforce' },
);
assert(Array.isArray(split.mapping.full_name), 'split array');
assert(split.transformationRules.fields.some((r) => r.action === 'split_2'), 'split rule');

const uncombined = uncombineGroup(
  combined.mapping,
  combined.transformationRules,
  combined.mapping.first_name.combine_group,
);
assert(!uncombined.mapping.first_name.is_combined_field, 'uncombine');
assert(uncombined.transformationRules.fields.length === 0, 'no combine rule');

const unsplit = unsplitField(split.mapping, split.transformationRules, 'full_name');
assert(!Array.isArray(unsplit.mapping.full_name), 'unsplit single');

const cleared = clearAllTransformations(split.mapping, split.transformationRules);
assert(cleared.transformationRules.fields.length === 0, 'cleared rules');

console.log('OK mappingTransformEdit contract checks passed');
console.log(JSON.stringify({ combineRule: combined.transformationRules.fields[0] }, null, 2));
