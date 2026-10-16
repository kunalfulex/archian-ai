import {
  resolveTransformedJobForMigration,
  resolveRecordsJobIdForMigration,
} from './resolveTransformedJobForMigration';

describe('resolveTransformedJobForMigration', () => {
  const sqlParent = 'sql-extract-job';
  const sqlChild = 'sql-filter-child';
  const sfParent = 'sf-extract-job';
  const sfChild = 'sf-filter-child';

  const history = [
    {
      metadata: {
        event_type: 'transformation_completed',
        mapping_id: 'map-sql',
        job_id: sqlParent,
        parent_job_id: sqlParent,
        transformed_job_id: sqlChild,
      },
    },
    {
      metadata: {
        event_type: 'extraction_completed',
        job_id: sfChild,
        parent_job_id: sfParent,
        extraction_version: 2,
        source_type: 'salesforce',
      },
    },
    {
      metadata: {
        event_type: 'extraction_completed',
        job_id: sfParent,
        extraction_version: 1,
        source_type: 'salesforce',
      },
    },
    {
      metadata: {
        event_type: 'mapping_started',
        mapping_id: 'map-sf',
        job_id: sfParent,
      },
    },
  ];

  it('does not return unrelated SQL preview transform for SF mapping', () => {
    expect(resolveTransformedJobForMigration(history, 'map-sf', sfParent)).toBeNull();
  });

  it('resolveRecordsJobIdForMigration uses highest-version child in extraction chain', () => {
    expect(resolveRecordsJobIdForMigration(history, sfParent, 'map-sf')).toBe(sfChild);
  });
});
