# Edison AI — Module Map

**Canonical companion to [`ARCHITECTURE.md`](./ARCHITECTURE.md).**  
Continuation LLM-first implementation plan: [`CONTINUATION_PLANNING_KERNEL.md`](./CONTINUATION_PLANNING_KERNEL.md).  
Use this file to find where behavior lives before editing.

---

## Repositories

| Path | Stack | Entry |
|------|-------|-------|
| `edison-ai/backend/` | Python / FastAPI | `main.py`, `salesforce_architect_agent.py` |
| `edisonai-frontend/` | React | `src/ChatUploader.js`, `src/architect/` |
| `edison-ai/e2e/` | Playwright | `tests/planner-panels.spec.ts` |

---

## Backend entry points

| Entry | File | Role |
|-------|------|------|
| HTTP stream | `main.py` | `POST /architect/stream`, `planner_resume`, `plan_auto_run` |
| Architect stream | `salesforce_architect_agent.py` | `_emit_plan_or_route`, plan approve/resume branches |
| Classify turn | `services/planning/clarification_turn.py` | `classify_architect_turn`: Gate 0 → `classify_and_enrich` |
| Classification | `handlers/classification_handler.py` | Thin wrapper → `classify_and_enrich` |
| Classify + route | `services/intent/classify_and_enrich.py` | Gate 0 pipeline → `resolve_pipeline_turn` → planning / job / fresh branches |
| Pipeline turn routing | `services/intent/pipeline_turn_routing.py` | LLM-only: `planning_continuation` \| `job_continuation` \| `fresh` |
| Pipeline turn helpers | `services/intent/pipeline_turn.py` | `continue_planning_from_prompt`, fresh supersede metadata |
| Fresh planning | `services/planning/orchestrator.py` | `enrich_classification_with_planning` |
| Continuation planning | `services/intent/continuation_planning.py` | `plan_from_job_context` |
| Execution | `agent/runtime.py` | `AgentRuntime.run_stream` |
| Plan → runtime | `services/planning/plan_runner.py` | `agent_plan_from_execution_plan`, step arg refresh |
| Runtime planner | `agent/planner.py` | Loads approved `execution_plan` from classification |

---

## Intent (`services/intent/`)

| Module | Responsibility |
|--------|----------------|
| `classify_and_enrich.py` | Gate 0 pipeline entry; `resolve_pipeline_turn` → planning / job / fresh; S01 fallback; bootstrap → planning enrich |
| `pipeline_turn_routing.py` | Gather turn context; `PIPELINE_TURN_ROUTING` LLM; parse branch (no code overrides) |
| `pipeline_turn.py` | `continue_planning_from_prompt`, `supersede_pending_for_fresh_turn` |
| `routing_context.py` | Shared dialogue context for Gate 0, S01, pipeline turn, PLAN_SCOPE prompts |
| `session_continuation.py` | S01 LLM: fresh vs `continue_active_job`, phase |
| `continuation_planning.py` | Continuation router — `plan_from_job_context` → `planning_kernel` |
| `job_context.py` | `JobContext`, loader from extraction results + migration context |
| `route_turn.py` | `continuation_path` vs `fresh_path` |
| `pipeline.py` | S01–S12 intent pipeline (**fresh path only**) |
| `enrich.py` | Fresh S01 bootstrap: intent pre → orchestrator → intent post |
| `apply.py` | Pre/post planning intent overlay |
| `project.py` | `IntentState` → classification projection |

### Intent steps (`services/intent/steps/`)

| File | Step |
|------|------|
| `context.py` | S01 context |
| `turn_type.py` | S02 turn type (+ S03 goal summary) |
| `destination_need.py` | S04 destination need |
| `bind_resources.py` | S06 — bind catalog connector pins to IntentState |
| `entity.py` | S07 entity |
| `field_scope.py` | S08 fields |
| `gaps.py` | S10 gaps |

---

## Planning (`services/planning/`)

| Module | Responsibility |
|--------|----------------|
| `goal_parser.py` | PLAN_SCOPE LLM (`enrich_scope_from_llm`, `enrich_continuation_scope_from_llm`) |
| `plan_scope.py` | `_plan_scope` read/write, binding requirements |
| `intent_routing.py` | Classification-based routing (`classification_is_general_turn`, info enrichment) |
| `conversation_session.py` | Session history helpers (`latest_extraction_completed_metadata`, session inherit) |
| `continuation_policy.py` | Continuation steps, `apply_job_destination_pins`, endpoint pins |
| `connector_llm_resolver.py` | Catalog LLM picks: source, destination, info |
| `source_catalog_validate.py` | `read_needs_source`, source pin validation |
| `source_llm_resolve.py` | `resolve_source_connector_via_llm` |
| `destination_catalog_validate.py` | `read_needs_destination`, destination pin validation |
| `destination_llm_resolve.py` | `resolve_destination_connector_via_llm` |
| `planning_kernel.py` | Source + destination catalog LLM → validate → `advance_planner_pipeline` |
| `info_entity_enrich.py` | Info-path entity hints from semantics (not connector binding) |
| `plan_builder.py` | `build_plan_steps`, `finalize_plan_status` |
| `pipeline_step_resolver.py` | Mechanical op ordering → `pipeline_steps` / `pipeline_label` metadata |
| `operation_resolver.py` | Op × connector → `tool_id` |
| `op_requirements.py` | Slot requirements per pipeline op (`derive_plan_requirements`); `CREATE_*` slots use `"<compose>"` |
| `clarification_compose.py` | LLM dialogue for open slots — prompt, `context_summary`, suggestion chips |
| `create_metadata.py` | Spreadsheet title policy; `planner_spreadsheet_title_confirmed`, generic title rejection |
| `source_column_headers.py` | PLAN_SCOPE `spreadsheet_header_source`; CREATE_HEADERS slot satisfaction; job column resolver |
| `clarification_pipeline.py` | `advance_planner_pipeline` |
| `clarification_turn.py` | Architect entry; `try_resume_clarification_from_prompt` (called from `pipeline_turn`) |
| `clarification_apply.py` | Apply user selections; `build_planner_pending_payload` |
| `plan_context.py` | `latest_clarification_pending_from_history`, reply heuristics |
| `planner_path.py` | `classification_has_plan_scope` — blocks legacy shortcuts |
| `schemas.py` | Planning `ExecutionPlan`, `merge_plan_into_classification` |
| `orchestrator.py` | Fresh-path planner orchestration |
| `plan_runner.py` | Runtime step execution |
| `observability.py` | `[planning]` structured logs |
| `rollout.py` | Capability flags (`planning_pipeline: on` — not env-gated) |
| `connector_guards.py` | Endpoint resolved checks |
| `entity_resolver.py` | Entity clarifications |
| `gap_detector.py` | Connector/entity gap clarifications; create slots delegated to `op_requirements` |
| `map_flow_debug.py` | `[map_flow]` continuation debug events |

### Plan binding (`services/planning/binding/`)

| File | Role |
|------|------|
| `pipeline.py` | `run_plan_binding_pipeline` |
| `ensure.py` | `ensure_plan_binding_if_needed` |
| `bind_source.py` | PLAN_BIND_SOURCE LLM |
| `bind_destination.py` | PLAN_BIND_DESTINATION LLM |
| `retrieve.py` | Entity shortlist |
| `validate.py` | Ground LLM picks against catalog |
| `guards.py` | Binding authoritative vs legacy |

### Extract contracts

| File | Connector |
|------|-----------|
| `extract_contract_from_classification.py` | Authoritative contract builder |
| `soql_extract_contract.py` | Salesforce |
| `sql_extract_contract.py` | SQL |
| `gsheets_extract_contract.py` | Google Sheets |
| `related_select_fields.py` | Related entity fields (SF/SQL) |

---

## Agent (`agent/`)

| Module | Role |
|--------|------|
| `runtime.py` | Step loop, tool dispatch, SSE |
| `planner.py` | Build runtime plan from approved execution_plan |
| `plan.py` | Runtime `ExecutionPlan` type |
| `rollout.py` | Agent runtime capability logging (`log_agent_runtime_rollout_status`) |
| `session.py` | `AgentSession` from classification |
| `events.py` | SSE event helpers |
| `tools/map_migrate_tools.py` | `map_fields`, `start_migration` |
| `tools/extract.py` | Extract per connector |
| `tools/architect_routes.py` | Legacy single-tool extract routes |
| `tools/data_ops.py` | `run_data_ops_plan` → `post_extract/` orchestrator |
| `recovery_flow.py` | Partial migration retry |

---

## Migration (`services/migration/`)

| Module | Role |
|--------|------|
| `migration_service.py` | Orchestration |
| `sql_runner.py` / `gsheets_runner.py` | Connector-specific migrate |
| `mapping_readiness.py` | Job mapping state |
| `context_repository.py` | `conversation_migration_contexts` |

---

## Frontend (`edisonai-frontend/`)

| Path | Role |
|------|------|
| `src/ChatUploader.js` | Main architect chat: history, send, panel orchestration |
| `src/hooks/useArchitectStream.js` | POST `/architect/stream` SSE read loop |
| `src/hooks/usePlannerResume.js` | Build `planner_resume` payloads |
| `src/architect/plannerState.js` | `planner_pending` ref, restore from loaded messages |
| `src/architect/plannerStreamHandlers.js` | SSE → chat entries for planner events |
| `src/architect/intentState.js` | Merge `intent_state` into pending |
| `src/architect/architectSse.js` | Parse/filter internal SSE from transcript |
| `src/architect/dispatchArchitectSseEvents.js` | Execution events → inline UI |
| `src/components/PlannerPlanPanel.js` | Execution plan + Run plan / Dismiss |
| `src/components/PlannerClarificationPanel.js` | Clarification options + Continue |
| `src/components/PlannerSchemaGatePanel.js` | Schema deploy gate |
| `src/components/PlannerRecoveryPanel.js` | Migration recovery |
| `src/components/DestinationTargetPicker.js` | Inline destination picker (post-extract map) |
| `src/components/ChatInputBar.js` | Ask / Act toggle (`plan_auto_run`) |
| `src/components/InlineDataPreview.js` | Post-extract preview + destination pick |
| `src/components/InlineMappingPanel.js` | Field mapping UI |
| `src/utils/destinationTargetOptions.js` | REST fetch connections + metadata |

### Frontend planner SSE events (handled before generic dispatch)

| Event | Panel / effect |
|-------|----------------|
| `planner_clarification_required` | `PlannerClarificationPanel` |
| `planner_plan_ready` | `PlannerPlanPanel` when `awaiting_approval` |
| `planner_schema_deploy_required` | `PlannerSchemaGatePanel` |
| `planner_recovery_required` | `PlannerRecoveryPanel` |
| `map_fields_ready` | Inline mapping panel |
| `extraction_job_finished` | Inline data preview |
| `agent_turn_completed` | Turn end signal (no `{ status, result }` envelope) |

**Do not emit** at turn end: `{ status: success, job_id, result: last_result }` — lifecycle events are delivered during live tool streams only.

---

## LLM task registry

`services/llm/tasks.py` — task names for `async_openai_call(..., task=...)`.

---

## Key tests

| Area | Test files |
|------|------------|
| Continuation map dest | `planning_kernel` / `advance_planner_pipeline` |
| Continuation routing | `tests/test_continuation_path.py`, `tests/test_continuation_compound.py` |
| Plan runner | `tests/test_plan_runner.py` |
| Single planning path | `tests/test_planner_single_path.py` |
| Architect turn routing | `tests/test_architect_turn_routing.py` |
| Binding | `tests/test_plan_binding.py` |
| Intent pipeline | `tests/test_intent_pipeline.py` |
| E2E panels | `edison-ai/e2e/tests/planner-panels.spec.ts` |

---

## Removed / do not extend

| Module | Notes |
|--------|-------|
| `agent/universal_runtime.py`, `dynamic_runtime.py`, `plan_suggester` | Deleted — plan-scoped `AgentRuntime` only |
| ~~`handlers/post_extract_operations.py`~~ | Removed — data ops via `run_data_ops_plan` / `post_extract/` |
| `resolve_destination_from_prompt()` | Deleted — use PLAN_SCOPE + `resolve_endpoints` |
| `prompt_requests_same_endpoint()` | Deleted — use `destination_same_as_source` |
| `planning/endpoint_resolver.py` | Label matching only; no regex migrate intent |
| `intent/steps/bind_resources.py` | S06 classification-pin bind (no prompt regex) |
| `handlers/google_sheets_v2_router.py` | **Deleted** — GSheets info via `google_sheets_info`; pipeline ops via PLAN_SCOPE |
| `google_sheets_smart_add_update` / `streams_upsert.py` | **Deleted** — upsert via `gsheets_execute_operation` |
| `planning/google_sheets_phrase_match.py` | **Deleted** — catalog exact match + LLM binding only |
| `extract_info_connector_phrase()` | **Deleted** — info path uses `llm_resolve_info_connector` |
| `workbook_mentioned_as_destination()` on multi_source | **Removed** — PLAN_SCOPE `extract_sources[]` excludes destination from source list |
| `detect_google_sheet_from_prompt` on executor path | **Removed** — `context_resolver` uses classification names + URL |
| `llm/tasks` legacy `plan_goal` DB key | Historical rows only — invocations use `PLAN_SCOPE` |
| `docs/PLANNER_V2_USER_GUIDE.md` | **Removed** — superseded by this doc set |

---

## Adding a feature (checklist)

1. Read `ARCHITECTURE.md` — fresh vs continuation? For continuation work, read `CONTINUATION_PLANNING_KERNEL.md` (phase + current vs target).
2. Ops change → extend PLAN_SCOPE prompt in `goal_parser.py` (separate fresh vs continuation prompts).
3. Destination **connector** (org / DB / GS account) → `llm_resolve_destination_connector` → `validate_destination_connector_pins`. GS **workbook/tab** → entity slot (`pick_workbook`, `resolve_and_apply_google_sheets_workbook`) — not connector LLM.
4. Destination entity (SF/SQL object/table) → `run_plan_binding_pipeline` / `advance_planner_pipeline` — not `map_continuation_destination` (legacy).
5. New tool → `operation_resolver.py` + `agent/tools/`.
6. UI panel or resume → frontend `usePlannerResume` + backend SSE in `agent/events.py`.
7. Add test in closest `tests/test_*` or `e2e/`.
8. Run `scripts/audit_keyword_routing.py` after routing or clarification copy changes.
