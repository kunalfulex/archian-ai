import {
  resolveExtractionCompletedEvent,
  resolveSourceSqlQuery,
  resolveSourceTypeForSave,
} from './resolveExtractionContextForSave';

const parentQuery = 'SELECT u.id FROM users u JOIN user_plans p ON u.id = p.user_id';

describe('resolveSourceSqlQuery', () => {

  it('prefers mapping_started sql_query over child job without query', () => {
    const q = resolveSourceSqlQuery({
      chatHistory: [],
      inlineDataPreviews: {
        child: { sqlQuery: null },
        parent: { sqlQuery: parentQuery },
      },
      sourceJobId: 'parent',
      recordsJobId: 'child',
      frontendMappingStartedEvent: {
        metadata: { sql_query: parentQuery, job_id: 'parent' },
      },
    });
    expect(q).toBe(parentQuery);
  });

  it('walks parent_job_id chain on extraction_completed metadata', () => {
    const q = resolveSourceSqlQuery({
      chatHistory: [
        {
          metadata: {
            event_type: 'extraction_completed',
            job_id: 'child',
            parent_job_id: 'parent',
          },
        },
        {
          metadata: {
            event_type: 'extraction_completed',
            job_id: 'parent',
            sql_query: parentQuery,
          },
        },
      ],
      inlineDataPreviews: {},
      sourceJobId: 'parent',
      recordsJobId: 'child',
    });
    expect(q).toBe(parentQuery);
  });

  it('reads sql from inline preview when events lack metadata', () => {
    const q = resolveSourceSqlQuery({
      chatHistory: [],
      inlineDataPreviews: { 'job-1': { query: parentQuery } },
      sourceJobId: 'job-1',
    });
    expect(q).toBe(parentQuery);
  });
});

describe('resolveSourceTypeForSave', () => {
  it('infers sql from destination when chat metadata is missing', () => {
    expect(
      resolveSourceTypeForSave({
        chatHistory: [],
        inlineDataPreviews: {},
        sourceJobId: 'e80c244e-bafe-4d4a-993c-2fdfb8ebadcd',
        destination: 'sql',
        extractionRow: { source_type: 'sql' },
      }),
    ).toBe('sql');
  });

  it('finds data_preview entries as extraction context', () => {
    const evt = resolveExtractionCompletedEvent(
      [
        {
          type: 'data_preview',
          jobId: 'job-1',
          metadata: {
            event_type: 'extraction_completed',
            job_id: 'job-1',
            source_type: 'sql',
            sql_query: parentQuery,
          },
        },
      ],
      'job-1',
    );
    expect(evt?.metadata?.source_type).toBe('sql');
    expect(evt?.metadata?.sql_query).toBe(parentQuery);
  });
});
