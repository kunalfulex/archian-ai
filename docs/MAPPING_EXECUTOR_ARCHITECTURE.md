# Mapping Executor Architecture

**Status:** Phase 4 implemented (2026-06)  
**Related:** [MAPPING_CONTEXT_AND_AGENTIC_MIGRATION_PLAN.md](./MAPPING_CONTEXT_AND_AGENTIC_MIGRATION_PLAN.md)

---

## Boundary

| Layer | Responsibility |
|-------|----------------|
| **Planning + clarification** | Connectors, entities, field scope, plan approval |
| **Mapping executor** | Load pinned destination schema → field LLM → validate → transform LLM → validate → persist |

Mapping **must not** re-resolve destination connector or entity from the user’s original pipeline prompt when `plan_contract` pins an entity.

---

## Module layout (`services/mapping/`)

| Module | Role |
|--------|------|
| `service.py` | Unified `execute_mapping` orchestrator (SF / SQL / GSheets) |
| `context.py` | `MappingExecutionContext` from plan pins |
| `brief.py` | Deterministic mapping brief (replaces raw session prompt) |
| `validate.py` | Empty-mapping fail-closed gate |
| `schema_loader.py` | Salesforce pinned-entity schema + lookups |
| `required_fields.py` | Deterministic required-field injection (SF) |
| `field_map_sf.py` | Salesforce executor adapter |
| `field_map_sql.py` | SQL executor adapter (plan-pinned table) |
| `field_map_gs.py` | Google Sheets executor adapter (plan-pinned tab) |

Shared helpers remain in `services/mapping_plan/` (`target_resolution`, `catalog_target_resolver`, `parse_instruction`, `models`).

---

## `MappingExecutionContext`

Built by `services/mapping/context.py::build_mapping_execution_context()` from:

- `plan_contract` (source / destination / `mapping_target`)
- Step args (`destination_entity_key`, recovery hints)
- Classification pins (`destination_object_api_name`, etc.)
- Extraction job sample row

`mapping_brief` (from `services/mapping/brief.py`) replaces `session.prompt` as the LLM instruction.

---

## Execution flow

1. **Gate** — Approved plan requires pinned destination entity; otherwise fail before LLM.
2. **Target resolution** — `plan_pinned` / `classification_explicit` hints skip catalog LLM (`target_resolution.py`).
3. **Field map LLM** — Uses destination schema for pinned entity only.
4. **Validate** — Schema filter, lookup sanitize; **fail closed** if mapping empty.
5. **Transform LLM** — Second call on validated mapping.
6. **Persist** — `conversation_migration_contexts` (see migration context doc).

---

## Connector-only (no entity)

Handled entirely in **planning**: `pick_destination_entity` clarification until `plan_contract.destination.entity_key` is set. Map step does not run.

---

## Plan-pinned parity (Phase 3)

All three connectors honor `target_resolution_source in ("plan_pinned", "classification_explicit")`:

| Connector | Adapter | Handler skip when pinned |
|-----------|---------|--------------------------|
| Salesforce | `field_map_sf.py` | reconcile, catalog LLM override, `apply_mapping_target_object_override` |
| SQL | `field_map_sql.py` | reconcile; table selected by pinned name |
| Google Sheets | `field_map_gs.py` | reconcile; tab filtered by pinned sheet name |

Legacy `/map-fields` without pins still uses categorization heuristics and `evaluate_and_refine_mapping` (Salesforce only).

---

## Phase 4 (implemented)

- `services/mapping/persist.py` — `persist_mapping_execution_result()` upserts `conversation_migration_contexts` after every agentic map path
- `MapFieldsTool` and `run_map_action` use unified persist (no duplicated write-policy / sheet inference logic)
- `call_salesforce_architect_agent` uses deterministic `inject_required_salesforce_fields` when target is pinned **or** schema narrowed to a single base object; `evaluate_and_refine_mapping` is legacy-only

- `services/mapping_plan/salesforce_target_resolver.py` (orphaned; catalog resolver used directly)
- `services/mapping_plan/salesforce.py`, `sql.py`, `google_sheets.py` (moved to `services/mapping/field_map_*`)
