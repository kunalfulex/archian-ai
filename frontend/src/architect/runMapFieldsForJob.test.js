jest.mock('../api', () => ({ __esModule: true, default: { post: jest.fn() } }));

import api from '../api';
import { refetchMappingViaActionApi, runMapFieldsForJob } from './runMapFieldsForJob';

describe('refetchMappingViaActionApi', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('loads mapping via POST actions/map for lazy panel restore', async () => {
    api.post.mockResolvedValue({
      data: {
        mapping_config: {
          Email: { table: 'users', column: 'email' },
        },
        mapping: {
          available_tables: { users: { columns: ['email'] } },
          primary_table: 'users',
          token_id: 'tok-1',
          spreadsheet_id: 'sheet-1',
          org_rules_display_by_object: { users: [] },
        },
      },
    });

    const out = await refetchMappingViaActionApi({
      conversationId: 'conv-1',
      jobId: 'job-1',
      metadata: { destination_name: 'Test DB', primary_table: 'users' },
    });

    expect(api.post).toHaveBeenCalledWith(
      '/conversations/conv-1/actions/map',
      { job_id: 'job-1', destination_name: 'Test DB' },
      { signal: undefined },
    );
    expect(out.mappingConfig.Email.column).toBe('email');
    expect(out.primaryTable).toBe('users');
    expect(out.objectFieldMap).toEqual({ users: { columns: ['email'] } });
    expect(out.destinationConnectorId).toBe('tok-1');
    expect(out.destinationSpreadsheetId).toBe('sheet-1');
  });

  it('requires conversationId and jobId', async () => {
    await expect(refetchMappingViaActionApi({ conversationId: null, jobId: 'j1' })).rejects.toThrow(
      'Conversation and job_id are required',
    );
  });
});

describe('runMapFieldsForJob', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('requires an active conversation', async () => {
    await expect(
      runMapFieldsForJob({
        jobId: 'job-1',
        inlineDataPreviews: { 'job-1': { data: [{}], documentType: 'Contact' } },
        chatHistory: [],
        metadataMatchesJobId: () => true,
      }),
    ).rejects.toThrow('Mapping requires an active conversation');
  });

  it('posts to actions/map with job_id and destination', async () => {
    api.post.mockResolvedValue({
      data: {
        mapping_id: 'map-99',
        mapping_config: { Name: { table: 'users', column: 'name' } },
        mapping: {
          mapping: { Name: { table: 'users', column: 'name' } },
          available_tables: { users: {} },
          primary_table: 'users',
          connector_id: 'sql-conn-1',
        },
        message_id: 'msg-map-1',
        mapping_version: 2,
      },
    });

    const result = await runMapFieldsForJob({
      jobId: 'job-1',
      conversationId: 'conv-1',
      destination: 'sql',
      destinationName: 'Test DB',
      inlineDataPreviews: {
        'job-1': {
          data: [{ Name: 'Ada' }],
          documentType: 'Contact',
          documentDescription: 'Contacts',
        },
      },
      chatHistory: [
        {
          metadata: {
            event_type: 'extraction_completed',
            job_id: 'job-1',
            destination: 'sql',
            destination_name: 'Test DB',
          },
        },
      ],
      metadataMatchesJobId: (meta, jid) => meta?.job_id === jid,
      sortedFirstRecord: { Name: 'Ada' },
      sortedAllRecords: [{ Name: 'Ada' }],
    });

    expect(api.post).toHaveBeenCalledWith(
      '/conversations/conv-1/actions/map',
      { job_id: 'job-1', destination_name: 'Test DB' },
      { signal: undefined },
    );
    expect(result.mappingId).toBe('map-99');
    expect(result.mappingConfig.Name.column).toBe('name');
    expect(result.backendMessageId).toBe('msg-map-1');
    expect(result.mappingVersion).toBe(2);
    expect(result.destinationConnectorId).toBe('sql-conn-1');
  });
});
