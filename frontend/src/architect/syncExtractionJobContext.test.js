import {
  syncMappingPanelRecordCount,
  ensureExtractionCompletedChatEntry,
} from './syncExtractionJobContext';

describe('syncExtractionJobContext', () => {
  it('syncMappingPanelRecordCount updates totalRecordCount only', () => {
    const setter = jest.fn((fn) =>
      fn({
        m1: { jobId: 'job-1', allRecords: [], totalRecordCount: 0 },
      }),
    );
    syncMappingPanelRecordCount('job-1', { totalRecordCount: 42 }, setter);
    const next = setter.mock.calls[0][0]({
      m1: { jobId: 'job-1', allRecords: [], totalRecordCount: 0 },
    });
    expect(next.m1.totalRecordCount).toBe(42);
    expect(next.m1.allRecords).toEqual([]);
  });

  it('ensureExtractionCompletedChatEntry adds metadata-only anchor', () => {
    const matches = (meta, jid) => meta?.job_id === jid;
    const setter = jest.fn((fn) => fn([]));
    ensureExtractionCompletedChatEntry({
      jobId: 'job-1',
      recordCount: 10,
      metadata: { source_type: 'sql' },
      setChatHistory: setter,
      metadataMatchesJobId: matches,
    });
    const next = setter.mock.calls[0][0]([]);
    expect(next).toHaveLength(1);
    expect(next[0].metadata.job_id).toBe('job-1');
    expect(next[0].metadata.record_count).toBe(10);
  });
});
