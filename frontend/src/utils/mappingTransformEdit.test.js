import {
  clearAllTransformations,
  combineSourceFields,
  findCombineGroups,
  findSplitFields,
  formatCombineSourcesLabel,
  getCombineSuppressedExtractionKeys,
  getOrphanCombineOutputKeys,
  isCombineSuppressedSourceKey,
  hasActiveTransformations,
  splitSourceField,
  syncMappingFlagsFromTransformationRules,
  uncombineGroup,
  unsplitField,
} from './mappingTransformEdit';

describe('mappingTransformEdit', () => {
  const combinedMapping = {
    first_name: {
      object: 'Contact',
      field: 'Name',
      is_combined_field: true,
      combine_group: 'name',
      combine_order: 0,
    },
    last_name: {
      object: 'Contact',
      field: 'Name',
      is_combined_field: true,
      combine_group: 'name',
      combine_order: 1,
    },
  };

  const combineRules = {
    fields: [
      {
        action: 'combine',
        source_fields: ['first_name', 'last_name'],
        output_field: 'Name',
        separator: ' ',
      },
    ],
  };

  it('findCombineGroups returns grouped keys', () => {
    expect(findCombineGroups(combinedMapping)).toEqual({
      name: ['first_name', 'last_name'],
    });
  });

  it('uncombineGroup strips flags and removes combine rule', () => {
    const { mapping, transformationRules } = uncombineGroup(
      combinedMapping,
      combineRules,
      'name',
    );
    expect(mapping.first_name.is_combined_field).toBeUndefined();
    expect(mapping.last_name.combine_group).toBeUndefined();
    expect(transformationRules.fields).toHaveLength(0);
  });

  it('unsplitField collapses array and removes split rule', () => {
    const splitMapping = {
      full_name: [
        { object: 'Contact', field: 'FirstName', is_split_field: true },
        { object: 'Contact', field: 'LastName', is_split_field: true },
      ],
    };
    const splitRules = {
      fields: [
        {
          source_field: 'full_name',
          action: 'split_2',
          split_char: ' ',
          output_fields: ['FirstName', 'LastName'],
        },
      ],
    };
    const { mapping, transformationRules } = unsplitField(
      splitMapping,
      splitRules,
      'full_name',
    );
    expect(Array.isArray(mapping.full_name)).toBe(false);
    expect(mapping.full_name.field).toBe('FirstName');
    expect(mapping.full_name.is_split_field).toBeUndefined();
    expect(transformationRules.fields).toHaveLength(0);
  });

  it('clearAllTransformations resets mapping and rules', () => {
    const splitMapping = {
      full_name: [
        { object: 'Contact', field: 'FirstName', is_split_field: true },
        { object: 'Contact', field: 'LastName', is_split_field: true },
      ],
      ...combinedMapping,
    };
    const rules = {
      fields: [
        { source_field: 'full_name', action: 'split_2' },
        { action: 'combine', source_fields: ['first_name', 'last_name'] },
      ],
    };
    const { mapping, transformationRules } = clearAllTransformations(splitMapping, rules);
    expect(findSplitFields(mapping)).toHaveLength(0);
    expect(Object.keys(findCombineGroups(mapping))).toHaveLength(0);
    expect(transformationRules.fields).toHaveLength(0);
  });

  it('hasActiveTransformations detects splits and combines', () => {
    expect(hasActiveTransformations(combinedMapping, { fields: [] })).toBe(true);
    expect(hasActiveTransformations({}, combineRules)).toBe(true);
    expect(hasActiveTransformations({}, { fields: [] })).toBe(false);
  });
});

describe('combineSourceFields', () => {
  it('marks fields combined and adds combine rule', () => {
    const mapping = {
      first_name: { object: 'Contact', field: 'Name' },
      last_name: { object: 'Contact', field: 'Name' },
    };
    const { mapping: out, transformationRules } = combineSourceFields(
      mapping,
      { fields: [] },
      ['first_name', 'last_name'],
      { primary: 'Contact', secondary: 'Name' },
      { destination: 'salesforce' },
    );
    expect(out.first_name.is_combined_field).toBe(true);
    expect(out.last_name.combine_order).toBe(1);
    expect(transformationRules.fields).toHaveLength(1);
    expect(transformationRules.fields[0].action).toBe('combine');
  });
});

describe('splitSourceField', () => {
  it('creates split array and split rule', () => {
    const mapping = {
      full_name: { object: 'Contact', field: 'Name' },
    };
    const { mapping: out, transformationRules } = splitSourceField(
      mapping,
      { fields: [] },
      'full_name',
      [
        { primary: 'Contact', secondary: 'FirstName' },
        { primary: 'Contact', secondary: 'LastName' },
      ],
      { destination: 'salesforce' },
    );
    expect(Array.isArray(out.full_name)).toBe(true);
    expect(out.full_name).toHaveLength(2);
    expect(transformationRules.fields[0].action).toBe('split_2');
  });
});

describe('syncMappingFlagsFromTransformationRules', () => {
  it('applies combine flags and removes orphan Name key', () => {
    const mapping = {
      FirstName: { sheet: 'Sheet1', column: 'Name' },
      LastName: { sheet: 'Sheet1', column: 'Name' },
      Name: { sheet: 'Sheet1', column: 'Name' },
    };
    const rules = {
      fields: [
        {
          action: 'combine',
          source_fields: ['FirstName', 'LastName'],
          output_field: 'Name',
          output_sheet: 'Sheet1',
        },
      ],
    };
    const synced = syncMappingFlagsFromTransformationRules(
      mapping,
      rules,
      'google_sheets',
    );
    expect(synced.FirstName.is_combined_field).toBe(true);
    expect(synced.LastName.is_combined_field).toBe(true);
    expect(synced.Name).toBeUndefined();
  });

  it('formatCombineSourcesLabel renders source list', () => {
    expect(formatCombineSourcesLabel(['FirstName', 'LastName'], 'Name')).toBe(
      'First Name + Last Name → Name',
    );
  });

  it('getOrphanCombineOutputKeys detects duplicate output key', () => {
    const mapping = {
      FirstName: {},
      LastName: {},
      Name: {},
    };
    const rules = {
      fields: [
        {
          action: 'combine',
          source_fields: ['FirstName', 'LastName'],
          output_field: 'Name',
        },
      ],
    };
    expect(getOrphanCombineOutputKeys(mapping, rules).has('Name')).toBe(true);
  });

  it('getCombineSuppressedExtractionKeys hides phantom name when combining to Name', () => {
    const rules = {
      fields: [
        {
          action: 'combine',
          source_fields: ['first_name', 'last_name'],
          output_field: 'Name',
        },
      ],
    };
    expect(getCombineSuppressedExtractionKeys(rules).has('name')).toBe(true);
    expect(isCombineSuppressedSourceKey('first_name', rules)).toBe(false);
    expect(isCombineSuppressedSourceKey('name', rules)).toBe(true);
  });
});
