import { applyDestinationTargetToMetadata, classificationSnapshotFromDestinationTarget } from '../utils/destinationTargetOptions';
import { applyMappingStartedToChat } from './applyMappingStartedToChat';
import { dataPreviewEntryId, metadataWithExtractionMessageId, resolvePersistedExtractionMessageId } from './extractionMessageId';
import { metadataMatchesJobId } from './metadataJobId';
import { resolveExtractionCompletedEvent } from './resolveExtractionContextForSave';
import { runMapFieldsForJob } from './runMapFieldsForJob';

export async function handlePreviewDestinationPick(deps, jobId, destType, destName, destTarget) {
  const {
    activeConversationId,
    chatHistoryRef,
    inlineDataPreviewsRef,
    persistMessage,
    setChatHistory,
    setInlineDataPreviews,
    setIsLoading,
    setLoadingMessage,
    setMappingPanels,
    stopLoadingMessages,
    updateMessageMetadata,
  } = deps;

  const targetFields = classificationSnapshotFromDestinationTarget(destType, destTarget);
  setInlineDataPreviews((prev) => ({
    ...prev,
    [jobId]: {
      ...prev[jobId],
      destination: destType,
      destinationName: destName,
      requiresDestination: false,
      ...targetFields,
    },
  }));
  const extractionEvt =
    resolveExtractionCompletedEvent(chatHistoryRef.current, jobId) ||
    chatHistoryRef.current.find(
      (e) =>
        e.metadata?.event_type === 'extraction_completed' &&
        metadataMatchesJobId(e.metadata, jobId),
    );
  const previewEntryId = extractionEvt?.id || dataPreviewEntryId(jobId);
  const persistedMessageId = resolvePersistedExtractionMessageId(extractionEvt);
  const nextMetadata = metadataWithExtractionMessageId(
    applyDestinationTargetToMetadata(
      {
        ...(extractionEvt?.metadata || {}),
        destination: destType,
        destination_name: destName,
        requires_destination: false,
      },
      destType,
      destTarget,
    ),
    persistedMessageId,
  );

  if (activeConversationId && persistedMessageId) {
    try {
      await updateMessageMetadata(activeConversationId, persistedMessageId, nextMetadata);
    } catch (err) {
      console.warn('Destination pick saved locally; metadata persist failed:', err);
    }
  }

  setChatHistory((prev) =>
    prev.map((e) =>
      e.id === previewEntryId
        ? {
            ...e,
            metadata: nextMetadata,
          }
        : e,
    ),
  );

  const existingMappingEntry = [...chatHistoryRef.current]
    .reverse()
    .find(
      (e) =>
        e.metadata?.event_type === 'mapping_started' &&
        metadataMatchesJobId(e.metadata, jobId),
    );
  if (!existingMappingEntry) return;

  setLoadingMessage('Re-mapping fields…');
  setIsLoading(true);
  try {
    const mapResult = await runMapFieldsForJob({
      jobId,
      destination: destType,
      destinationName: destName,
      destTarget: destTarget,
      inlineDataPreviews: inlineDataPreviewsRef.current,
      chatHistory: chatHistoryRef.current,
      metadataMatchesJobId,
      conversationId: activeConversationId,
      remap: true,
    });
    await applyMappingStartedToChat({
      jobId,
      mapResult,
      activeConversationId,
      chatHistory: chatHistoryRef.current,
      setChatHistory,
      setMappingPanels,
      persistMessage,
      mappingInstruction: `Change destination to ${destName || destType}${
        destTarget ? ` · ${destTarget}` : ''
      }`,
      mappingVersion: (existingMappingEntry.metadata?.mapping_version || 1) + 1,
    });
  } catch (err) {
    console.error('Re-map after destination change failed:', err);
  } finally {
    stopLoadingMessages();
    setIsLoading(false);
  }
}
