# Edison AI Frontend — Architect Architecture

**Status:** Canonical (2026-06)  
**Backend cross-link:** [`edison-ai/backend/docs/ARCHITECTURE.md`](../../edison-ai/backend/docs/ARCHITECTURE.md#session-first-architect-2026-06)

---

## Command vs event model

| Direction | Frontend sends | Backend returns |
|-----------|----------------|-----------------|
| User turn | `conversation_id`, `prompt`, optional `plan_auto_run`, slim `planner_resume` | SSE events (planner, extraction, mapping, migration) |
| Map / Re-map | `POST /conversations/{id}/actions/map\|remap` with `{ job_id?, destination_name?, destination_entity? }` | JSON panel descriptor + `message_id` |
| Start migration | `POST /conversations/{id}/actions/migrate` with `{ job_id?, mapping_message_id? }` | Migration SSE stream |
| Save flow | `POST /conversations/{id}/actions/save-flow` with `{ flow_name, batch_id? }` | `{ flow_id }` — backend resolves connectors/SOQL |
| Mapping edit | `PATCH /conversations/{id}/session/mapping-draft` (debounced 400ms) | Session draft only; migration context flushed server-side on migrate |

The frontend **does not** send `conversation_history`, reconstructed map/migrate payloads, or client-synthesized `intent_state` on architect streams.

---

## What lives where

| State | Owner |
|-------|--------|
| Chat UI list | React `chatHistory` (hydrated from `GET /chat/conversations/{id}/messages`) |
| Inline previews / mapping panels | React refs + SSE updates |
| Session pins, mapping draft | Backend Redis `ConversationSession` |
| Durable pipeline events | Postgres `chat_messages.metadata` |
| Extract/map/migrate execution | Backend `JobContext` + migration context at commit |

---

## Architect stream contract

```json
{
  "conversation_id": "uuid",
  "prompt": "string",
  "plan_auto_run": false,
  "planner_resume": {
    "approve_plan": true,
    "selections": {}
  }
}
```

`planner_resume` carries action flags and selections only — not full `planner_pending` blobs when session stores pending message id.

---

## SSE event catalog (architect / migration)

| `event_type` | UI handler |
|--------------|------------|
| `planner_plan_ready` | `plannerStreamHandlers.onPlannerPlanReady` — use `message_id` when present (skip duplicate persist) |
| `planner_clarification_required` | Clarification panel |
| `planner_recovery_required` | `plannerStreamHandlers.onPlannerRecoveryRequired` — UI only; backend persists the chat row |
| `extraction_job_created` / `extraction_completed` | Inline data preview |
| `mapping_started` | Written by map action API; `applyMappingStartedToChat` when `backendMessageId` set |
| `migration_started` | `applyMigrationStarted` — live status card + `migrationStatuses` (no message polling) |
| `migration_progress` | `applyMigrationProgress` — progress bar / counts |
| `migration_completed` | `applyMigrationCompletedFromStream` — merge persisted chat row from SSE `message` payload |
| `planner_recovery_required` | With persisted `message` object: `applyMigrationRecoveryFromStream`; string-only payloads: planner handlers |
| `migration_job_finished` | `applyMigrationFinished` — terminal counts on status card |

Handlers wired via `createMigrationStreamHandlers` in `applyMigrationStreamToChat.js` (migrate action API + architect stream).

### Migration SSE (two entry paths, one contract)

| Entry | Trigger | Stream |
|-------|---------|--------|
| **Panel** | `executeChatMigration` → `POST /conversations/{id}/actions/migrate` | Held-open migrate SSE until terminal |
| **Agent plan** | `StartMigrationTool` → `AgentRuntime._stream_migration_job_sse` after `agent_step_completed` | Same events on architect SSE until terminal |

**Live progress:** `migration_progress` updates `migrationStatuses` (no `GET /job-status`, no client persist).

**Terminal chat merge (primary):** Backend waits for DB-persisted rows, then emits on SSE:

1. `migration_completed` with `message: { id, content, metadata, created_at }`
2. `planner_recovery_required` with `message` (when failures warrant recovery)
3. `migration_job_finished` with `job_status` (status card counts)

Frontend merges chat from SSE via `applyMigrationCompletedFromStream` / `applyMigrationRecoveryFromStream`. **`applyMigrationStarted` does not poll messages.**

**Fallbacks only** (no polling during an active SSE stream):

| Helper | When |
|--------|------|
| `syncMigrationTerminalFromServer` | Page reload / in-flight resume; migrate JSON fallback; `applyMigrationFinished` if SSE did not deliver `migration_completed` |
| `syncPendingMigrationJobsAfterArchitectStream` | Architect stream had `migration_started` but no `migration_job_finished` |

The frontend **does not** poll `GET /job-status` for migrations or client-persist `migration_completed`.

Extraction fallback when architect SSE ends without `extraction_job_finished` (rare): **`syncExtractionPreviewFromServer`** in `recoverExtractionPreviewFromServer.js` loads `/extraction-results/{job_id}` + paginated records — not `GET /job-status`. Used by `pollJobForInlinePreview` and `recoverCompletedExtractionsFromActiveJobs`.

Mapping panel lazy-load (old threads without `mapping_config`): **`refetchMappingViaActionApi`** via `POST /conversations/{id}/actions/map` — not raw `POST /map-fields`.

---

## Key modules

| Module | Role |
|--------|------|
| `hooks/useArchitectStream.js` | POST `/architect/stream`, SSE dispatch |
| `hooks/usePlannerResume.js` | Run plan / clarification resume |
| `architect/runMapFieldsForJob.js` | Map via action API; `refetchMappingViaActionApi` for lazy-load |
| `architect/recoverExtractionPreviewFromServer.js` | Extraction preview merge when SSE drops |
| `architect/runMigrationStream.js` | Read migrate-action SSE; dispatch progress/terminal events |
| `architect/executeChatMigration.js` | Migrate via action API |
| `architect/applyMigrationStreamToChat.js` | Migration SSE handlers; SSE terminal merge + fallback sync |
| `architect/persistMappingPanelEdit.js` | Draft PATCH to session (not migration context on keystroke) |
| `architect/plannerStreamHandlers.js` | Planner SSE → chat UI |

---

## Deprecated / removed (chat architect)

- `conversation_history` on architect requests
- `mergeMappingPanelsIntoConversationHistory` (removed — session mapping draft is source of truth)
- Client `intentState.js` synthesis for backend requests (display helpers may remain)
- Migration `GET /job-status` polling and client-side `migration_completed` persist
- Polling `GET /chat/conversations/{id}/messages` during live migration (terminal merge is SSE-first; message fetch is reload/fallback only)
- Extraction `GET /job-status` interval polling (`pollJobForInlinePreview` 2s loop)
- Per-edit `PATCH /migrations/context` from mapping panel (draft + migrate flush only)
- Legacy `/migrate-to-*` POST bodies from chat mapping panel (use `POST /conversations/{id}/actions/migrate`)
- `planner_recovery_building` ephemeral chat rows (recovery merges from persisted messages)

Legacy `/map-fields` and `/migrate-to-*` endpoints remain for scripts and saved flows outside chat.
