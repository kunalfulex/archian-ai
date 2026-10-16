import {
  readinessCompleteMessage,
  readinessInProgressMessage,
  normalizePendingSync,
} from './connectorReadinessSse';

describe('normalizePendingSync', () => {
  it('includes trigger when present', () => {
    const out = normalizePendingSync({
      connectorType: 'sql',
      connectorInstanceId: 'abc',
      displayName: 'Test DB',
      trigger: 'resync',
    });
    expect(out.trigger).toBe('resync');
  });
});

describe('readinessInProgressMessage', () => {
  it('uses unified reading copy for metadata_sync on resync', () => {
    const msg = readinessInProgressMessage({
      trigger: 'resync',
      phase: 'metadata_sync',
      name: 'SF 1',
      metadataProgress: { processed: 12, total: 84 },
    });
    expect(msg).toBe('Archian AI is reading SF 1 (12/84)');
  });

  it('uses reading copy with count for metadata_sync on create', () => {
    const msg = readinessInProgressMessage({
      trigger: 'create',
      phase: 'metadata_sync',
      name: 'Test DB',
      metadataProgress: { processed: 3, total: 10 },
    });
    expect(msg).toBe('Archian AI is reading Test DB (3/10)');
  });

  it('shows percent when total is unavailable', () => {
    const msg = readinessInProgressMessage({
      trigger: 'resync',
      phase: 'metadata_sync',
      name: 'SF 1',
      metadataProgress: { percent: 70 },
    });
    expect(msg).toBe('Archian AI is reading SF 1 (70%)');
  });

  it('shows entity counts during entity_labeling', () => {
    const msg = readinessInProgressMessage({
      trigger: 'resync',
      phase: 'entity_labeling',
      name: 'SF 1',
      stats: { ready: 3, total: 120 },
    });
    expect(msg).toBe('Archian AI is reading entities for SF 1 (3/120)');
  });
});

describe('readinessCompleteMessage', () => {
  it('uses resync complete copy', () => {
    const msg = readinessCompleteMessage({
      trigger: 'resync',
      name: 'SF 1',
      connectorType: 'salesforce',
    });
    expect(msg).toContain('SF 1 is up to date');
  });

  it('uses create ready copy', () => {
    const msg = readinessCompleteMessage({
      trigger: 'create',
      name: 'Test DB',
      connectorType: 'sql',
    });
    expect(msg).toContain('ready to work with Test DB');
  });
});
