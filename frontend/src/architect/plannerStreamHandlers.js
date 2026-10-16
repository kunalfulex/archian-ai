/**
 * SSE / stream callbacks when planner events arrive from the architect API.
 */
import { createEmptyOperationPreview } from './reduceOperationPreview';
import { createEmptyPlanningActivity } from './reducePlanningActivity';
import { enrichPlannerPendingWithIntentState } from './intentState';
import {
  freezeOlderPlannerEntries,
  resolveFreshSupersededPlannerEntries,
  resolvePlannerEntriesAfterPipeline,
  schedulePersistPlannerResolved,
} from './plannerState';
import {
  buildPlannerPlanReadyMarkdown,
  formatPlanEntityExecutionSide,
  resolvePlannerDestinationDisplay,
  resolvePlannerPlanContract,
} from './plannerPlanDisplay';

const EXECUTING_PLAN_STATUSES = new Set(['approved', 'running', 'completed']);

export { formatPlanEntityExecutionSide };

export function buildPlannerPlanReadyText(steps, destinationMeta = {}) {
  const classification = destinationMeta.classification || {};
  const pending = destinationMeta.pending || { classification };
  const display = resolvePlannerDestinationDisplay(
    {
      plan_contract: destinationMeta.plan_contract,
      plan_destination: destinationMeta.plan_destination,
      destination_name: destinationMeta.destination_name,
      destination_sheet_name: destinationMeta.destination_sheet_name,
      mode: destinationMeta.mode,
      source_name: destinationMeta.source_name,
      source_entity_key: destinationMeta.source_entity_key,
      execution_plan: destinationMeta.execution_plan,
    },
    pending,
    steps,
  );
  return buildPlannerPlanReadyMarkdown(steps, display);
}

export function createPlannerStreamHandlers({
  plannerPendingRef,
  setChatHistory,
  setIsLoading,
  stopLoadingMessages,
  setAgentTurnSteps,
  setOperationPreview,
  setPlanningActivity,
  persistMessage,
  updateMessageMetadata,
}) {
  const clearStreamUi = () => {
    setIsLoading(false);
    stopLoadingMessages();
    setAgentTurnSteps([]);
    setOperationPreview?.(createEmptyOperationPreview());
    setPlanningActivity?.(createEmptyPlanningActivity());
  };

  return {
    onFreshSupersedesPending: async (_data, conversationId) => {
      plannerPendingRef.current = null;
      setChatHistory((prev) => {
        const next = resolveFreshSupersededPlannerEntries(prev);
        schedulePersistPlannerResolved(conversationId, next, updateMessageMetadata);
        return next;
      });
    },

    onPlannerPlanReady: async (data) => {
      const pending = enrichPlannerPendingWithIntentState(
        data.planner_pending || {
          original_prompt: data.original_prompt,
          classification: data.classification,
          execution_plan: data.execution_plan,
          plan_id: data.plan_id,
        },
      );
      plannerPendingRef.current = pending;

      const steps = data.steps || pending.plan_steps_summary || [];
      const classification = pending.classification || data.classification || {};
      const planContract =
        resolvePlannerPlanContract(
          {
            plan_contract: data.plan_contract || classification.plan_contract,
            execution_plan: pending.execution_plan || data.execution_plan,
          },
          pending,
        ) || data.plan_contract || classification.plan_contract;
      const display = resolvePlannerDestinationDisplay(
        {
          plan_contract: planContract,
          plan_destination: data.plan_destination || classification.plan_destination,
          destination_name: data.destination_name || classification.destination_name,
          destination_sheet_name:
            data.destination_sheet_name || classification.destination_sheet_name,
          destination: data.destination || classification.destination,
          mode: classification.mode,
          source_name: classification.source_name,
          execution_plan: pending.execution_plan || data.execution_plan,
        },
        pending,
        steps,
      );
      const destinationMeta = {
        classification,
        pending,
        ...display,
      };
      const status = (data.plan_status || 'awaiting_approval').toLowerCase();
      if (EXECUTING_PLAN_STATUSES.has(status)) {
        setPlanningActivity?.(createEmptyPlanningActivity());
        return null;
      }

      const text = buildPlannerPlanReadyText(steps, destinationMeta);
      const entryId = data.message_id || `planner-plan-${Date.now()}`;

      setChatHistory((prev) => [
        ...freezeOlderPlannerEntries(prev),
        {
          id: entryId,
          type: 'system',
          text,
          timestamp: Date.now(),
          metadata: {
            event_type: 'planner_plan_ready',
            planner_pending: pending,
            plan_id: data.plan_id || pending.plan_id,
            plan_steps_summary: steps,
            plan_status: data.plan_status || 'awaiting_approval',
            goal_summary: data.goal_summary || classification.goal_summary,
            plan_contract: destinationMeta.planContract,
            plan_destination: destinationMeta.destination,
            destination_name: destinationMeta.destinationName,
            destination_sheet_name: destinationMeta.destinationSheetName,
            destination: destinationMeta.destination,
            mode: destinationMeta.mode,
            source_name: destinationMeta.sourceName,
            source_entity_key: destinationMeta.sourceEntityKey,
            ...(pending.intent_state ? { intent_state: pending.intent_state } : {}),
          },
        },
      ]);

      if (status === 'awaiting_approval') {
        clearStreamUi();
        return '';
      }
      return null;
    },

    onSpreadsheetCreated: async (data, conversationId) => {
      const name = data.spreadsheet_name || data.destination_name || 'Spreadsheet';
      const url =
        data.spreadsheet_url ||
        (data.spreadsheet_id
          ? `https://docs.google.com/spreadsheets/d/${data.spreadsheet_id}/edit`
          : null);
      const lines = ['**Spreadsheet created successfully!**', '', `**Name**: ${name}`];
      if (url) {
        lines.push('', `**View Spreadsheet**: [Open in Google Sheets](${url})`);
      }
      const text = lines.join('\n');
      plannerPendingRef.current = null;
      setChatHistory((prev) => {
        const already = prev.some(
          (e) =>
            e.metadata?.event_type === 'spreadsheet_creation_completed' &&
            e.metadata?.spreadsheet_id === data.spreadsheet_id,
        );
        const base = already
          ? prev
          : [
              ...prev,
              {
                id: `spreadsheet-created-${data.spreadsheet_id || Date.now()}`,
                type: 'system',
                text,
                timestamp: Date.now(),
                metadata: {
                  event_type: 'spreadsheet_creation_completed',
                  spreadsheet_id: data.spreadsheet_id,
                  spreadsheet_name: name,
                  spreadsheet_url: url,
                  destination: data.destination || 'google_sheets',
                  destination_name: data.destination_name || name,
                },
              },
            ];
        const next = resolvePlannerEntriesAfterPipeline(base);
        schedulePersistPlannerResolved(conversationId, next, updateMessageMetadata);
        return next;
      });
    },

    onSchemaGateProposal: async (data, conversationId) => {
      const proposal = {
        object_api_name: data.object_api_name,
        fields_missing_on_destination: data.fields_missing_on_destination || [],
        deploy_recommended: data.deploy_recommended,
      };
      const missing = proposal.fields_missing_on_destination;
      const obj = proposal.object_api_name || 'Object';
      const text =
        missing.length > 0
          ? `**Schema review — ${obj}**: ${missing.length} custom field(s) missing on the destination org.`
          : `**Schema review — ${obj}**: destination schema looks aligned.`;

      const entryId = `schema-gate-${Date.now()}`;
      setChatHistory((prev) => [
        ...prev,
        {
          id: entryId,
          type: 'system',
          text,
          timestamp: Date.now(),
          metadata: {
            event_type: 'schema_gate_proposal',
            schema_gate_proposal: proposal,
          },
        },
      ]);

      if (conversationId) {
        await persistMessage(conversationId, 'assistant', text, {
          event_type: 'schema_gate_proposal',
          schema_gate_proposal: proposal,
        });
      }
      return '';
    },

    onPlannerSchemaDeployRequired: async (data, conversationId) => {
      const pending = data.planner_pending || {};
      plannerPendingRef.current = pending;
      const proposal = data.schema_proposal || pending.schema_proposal || {};
      const text =
        (data.message || '').trim() || 'Approve schema deploy to continue mapping.';
      const entryId = `schema-deploy-${Date.now()}`;

      setChatHistory((prev) => [
        ...prev,
        {
          id: entryId,
          type: 'system',
          text,
          timestamp: Date.now(),
          metadata: {
            event_type: 'planner_schema_deploy_required',
            schema_gate_proposal: proposal,
            planner_pending: pending,
            show_deploy_approval: true,
          },
        },
      ]);

      if (conversationId) {
        await persistMessage(conversationId, 'assistant', text, {
          event_type: 'planner_schema_deploy_required',
          schema_gate_proposal: proposal,
          planner_pending: pending,
        });
      }
      clearStreamUi();
      return '';
    },

    onPlannerClarificationRequired: async (data, conversationId) => {
      const pending = enrichPlannerPendingWithIntentState(
        data.planner_pending || {
          original_prompt: data.original_prompt,
          classification: data.classification || data,
          execution_plan: data.execution_plan,
          plan_id: data.plan_id,
          clarifications: data.clarifications,
          extract_step_clarification: data.extract_step_clarification,
          plan_execution_pending: data.plan_execution_pending,
        },
      );
      plannerPendingRef.current = pending;

      const clarifications = data.clarifications || pending.clarifications || [];
      const isExtractStep = Boolean(
        data.event_type === 'planner_plan_execution_clarification' ||
          pending.extract_step_clarification,
      );
      const text =
        (data.message || clarifications[0]?.prompt || '').trim() ||
        (isExtractStep
          ? 'Please provide more detail to continue the extraction.'
          : 'Please choose an option to continue.');
      const entryId = `planner-clar-${Date.now()}`;
      const eventType = isExtractStep
        ? 'planner_plan_execution_clarification'
        : 'planner_clarification_required';
      const metadata = {
        event_type: eventType,
        clarifications,
        planner_pending: pending,
        plan_id: data.plan_id || pending.plan_id,
        ...(pending.intent_state ? { intent_state: pending.intent_state } : {}),
      };

      setChatHistory((prev) => [
        ...freezeOlderPlannerEntries(prev),
        {
          id: entryId,
          type: 'system',
          text,
          timestamp: Date.now(),
          metadata,
        },
      ]);

      if (conversationId) {
        await persistMessage(conversationId, 'assistant', text, metadata);
      }

      clearStreamUi();
      // Continue draining SSE — classification_completed may precede this event in an
      // earlier chunk; aborting on '' used to drop _pending_file_ref from planner_pending.
      return null;
    },

    onPlannerRecoveryRequired: async (data, conversationId) => {
      const pending = data.planner_pending || {};
      const proposal = data.recovery_proposal || pending.recovery_proposal || {};
      const batchId = pending.recovery_batch_id || null;
      const text =
        (data.message || '').trim() || 'Review migration errors and approve a fix to continue.';

      let added = false;
      setChatHistory((prev) => {
        const exists = prev.some(
          (e) =>
            e.metadata?.event_type === 'planner_recovery_required' &&
            (!batchId ||
              e.metadata?.planner_pending?.recovery_batch_id === batchId),
        );
        if (exists) return prev;
        added = true;
        plannerPendingRef.current = pending;
        return [
          ...prev,
          {
            id: `recovery-${Date.now()}`,
            type: 'system',
            text,
            timestamp: Date.now(),
            metadata: {
              event_type: 'planner_recovery_required',
              recovery_proposal: proposal,
              planner_pending: pending,
              show_recovery_approval: Boolean(
                data.show_recovery_approval ?? proposal.requires_write,
              ),
            },
          },
        ];
      });

      if (!added && pending) {
        plannerPendingRef.current = pending;
      }
      clearStreamUi();
      return null;
    },
  };
}
