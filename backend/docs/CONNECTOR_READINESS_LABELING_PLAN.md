# Connector readiness & semantics-aware labeling — implementation plan

**Branch:** `bug/flowbugschatfixes`  
**Goal:** Unify **when** entity labeling is queued across OAuth reconnect, legacy analysis, and manual metadata sync — without changing **how** each connector is labeled (SF / SQL / GSheets prompts stay separate).

---

## Problem summary

| Symptom | Root cause |
|---------|------------|
| No “connector ready” email after SF OAuth reconnect | Email only sent from profile rollup; OAuth path never queues labeling when `category` is set but `connector_entity_semantics` is empty |
| UI shows “agent ready” but no email | Readiness SSE reads DB state; email path is decoupled and may be deduped or never triggered |
| Manual “Sync schema now” works better | Metadata adapters use `collect_entities_needing_labeling` (drift + stale/missing semantics); OAuth uses legacy gates |
| Legacy orgs stuck in `profile_rollup` | `get_readiness_state` counts only `connector_entity_semantics`; empty table → never `ready` |

**Non-goal:** One LLM prompt for all connectors. Scheduling is unified; execution stays in `EntityLabelingService` + `prompts.py`.

---

## Architecture target

```
Metadata sync completes (any entry point)
        │
        ▼
schedule_pending_entity_labeling()     ← NEW shared hook
  • drift_keys (optional, from adapter)
  • current_hashes (connector-specific)
  • legacy_keys (SF optional: category IS NULL)
  • collect_entities_needing_labeling()
        │
        ▼
schedule_entity_labeling_fire_and_forget()
        │
        ▼
EntityLabelingService.label_one_entity()
  • _load_salesforce_entity | _load_sql_entity | _load_gsheets_entity
  • build_salesforce_ | sql_ | gsheets_ entity_labeling_prompt
        │
        ▼
Profile rollup (after labeling batch)
        │
        ▼
check_and_notify_agent_ready() → send_connector_agent_ready_email
```

---

## Phase 0 — Shared scheduling hook (backend core)

**New file:** `backend/services/labeling/pending_labeling.py`

```python
async def schedule_pending_entity_labeling(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: dict,
    current_hashes: dict[str, str],
    drift_keys: list[str] | None = None,
    legacy_keys: list[str] | None = None,
    entity_hashes: dict[str, str] | None = None,  # defaults to subset of current_hashes
) -> int:
    """Return count of entity keys queued (0 if none)."""
```

**Behavior:**

1. Resolve `connector_instance_id` via `connector_ref`.
2. Call existing `collect_entities_needing_labeling(...)`.
3. If non-empty, `schedule_entity_labeling_fire_and_forget(...)` with hashes.
4. Log at info: `connector_type`, instance id, `len(to_label)`.

**Fire-and-forget wrapper:** `schedule_pending_entity_labeling_fire_and_forget(...)` mirroring `schedule_entity_labeling_fire_and_forget`.

**Do not change:** `prompts.py`, `entity_labeling_service.py` loaders, or LLM task config.

---

## Phase 1 — Wire legacy connect / reconnect paths

### 1a. Salesforce OAuth (`fetch_and_store_salesforce_objects`)

**File:** `backend/utils/salesforceapimigration.py`

**Today:** End of sync calls `schedule_salesforce_connector_labeling_by_org_id_fire_and_forget` (`category IS NULL` only).

**Change:**

1. After field fetch + hash update (reuse logic from `SalesforceMetadataAdapter._update_object_hashes` or extract shared helper `load_salesforce_current_hashes(user_id, org_id) -> dict`).
2. Replace narrow scheduling with:
   ```python
   schedule_pending_entity_labeling_fire_and_forget(
       user_id=..., connector_type="salesforce",
       connector_ref={"connector_id": ..., "org_id": org_id},
       current_hashes=current_hashes,
       drift_keys=[],  # full reconnect: stale/missing semantics drives queue
       legacy_keys=legacy_category_null_keys,  # optional backward compat
   )
   ```
3. Keep `on_metadata_sync_complete` as today.
4. **Remove or deprecate** direct call to `schedule_salesforce_connector_labeling_by_org_id_fire_and_forget` from this path to avoid double-queue (adapter still runs when manual sync calls `fetch_and_store` internally — dedupe via active job merge in `schedule_entity_labeling` is OK but prefer single call from adapter only).

**Note:** When manual sync runs, `fetch_and_store` is called with `skip_org_rules_sync=True`, then adapter queues labeling. Avoid **triple** queue: either remove labeling from `fetch_and_store` and let callers queue, **or** add idempotent “already scheduled” short-circuit. **Recommended:** remove labeling from `fetch_and_store`; require callers (OAuth callback, SF adapter) to call shared hook once.

### 1b. SQL connect / re-analysis

**File:** `backend/services/sql_background_processing_service.py`

**Today:** After analysis, queues **all** tables via `schedule_entity_labeling_fire_and_forget(entity_keys=all_tables)`.

**Change:**

1. Build `current_hashes` map during analysis (already computed for drift logging — reuse `fingerprint_sql_schema` per table).
2. Replace label-all with `schedule_pending_entity_labeling_fire_and_forget(..., drift_keys=new/changed tables if tracked)`.
3. Keeps semantics-aware backfill for legacy DBs; skips re-labeling unchanged `ready` rows.

**Entry points:** `start_database_analysis` from `main.py` create/update SQL connector (~7144, ~7309).

### 1c. Google Sheets analysis / re-sync

**File:** `backend/services/gsheets_background_processing_service.py`

**Today:** Per-sheet `schedule_entity_labeling_fire_and_forget` for every analyzed sheet.

**Change:**

1. Accumulate `current_hashes` during analysis loop (`fingerprint_sheets_header`).
2. After loop (or per connection batch), one call to `schedule_pending_entity_labeling_fire_and_forget` with all sheet entity keys — **or** keep per-sheet queue but pass through shared collector so already-`ready` sheets are skipped.

**Also:** Google OAuth connect path in `routes/auth.py` / spreadsheet registration — ensure analysis completion uses same hook.

---

## Phase 2 — DRY metadata adapters

**Files:**

- `backend/services/connector_metadata/adapters/salesforce_adapter.py`
- `backend/services/connector_metadata/adapters/sql_adapter.py`
- `backend/services/connector_metadata/adapters/google_sheets_adapter.py`

**Change:** Replace inline `collect_entities_needing_labeling` + `schedule_entity_labeling_fire_and_forget` blocks with `schedule_pending_entity_labeling_fire_and_forget`.

Adapters keep responsibility for:

- Fetching/updating schema
- Computing `current_hashes` and `drift_keys`
- SF-only `legacy_keys` query

---

## Phase 3 — Readiness & email reliability

**File:** `backend/services/labeling/readiness_service.py`

| Fix | Detail |
|-----|--------|
| **Notify only on success** | Move `mark_agent_ready_notified` to run only after `send_connector_agent_ready_email` succeeds |
| **Retry-safe dedupe** | If send fails, leave `agent_ready_notified_at` null so a later rollup/SSE can retry |
| **Rollup after partial labeling** | In `job_worker.py`, when labeling completes, call `schedule_connector_profile_rollup(..., force=True)` if any entities reached `ready` but `should_rollup` is false (legacy org backfill) |

**Optional (P3b):** After labeling job with `ready_count > 0` and profile still `pending`, force rollup — avoids stuck `profile_rollup` phase.

**Optional (P3c):** When readiness SSE hits `is_ready` and `!agent_ready_notified`, call `check_and_notify_agent_ready` once (covers reconnect where rollup already happened). Guard with DB lock or `agent_ready_notified_at` check.

---

## Phase 4 — Deprecate narrow legacy schedulers

**File:** `backend/services/labeling/schedule.py`

- Mark `schedule_salesforce_connector_labeling*` as deprecated; implement as thin wrappers around `schedule_pending_entity_labeling` + SF legacy key query for any external callers.
- Update `handlers/salesforce_handler.py` `categorize_salesforce_objects_in_batches` to use pending hook or backfill helper.

**Do not delete** until all call sites migrated.

---

## Phase 5 — Tests

**New / extended tests:**

| Test | Assert |
|------|--------|
| `test_schedule_pending_entity_labeling_sf_empty_semantics` | All catalog objects queued when semantics empty, even if `category` set |
| `test_schedule_pending_entity_labeling_sf_no_op_when_ready` | No queue when all semantics `ready` and hashes match |
| `test_schedule_pending_entity_labeling_sql_skips_ready` | Unchanged ready tables not re-queued |
| `test_check_and_notify_does_not_mark_on_email_failure` | Mock Postmark fail → `agent_ready_notified_at` stays null |
| `test_oauth_fetch_and_store_uses_pending_hook` | Mock `fetch_and_store` end → pending hook called with hashes |
| Adapter integration | Existing `test_labeling_system.py` still passes |

**Manual QA checklist:**

- [ ] SF: reconnect OAuth, no schema delta, empty semantics → labeling jobs appear, email after rollup
- [ ] SF: manual sync after OAuth → no duplicate labeling storm
- [ ] SQL: reconnect existing connector → only pending/stale tables labeled
- [ ] GSheets: re-sync spreadsheet → semantics backfill
- [ ] Readiness banner in chat (OAuth `pending_sync`) + email received
- [ ] Reconnect when already notified → no second email (dedupe)

---

## Phase 6 — Frontend (optional, same branch or follow-up)

| Item | File | Note |
|------|------|------|
| Stale copy on Connections | `ConnectionsPage.js` | “You will receive an email…” — align with unified pipeline |
| Manual sync feedback | `ConnectorSchemaPanel.js` | Optional: toast “Labeling queued in background” when job completes |
| Readiness after manual sync | sessionStorage / SSE | Optional: set `pending_sync` after manual sync if user navigates to chat |

Low priority; backend fixes deliver most value.

---

## Implementation order (recommended)

```
P0  shared hook                          (~1 file, small)
P1a SF OAuth (highest user impact)       (~2 files)
P3  email dedupe fix                     (~1 file, quick win)
P1b SQL                                  (~1 file)
P1c GSheets                              (~1 file)
P2  adapter DRY                          (~3 files)
P4  deprecate legacy schedulers          (~1 file)
P5  tests                                 (~2 files)
P6  FE polish                            (optional)
```

**Estimated scope:** ~8–12 backend files, 1 new module, 6–10 tests. No migrations required.

---

## Shared helpers to extract (avoid duplication)

| Helper | Used by |
|--------|---------|
| `load_salesforce_current_hashes(user_id, org_id) -> dict` | `salesforceapimigration.py`, `salesforce_adapter.py` |
| `salesforce_legacy_uncategorized_keys(user_id, org_id) -> list` | SF OAuth + adapter |
| `load_sql_table_hashes(user_id, connector_id) -> dict` | SQL analysis + adapter |

Place in `services/labeling/catalog_hash_helpers.py` or under `connector_metadata/`.

---

## Rollout / risk

| Risk | Mitigation |
|------|------------|
| Double labeling on manual SF sync | Remove queue from `fetch_and_store`; single queue in adapter/OAuth |
| LLM cost spike on large SF org first backfill | Expected once for legacy orgs; batch concurrency already capped (`LabelingConfig`) |
| Email spam on reconnect | Keep `agent_ready_notified_at` dedupe; only send when null |
| Long time to “ready” on 200+ objects | Existing behavior; UI already shows labeling phase in SSE |

**Feature flag (optional):** `LABELING_USE_PENDING_HOOK=1` env default true for gradual rollout.

---

## Out of scope (defer)

- Migrating legacy `salesforce_objects.category` into semantics (backfill handles via re-label)
- Rewriting GSheets metadata adapter to live-fetch from Google (v1 stays hash-on-cache)
- GitHub release / version bump (separate task after merge to dev)

---

## Definition of done

1. OAuth reconnect for SF / SQL / GSheets queues labeling when `connector_entity_semantics` is missing or stale, regardless of legacy categorization columns.
2. Manual metadata sync and OAuth reconnect use the same scheduling primitive.
3. Connector-specific labeling prompts and schema loaders unchanged.
4. Agent-ready email sent once per connector when pipeline reaches `ready`, not marked notified on Postmark failure.
5. Tests cover SF empty-semantics reconnect case and email failure dedupe.
