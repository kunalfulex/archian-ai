import {
  applyLookupOfToMappingEntry,
  stripEmptyLookupOfFromMapping,
} from './mappingLookupOf';

describe('mappingLookupOf', () => {
  it('removes lookup_of when value is empty', () => {
    const entry = { object: 'Contact', field: 'FirstName', lookup_of: '' };
    const next = applyLookupOfToMappingEntry(entry, '');
    expect(next.lookup_of).toBeUndefined();
    expect(next.object).toBe('Contact');
  });

  it('sets lookup_of when value is valid', () => {
    const entry = { object: 'Account', field: 'Name' };
    const next = applyLookupOfToMappingEntry(entry, ' Contact.AccountId ');
    expect(next.lookup_of).toBe('Contact.AccountId');
  });

  it('stripEmptyLookupOfFromMapping cleans all entries', () => {
    const mapping = {
      Email: { object: 'Contact', field: 'Email', lookup_of: '' },
      Company: { object: 'Account', field: 'Name', lookup_of: 'Contact.AccountId' },
    };
    const out = stripEmptyLookupOfFromMapping(mapping);
    expect(out.Email.lookup_of).toBeUndefined();
    expect(out.Company.lookup_of).toBe('Contact.AccountId');
  });
});
