import { buildMigrationDataFromPanel, executeChatMigration, handleMigrationStartError } from './executeChatMigration';
import { findMappingContextForMigration, hasUsableMapping } from './findMappingContextForMigration';
import { flushPersistMappingPanelEdit } from './persistMappingPanelEdit';
import { getMappingTransformation } from '../utils/migrationRulesKeys';

export async function handleAgentMigrateDataPrompt(deps, payload) {
  const {
    activeConversationId,
    chatHistoryRef,
    inlineDataPreviewsRef,
    mappingContextPersistTimersRef,
    mappingPanelsRef,
    migrationConfirmJobRef,
    migrationExecutionDeps,
    setChatHistory,
    setIsLoading,
    startLoadingMessages,
    stopLoadingMessages,
    updateMessageMetadata,
  } = deps;

  migrationConfirmJobRef.current = payload.job_id;
  let ctx = null;
  for (const delayMs of [0, 150, 400, 800]) {
    if (delayMs) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    ctx = findMappingContextForMigration({
      chatHistory: chatHistoryRef.current,
      mappingPanels: mappingPanelsRef.current,
      payload,
      inlineDataPreviews: inlineDataPreviewsRef.current,
    });
    if (ctx?.mappingData && hasUsableMapping(ctx.mappingData.mapping)) {
      break;
    }
  }
  if (!ctx?.mappingData || !hasUsableMapping(ctx.mappingData.mapping)) {
    const debugMigration =
      process.env.REACT_APP_MIGRATION_DEBUG === '1' ||
      process.env.REACT_APP_MIGRATION_DEBUG === 'true';
    if (debugMigration) {
      console.warn('[MIGRATION_DEBUG] start_migration: no mapping context', {
        payload,
        jobId: payload?.job_id,
        mappingId: payload?.mapping_id,
        panelKeys: Object.keys(mappingPanelsRef.current || {}),
      });
    }
    const hint =
      payload?.message ||
      'Mapping is not ready yet. Use **Start Migration** on the field mapping panel above.';
    setChatHistory((prev) => [
      ...prev,
      {
        id: `migration-hint-${Date.now()}`,
        type: 'system',
        text: hint,
        timestamp: Date.now(),
        metadata: { event_type: 'migration_start_blocked', job_id: payload?.job_id },
      },
    ]);
    stopLoadingMessages();
    setIsLoading(false);
    return;
  }

  setIsLoading(true);
  startLoadingMessages('Starting migration');
  try {
    if (
      activeConversationId &&
      ctx.entry?.id &&
      ctx.entry?.metadata &&
      ctx.entry.canUpdate !== false &&
      ctx.mappingData?.mapping
    ) {
      await flushPersistMappingPanelEdit({
        conversationId: activeConversationId,
        entryId: ctx.entry.id,
        entryMetadata: ctx.entry.metadata,
        panel: ctx.mappingData,
        updatedMapping: ctx.mappingData.mapping,
        transformationRules:
          ctx.mappingData.transformationRules ??
          getMappingTransformation(ctx.entry.metadata) ??
          { fields: [] },
        updateMessageMetadata,
        setChatHistory,
        mappingId: ctx.mappingId,
        timersRef: mappingContextPersistTimersRef,
      });
    }
    const migrationData = buildMigrationDataFromPanel({
      mappingData: ctx.mappingData,
      entry: ctx.entry,
      chatHistory: chatHistoryRef.current,
      activeConversationId,
    });
    await executeChatMigration(migrationExecutionDeps, migrationData, ctx.entry);
  } catch (error) {
    console.error('Agent start migration failed:', error);
    await handleMigrationStartError(migrationExecutionDeps, error);
  } finally {
    stopLoadingMessages();
    setIsLoading(false);
  }
}
