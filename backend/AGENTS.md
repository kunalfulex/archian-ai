# Agent instructions — Edison AI

When working on **intent, planning, classification, map, migrate, extract, or architect chat** in this monorepo:

## Required reading (in order)

1. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system behavior, diagrams, invariants
2. [`docs/MODULES.md`](docs/MODULES.md) — backend + frontend module map
3. [`docs/CONTINUATION_PLANNING_KERNEL.md`](docs/CONTINUATION_PLANNING_KERNEL.md) — continuation LLM-first kernel plan (separate routes, shared bind/clarify)

**Do not implement from old `*_IMPLEMENTATION_PLAN.md` or `*_PLAN.md` files** outside the docs above. They are stale and often contradict the current code.

**Naming:** Say **planning pipeline** / **execution plan**, not “Planner v2”. Logs use `edison.planning` / `[planning]` (legacy `planner_v2` is deprecated).

## Repositories

| Path | What to edit for |
|------|------------------|
| `edison-ai/backend/` | Intent, planning, binding, agent runtime, APIs |
| `edisonai-frontend/` | Chat UI, planner panels, Ask/Act, `planner_resume` |
| `edison-ai/e2e/` | Planner panel smoke tests |

## Backend rules

- **Pre-planner hooks** — no keyword `classification_is_general_turn` or `prompt_requests_same_workbook_destination`; Gate 0 `plan_route` + PLAN_SCOPE own routing.
- **Runtime session** — `classification_retains_active_job_at_execution` (not `prompt_requires_fresh_extraction_pipeline`).
- **Entry:** `classify_architect_turn` — **Gate 0** → `classify_and_enrich` (pipeline). On pipeline: **`resolve_pipeline_turn` LLM** picks `planning_continuation` | `job_continuation` | `fresh`. Clar answers use **`planning_continuation`** → `continue_planning_from_prompt` (with clar-reply routing). Job-scoped new goals use **`job_continuation`** → `plan_from_job_context`. See `docs/ROUTING_REFACTOR_PLAN.md`.
- **Do not change routing architecture** without user approval — see `.cursor/rules/planning-architecture-fixed.mdc`.
- **Fresh path:** S01 bootstrap → orchestrator → PLAN_SCOPE → `planning_kernel` (source + dest catalog LLM) → intent pipeline S07–S12 post.
- **Continuation path:** `plan_from_job_context` → shared `planning_kernel` (`advance_planner_pipeline`) with fresh. Keyword `apply_resource_hints` and `map_continuation_destination` are removed.
- **Continuation map:** PLAN_SCOPE → source/dest catalog LLM (when `needs_*`) → validate → `advance_planner_pipeline` / binding. **Do not re-pick connector** when prior turn left `awaiting_clarification` with connector already pinned.
- **Continuation migrate (`mapping_ready`):** `apply_job_destination_pins` from `JobContext` — do not re-resolve connector or replay chat `mapping_started`.
- **Same database / same org:** `destination_same_as_source` from PLAN_SCOPE; catalog destination LLM must return matching `workbook_name` / `display_name` — no mechanical job pins.
- **Migrate write mode:** duplicate-check fields on mapping → upsert for all destinations (`services/migration/write_policy.py`). Do not branch on same/cross connector or `same_entity_migration`.
- **Extract portability:** use resolved source/destination in extract prompts (`extract_field_scope.extract_portability_context`); do not add new `connectors_cross_system()` planning gates.
- **PLAN_SCOPE path:** no regex endpoint backfill (`merge_endpoint_signals`) or continuation regex destination picks when `_plan_scope` is present.
- **Conversational clarification:** `op_requirements` owns slots; `clarification_compose` generates question + chips; user confirms (especially spreadsheet title via `planner_spreadsheet_title_confirmed`). Do not add hardcoded create/title prompts outside `clarification_compose._fallback_prompt`.
- **Destination validation:** `destination_catalog_validate.py` — LLM/user pins must pass catalog membership. GSheets clarifications gated by `needs_google_sheets_destination_clarification` (PLAN_SCOPE slots), not prompt regex.
- **Fresh path source LLM:** when `needs_source`, `planning_kernel` runs `resolve_source_connector_via_llm` before destination bind.
- **Fresh path destination LLM:** when `needs_destination`, `planning_kernel` runs `resolve_destination_connector_via_llm` before `advance_planner_pipeline`.
- **Minimal diffs:** Match surrounding style; do not refactor unrelated code.

## Frontend rules

- Planner state lives in `metadata.planner_pending` on chat messages — backend clarification resume reads `conversation_history`; keep pending in history when fixing resume bugs.
- **Ask / Act** = `plan_auto_run` on `/architect/stream`.
- User-facing label is **“Execution plan”**, not “Planner v2”.
- Destination picker (`DestinationTargetPicker`) is inline post-extract; planner plan panel is separate.

## Tests

```bash
# Backend (after planner / continuation edits)
cd edison-ai/backend
python scripts/audit_keyword_routing.py
pytest tests/test_plan_scope_authority.py \
       tests/test_source_catalog_validate.py \
       tests/test_destination_catalog_validate.py \
       tests/test_planning_kernel.py \
       tests/test_conversation_turn_routing.py \
       tests/test_continuation_path.py \
       tests/test_continuation_planning_kernel_invariants.py \
       tests/test_planner_pipeline_e2e.py -q

# E2E planner panels (optional)
cd edison-ai/e2e && npx playwright test tests/planner-panels.spec.ts
```

## Servers

```bash
# Backend
cd edison-ai/backend
./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 \
  --ssl-keyfile certs/localhost.key --ssl-certfile certs/localhost.crt --reload

# Frontend (separate terminal)
cd edisonai-frontend && npm start
```
