jest.mock('../api', () => ({ __esModule: true, default: { get: jest.fn() } }));
jest.mock('./fetchExtractionPreviewRecords', () => ({
  fetchExtractionPreviewRecords: jest.fn(),
}));

import api from '../api';
import { fetchExtractionPreviewRecords } from './fetchExtractionPreviewRecords';
import {
  loadCompletedExtractionTaskData,
  syncExtractionPreviewFromServer,
} from './recoverExtractionPreviewFromServer';

describe('loadCompletedExtractionTaskData', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns null when jobId is missing', async () => {
    expect(await loadCompletedExtractionTaskData(null)).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
  });

  it('builds synthetic completed task from extraction-results and paginated records', async () => {
    api.get.mockResolvedValue({
      data: {
        result: {
          source_type: 'sql',
          document_type: 'Contact',
          document_description: 'Contacts',
          source_name: 'Test DB',
          destination: 'salesforce',
          destination_name: 'SF Prod',
          record_count: 2,
          extraction_metadata: JSON.stringify({
            sql_query: 'SELECT * FROM users',
            table_name: 'users',
            parent_job_id: 'parent-1',
          }),
        },
      },
    });
    fetchExtractionPreviewRecords.mockResolvedValue({
      records: [{ Email: 'a@b.com' }, { Email: 'c@d.com' }],
      totalCount: 2,
    });

    const task = await loadCompletedExtractionTaskData('job-1', { maxAttempts: 1 });

    expect(api.get).toHaveBeenCalledWith('/extraction-results/job-1');
    expect(fetchExtractionPreviewRecords).toHaveBeenCalledWith('job-1', {
      maxAttempts: 1,
      initialDelayMs: 0,
    });
    expect(task).toMatchObject({
      job_id: 'job-1',
      status: 'completed',
      source_type: 'sql',
      parent_job_id: 'parent-1',
      result: {
        status: 'success',
        total_records: 2,
        record_count: 2,
        sql_query: 'SELECT * FROM users',
        table_name: 'users',
        document_type: 'Contact',
      },
    });
    expect(task.result.data).toHaveLength(2);
  });

  it('accepts zero-record extraction as success', async () => {
    api.get.mockResolvedValue({
      data: {
        source_type: 'salesforce',
        document_type: 'Contact',
        record_count: 0,
      },
    });
    fetchExtractionPreviewRecords.mockResolvedValue({ records: [], totalCount: 0 });

    const task = await loadCompletedExtractionTaskData('job-empty', { maxAttempts: 1 });

    expect(task.status).toBe('completed');
    expect(task.result.total_records).toBe(0);
    expect(task.result.data).toEqual([]);
  });

  it('returns null when extraction-results is not ready', async () => {
    api.get.mockRejectedValue({ response: { status: 404 } });

    expect(await loadCompletedExtractionTaskData('job-missing', { maxAttempts: 1 })).toBeNull();
  });
});

describe('syncExtractionPreviewFromServer', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns false when jobId or applyTaskJobStatusToUi is missing', async () => {
    expect(
      await syncExtractionPreviewFromServer({
        jobId: null,
        applyTaskJobStatusToUi: jest.fn(),
        resolvePreviewSourceType: () => 'sql',
      }),
    ).toBe(false);
    expect(
      await syncExtractionPreviewFromServer({
        jobId: 'j1',
        applyTaskJobStatusToUi: null,
        resolvePreviewSourceType: () => 'sql',
      }),
    ).toBe(false);
  });

  it('calls applyTaskJobStatusToUi with resolved source type', async () => {
    api.get.mockResolvedValue({
      data: { source_type: 'google_sheets', record_count: 1 },
    });
    fetchExtractionPreviewRecords.mockResolvedValue({
      records: [{ Name: 'Acme' }],
      totalCount: 1,
    });

    const applyTaskJobStatusToUi = jest.fn().mockResolvedValue(undefined);
    const resolvePreviewSourceType = jest.fn((raw, hint) => hint || 'gsheets');

    const ok = await syncExtractionPreviewFromServer({
      jobId: 'job-gs',
      conversationId: 'conv-1',
      sourceTypeHint: 'gsheets',
      applyTaskJobStatusToUi,
      resolvePreviewSourceType,
      maxAttempts: 1,
    });

    expect(ok).toBe(true);
    expect(resolvePreviewSourceType).toHaveBeenCalledWith('google_sheets', 'gsheets');
    expect(applyTaskJobStatusToUi).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: 'job-gs', status: 'completed' }),
      'gsheets',
      'conv-1',
    );
  });

  it('dedupes concurrent sync for the same conversation and job', async () => {
    let resolveGet;
    api.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveGet = () =>
            resolve({ data: { source_type: 'sql', record_count: 1 } });
        }),
    );
    fetchExtractionPreviewRecords.mockResolvedValue({
      records: [{ id: 1 }],
      totalCount: 1,
    });

    const applyTaskJobStatusToUi = jest.fn().mockResolvedValue(undefined);
    const first = syncExtractionPreviewFromServer({
      jobId: 'job-dedup',
      conversationId: 'conv-dedup',
      applyTaskJobStatusToUi,
      resolvePreviewSourceType: () => 'sql',
      maxAttempts: 1,
    });
    const second = syncExtractionPreviewFromServer({
      jobId: 'job-dedup',
      conversationId: 'conv-dedup',
      applyTaskJobStatusToUi,
      resolvePreviewSourceType: () => 'sql',
      maxAttempts: 1,
    });

    resolveGet();
    const [a, b] = await Promise.all([first, second]);

    expect(a).toBe(true);
    expect(b).toBe(true);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(applyTaskJobStatusToUi).toHaveBeenCalledTimes(1);
  });
});
