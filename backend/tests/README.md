# Backend test tiers

## Tier 1 — Clarification / LLM-first planner (CI gate)

Run after planner or clarification changes:

```bash
cd edison-ai/backend
python scripts/audit_keyword_routing.py
pytest tests/test_sf_export_new_sheet_flow.py \
       tests/test_clarification_emit_invariant.py \
       tests/test_clarification_pipeline.py \
       tests/test_clarification_queue.py \
       tests/test_destination_catalog_validate.py \
       tests/test_planning_clarification_apply.py \
       tests/test_planner_session_loop.py \
       tests/test_google_sheets_account_clarification.py \
       tests/test_op_requirements.py \
       tests/test_plan_builder.py::test_sf_to_new_sheet_plan_steps \
       tests/test_plan_builder.py::test_load_flow_run_template_starts_with_load_flow \
       tests/test_plan_binding_create_only.py \
       tests/test_plan_extract_gates.py \
       tests/test_same_entity_migration.py \
       tests/test_gap_detector.py \
       -q
```

**Canonical slot compiler:** `services/planning/op_requirements.py`  
**Slot gap tests:** `test_op_requirements.py`, `test_sf_export_new_sheet_flow.py`  
**Catalog disambiguation (supplement):** `test_gap_detector.py`

## Tier 2 — Architecture guardrails

Assert removed keyword paths stay off the planning hot path:

- `test_keyword_routing_gates.py`
- `test_plan_scope_authority.py`
- `test_binding_contract_planner_path.py`
- `test_audit_keyword_routing.py`
## Tier 3 — Intent overlay (UI gaps, not slot compiler)

Fresh-path intent projection only; does not drive plan assembly:

- `test_intent_pipeline.py`
- `test_intent_golden.py`

S04 destination need follows `_pipeline_tokens` / PLAN_SCOPE when present.

## Retired / merged

| Removed | Replaced by |
|---------|-------------|
| `test_planner_integration.py` | `test_plan_builder.py` (SF export + load_flow) |

## Not in clarification gate

Mapping transforms, enterprise graph, DB integration, asyncio harness issues — fix separately.
