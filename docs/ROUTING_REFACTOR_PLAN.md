# Routing refactor plan — align code with fixed architecture

**Status:** Complete (2026-06)  
**Approval:** User confirmed fixed architecture; refactor restores it without changing the model.

## Fixed model (unchanged)

| Step | Decision | Module |
|------|----------|--------|
| 1 | info / general / pipeline | `turn_type_routing.resolve_turn_type_routing` (Gate 0) |
| 2 | Pipeline turn (LLM only) | `pipeline_turn_routing.resolve_pipeline_turn` — gathers context, no code-side branch overrides |
| 2a | `planning_continuation` | `pipeline_turn.continue_planning_from_prompt` (`skip_reply_routing=False`) |
| 2b | `job_continuation` | `continuation_planning.plan_from_job_context` (direct from `classify_and_enrich`) |
| 2c | `fresh` | `supersede_pending_for_fresh_turn` when applicable → S01 + `route_planning_path` → orchestrator |
| 3 | Shared kernel | `planning_kernel` / `advance_planner_pipeline` / bind / assemble |

**Clarification loop:** Any entry path (fresh or job) may emit `planner_clarification_required`. Answering an open gap on the **next** turn is **`planning_continuation`**, not `job_continuation`.

## Drift removed (this refactor)

| Before | After |
|--------|-------|
| `try_resume_clarification_from_prompt` **before** Gate 0 | Planning continuation **after** Gate 0, inside pipeline branch |
| Gate 0 run twice (architect + `classify_and_enrich`) | Gate 0 once in `classify_architect_turn`; passed into `classify_and_enrich` |
| Fresh path leaves stale `planner_pending` visible | `supersede_pending_for_fresh_turn` flag when fresh wins over open clar |

## Phase 1 (this PR)

- [x] Cursor rule + this plan doc
- [x] `services/intent/pipeline_turn.py` — planning continuation + supersede helper
- [x] `classify_architect_turn` — Gate 0 → `classify_and_enrich` only
- [x] `classify_and_enrich` — skip duplicate Gate 0; planning continuation before S01
- [x] Update `ARCHITECTURE.md` diagram + `AGENTS.md`
- [x] Fix tests (`test_architect_turn_routing`, `test_create_sheet_routing`)

## Phase 2 (follow-up)

- [x] Unified `resolve_pipeline_turn` LLM (`services/intent/pipeline_turn_routing.py`)
- [x] Frontend: dismiss stale panel on `planner_pending_superseded` / `_fresh_supersedes_pending` via `classification_completed`
- [x] Remove routing use of `prompt_is_likely_clarification_reply` (heuristic fallback removed; parse-only paths unchanged)
- [x] Shrink `conversation_session.prompt_requests_*` on hot paths (already absent from `services/intent`; deferred cleanup in `conversation_session.py`)

## Phase 3 (follow-up)

- [x] Assembly dead-end fixes (`empty_plan_no_steps` when slots satisfied)
  - `sync_create_assembly_readiness` aligns CREATE_TITLE with `should_materialize_create_step`
- [x] Emit-path recovery (`plan_emit_recovery.try_recover_empty_plan_at_emit`) before `empty_plan_no_steps`
- [x] DB migration `add_pipeline_turn_routing_llm_task.sql`
- [x] Regression tests: create account → fresh migrate supersedes; typed clar answer continues plan (`test_phase3_assembly_regression.py`)

## Phase 4 (2026-06) — LLM-only pipeline turn

- [x] Remove pre-LLM exits from `resolve_pipeline_turn` (`ranked_slot_open`, `no_context`, `stalled_or_extract_pending`, empty prompt)
- [x] Remove post-LLM overrides (low-confidence flip, ranked-slot flip, `llm_unavailable` fallbacks)
- [x] Split `pipeline_turn_routing.py`: `gather_pipeline_turn_context` → `build_pipeline_turn_routing_prompt` → LLM → parse
- [x] User-prompt-first routing prompt (open clar / job / dismiss as background context)
- [x] `classify_and_enrich`: explicit `job_continuation` → `plan_from_job_context`; `skip_reply_routing=False` on planning path
- [x] `routing_context`: `plan_dismissed` in dialogue summaries
- [x] Tests updated (`test_pipeline_turn_routing`, `test_ranked_slot_continuation`, `test_conversation_turn_routing`)

## Invariants (must hold)

1. Explicit UI `planner_resume` with `selections` still bypasses free-text routing (`is_explicit_planner_ui_resume`).
2. Info/general turns never invoke planning continuation.
3. `resolve_pipeline_turn` is LLM-only — no keyword or flag-based branch overrides on the hot path.
4. Ranked-slot supersede guard lives in `continuation_policy.should_supersede_pending_for_fresh_turn`, not in turn routing.
5. `scripts/audit_keyword_routing.py` still passes.
