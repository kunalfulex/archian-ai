jest.mock('../api', () => ({ __esModule: true, default: { patch: jest.fn(), put: jest.fn() } }));

import api from '../api';
import {
  buildMappingPanelMetadataPatch,
  persistMappingPanelEdit,
} from './persistMappingPanelEdit';

describe('persistMappingPanelEdit', () => {
  test('buildMappingPanelMetadataPatch includes lookup_of in mapping_config', () => {
    const mapping = {
      Email: { table: 'users', column: 'email', lookup_of: 'user_plans.user_id' },
    };
    const meta = buildMappingPanelMetadataPatch(
      { job_id: 'j1', event_type: 'mapping_started' },
      mapping,
      { fields: [] },
      { primaryTable: 'users' },
    );
    expect(meta.mapping_config.Email.lookup_of).toBe('user_plans.user_id');
    expect(meta.primary_table).toBe('users');
  });

  test('persistMappingPanelEdit patches session mapping draft only', async () => {
    api.patch.mockResolvedValue({ data: {} });

    const entryMetadata = {
      job_id: 'job-1',
      event_type: 'mapping_started',
      destination: 'sql',
      document_type: 'Contact',
    };
    const updatedMapping = {
      Email: { table: 'users', column: 'email' },
      Phone: { table: 'users', column: 'phone' },
    };

    await persistMappingPanelEdit({
      conversationId: 'conv-1',
      entryId: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
      entryMetadata,
      panel: { primaryTable: 'users' },
      updatedMapping,
      transformationRules: { fields: [] },
      updateMessageMetadata: jest.fn(),
      setChatHistory: jest.fn(),
    });

    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(api.patch).toHaveBeenCalledWith(
      '/conversations/conv-1/session/mapping-draft',
      expect.objectContaining({
        job_id: 'job-1',
        mapping_config: updatedMapping,
        destination: 'sql',
        table_name: 'users',
      }),
    );
    expect(api.put).not.toHaveBeenCalled();
  });
});
