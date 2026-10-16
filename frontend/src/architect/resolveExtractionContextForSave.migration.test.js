import {
  findBestMigrationCompletedEvent,
  pickSalesforceDestinationConnectorId,
} from './resolveExtractionContextForSave';

describe('findBestMigrationCompletedEvent', () => {
  it('prefers migration_completed with destination_connector_id over sparse event', () => {
    const history = [
      {
        id: 'sparse',
        metadata: {
          event_type: 'migration_completed',
          job_id: 'job-1',
          mapping_config: { Email: { field: 'Email' } },
        },
        timestamp: 200,
      },
      {
        id: 'full',
        metadata: {
          event_type: 'migration_completed',
          job_id: 'job-1',
          destination_connector_id: 'sf-token-99',
        },
        timestamp: 100,
      },
    ];
    const best = findBestMigrationCompletedEvent(history, { jobId: 'job-1' });
    expect(best?.id).toBe('full');
  });
});

describe('pickSalesforceDestinationConnectorId', () => {
  it('falls back to mapping_started metadata', () => {
    const id = pickSalesforceDestinationConnectorId({
      migrationCompletedEvent: { metadata: {} },
      frontendMappingStartedEvent: {
        metadata: { destination_connector_id: 'org-abc' },
      },
    });
    expect(id).toBe('org-abc');
  });
});
