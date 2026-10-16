jest.mock('../api', () => ({ __esModule: true, default: { post: jest.fn() } }));

import { buildMigrationDataFromPanel } from './executeChatMigration';

describe('buildMigrationDataFromPanel', () => {
  it('includes map-fields transformation_rules for sql without preview-transform job', () => {
    const mappingData = {
      mapping: { Name: [{ table: 'users', column: 'first_name' }] },
      transformationRules: { fields: [{ action: 'split_2', source_field: 'Name' }] },
      documentType: 'User Management',
      destination: 'sql',
      destinationName: 'Test DB',
    };
    const entry = { metadata: { job_id: 'job-v1', mapping_id: 'map-1' } };
    const chatHistory = [
      {
        metadata: {
          event_type: 'extraction_completed',
          job_id: 'job-v2',
          parent_job_id: 'job-v1',
          extraction_version: 2,
        },
      },
    ];

    const out = buildMigrationDataFromPanel({
      mappingData,
      entry,
      chatHistory,
      activeConversationId: 'conv-1',
    });

    expect(out.job_id).toBe('job-v2');
    expect(out.transformation_rules).toEqual(mappingData.transformationRules);
    expect(out.is_transformed_data).toBe(false);
  });

  it('includes transformation_rules for google_sheets without preview-transform job', () => {
    const rules = { fields: [{ action: 'split_2', source_field: 'Name' }] };
    const mappingData = {
      mapping: { Name: [{ sheet: 'Sheet1', column: 'First Name' }] },
      transformationRules: rules,
      destination: 'google_sheets',
    };
    const out = buildMigrationDataFromPanel({
      mappingData,
      entry: { metadata: { job_id: 'job-1' } },
      chatHistory: [],
      activeConversationId: 'conv-1',
    });
    expect(out.transformation_rules).toEqual(rules);
    expect(out.is_transformed_data).toBe(false);
  });

  it('includes split/combine transformation_rules for salesforce without preview-transform job', () => {
    const rules = {
      fields: [
        { action: 'split_2', source_field: 'Name' },
        { action: 'keep_only', source_field: 'Email' },
      ],
    };
    const mappingData = {
      mapping: {
        Name: [
          { object: 'Contact', field: 'FirstName', lookup_of: 'Contact.AccountId' },
          { object: 'Contact', field: 'LastName', lookup_of: 'Contact.AccountId' },
        ],
      },
      transformationRules: rules,
      destination: 'salesforce',
    };
    const out = buildMigrationDataFromPanel({
      mappingData,
      entry: { metadata: { job_id: 'job-1' } },
      chatHistory: [],
      activeConversationId: 'conv-1',
    });
    expect(out.transformation_rules).toEqual({
      fields: [{ action: 'split_2', source_field: 'Name' }],
    });
  });

  it('omits non-split transformation_rules for salesforce without preview-transform job', () => {
    const mappingData = {
      mapping: { Name: { object: 'Contact', field: 'LastName' } },
      transformationRules: { fields: [{ action: 'keep_only', source_field: 'Email' }] },
      destination: 'salesforce',
    };
    const out = buildMigrationDataFromPanel({
      mappingData,
      entry: { metadata: { job_id: 'job-1' } },
      chatHistory: [],
      activeConversationId: 'conv-1',
    });
    expect(out.transformation_rules).toBeNull();
  });

  it('includes transformation_rules when preview transformation_completed exists', () => {
    const mappingData = {
      mapping: { Name: [{ table: 'users', column: 'first_name' }] },
      transformationRules: { fields: [{ action: 'split_2', source_field: 'Name' }] },
      destination: 'sql',
    };
    const entry = { metadata: { job_id: 'job-v1', mapping_id: 'map-1' } };
    const chatHistory = [
      {
        metadata: {
          event_type: 'transformation_completed',
          job_id: 'job-v1',
          mapping_id: 'map-1',
          transformed_job_id: 'job-transformed',
        },
      },
    ];

    const out = buildMigrationDataFromPanel({
      mappingData,
      entry,
      chatHistory,
      activeConversationId: 'conv-1',
    });

    expect(out.is_transformed_data).toBe(true);
    expect(out.transformed_job_id).toBe('job-transformed');
    expect(out.transformation_rules).toEqual(mappingData.transformationRules);
  });
});
