import {
  CHAT_MESSAGE_UUID_RE,
  dataPreviewEntryId,
  isPersistedChatMessageId,
  metadataWithExtractionMessageId,
  resolvePersistedExtractionMessageId,
} from './extractionMessageId';

describe('extractionMessageId', () => {
  const uuid = 'ba749d24-1727-4ad5-b935-b14f68309bcb';

  test('isPersistedChatMessageId rejects data-preview synthetic ids', () => {
    expect(isPersistedChatMessageId(`data-preview-${uuid}`)).toBe(false);
    expect(isPersistedChatMessageId(uuid)).toBe(true);
  });

  test('resolvePersistedExtractionMessageId prefers metadata.extraction_message_id', () => {
    expect(
      resolvePersistedExtractionMessageId({
        id: dataPreviewEntryId(uuid),
        metadata: {
          event_type: 'extraction_completed',
          job_id: uuid,
          extraction_message_id: '11111111-1111-4111-8111-111111111111',
        },
      }),
    ).toBe('11111111-1111-4111-8111-111111111111');
  });

  test('resolvePersistedExtractionMessageId falls back to UUID entry id', () => {
    expect(
      resolvePersistedExtractionMessageId({
        id: uuid,
        metadata: { event_type: 'extraction_completed', job_id: uuid },
      }),
    ).toBe(uuid);
  });

  test('resolvePersistedExtractionMessageId returns null for preview-only entry', () => {
    expect(
      resolvePersistedExtractionMessageId({
        id: dataPreviewEntryId(uuid),
        metadata: { event_type: 'extraction_completed', job_id: uuid },
      }),
    ).toBe(null);
  });

  test('metadataWithExtractionMessageId adds id when valid', () => {
    const out = metadataWithExtractionMessageId({ job_id: uuid }, uuid);
    expect(out.extraction_message_id).toBe(uuid);
    expect(CHAT_MESSAGE_UUID_RE.test(out.extraction_message_id)).toBe(true);
  });
});
