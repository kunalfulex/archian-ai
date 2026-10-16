# Enterprise Graph Assessment (EGA) — Implementation Plan

**Status:** P0–P6 complete (2026-06); warehouse scoping refinement pending  
**Scope:** Graph canvas, assessment pipeline, LLM-first intelligence, artifact freshness

**Related docs:** [DATA_LAKE_IMPLEMENTATION_PLAN.md](./DATA_LAKE_IMPLEMENTATION_PLAN.md), [ARCHITECTURE.md](./ARCHITECTURE.md), [CONNECTOR_READINESS_LABELING_PLAN.md](./CONNECTOR_READINESS_LABELING_PLAN.md)

---

## Intelligence architecture (LLM-first)

EGA uses a **unified semantic bundle** and **LLM-first** design for cross-connector mapping, lake, and warehouse. Heuristic/rules paths are **fallback only**.

```
per-connector DAA
    → schema-aware inventory (semantics-enriched, metadata filtered)
    → LLM cross-connector mapping     [EGA_ENTITY_MAPPING]  (pairwise clusters)
    → canonical build (+ optional FK neighbor augmentation)
    → LLM lake proposal (medallion)   [EGA_LAKE_DESIGN]
    → LLM warehouse proposal (star)     [EGA_WAREHOUSE_DESIGN]
    → assessment report
```

### Shared bundle (`ega_intelligence_bundle.py`)

All LLM stages consume the same compact JSON:

| Field | Source |
|-------|--------|
| `entities_by_member` | Filtered DAA inventory + `connector_entity_semantics` |
| `entity_class` | `business` / `metadata` / `junction` from semantics |
| `semantics.category/description/synonyms/domains` | `connector_entity_semantics.semantics` |
| `display_label` | Workbook/tab or table name (UI) |
| `linking_name` | Semantic category (matching) |
| `cross_map_intent` | Canvas `layout.edges` where `linkType === cross_map` |
| `existing_mappings` / `canonical_entities` | Prior artifacts for lake/warehouse |

Caps: `EGA_MAX_ENTITIES_PER_MEMBER` (default 40), `EGA_MAX_COLUMNS_PER_ENTITY` per bundle entity.

### Entity inclusion (semantics-first, not hardcoded table names)

**Do not** filter platform/system tables by Edison-specific regex (`migration_*`, `chat_*`, etc.). Customer SQL sources use arbitrary table names.

| Layer | File | Rule |
|-------|------|------|
| Inventory filter | `semantic_mapping/entity_filters.py` | `should_exclude_from_ega_modeling()` |
| Policy | `planning/entity_catalog_policy.py` | `is_metadata_entity()` — semantics first |
| Semantics signal | `connector_entity_semantics` | `entity_class=metadata\|system`, category markers (`System Catalog`, `Spreadsheet Registry`, …) |
| Excluded classes | `entity_filters.py` | `junction` tables (linkage, not cross-map endpoints) |
| Fallback | `entity_catalog_policy` | Connector-type name heuristics **only when semantics missing** |

Filter runs on **physical `entity_id`** before semantic category overwrites `normalized_name` (prevents `migration_records` → `Customer Records` bypass).

**Applies to:** cross-connector mapping inventory, lake bronze candidate set.  
**Does not yet fully apply to:** warehouse canonical FK augmentation (see § Known gaps).

### Semantics precedence (per entity)

1. **`connector_entity_semantics`** (`labeling_status = ready`)
2. Legacy `ai_categorization` on `*_extracted_schemas`
3. DAA assessment entity meta

### Cross-connector mapping rules

| Rule | Enforced in |
|------|-------------|
| Pairwise clusters (one entity per `member_id`) | `linking_validator.py` |
| Span ≥ 2 connectors | `linking_validator.py` |
| Min confidence | `EGA_MAPPING_MIN_CONFIDENCE` (default 0.55) |
| Business entities only | Prompt + inventory filter |
| Prefer `cross_map_intent` pairs | `entity_linking_v1.py` prompt |

UI (`SemanticMappingsEditor.js`): clusters with 3+ entities render as canonical name + per-connector list (not one long `↔` chain).

### Lake bronze naming

Deterministic names via `lake_designer.bronze_table_name()` — **never** embed Google `spreadsheet_id` in table slugs.

| Pattern | Example |
|---------|---------|
| SQL | `raw__<member_slug>__<semantic_or_table_slug>` |
| GSheets | `raw__<member_slug>__<workbook>_<tab>` from `display_label` (`Archian Data / Sheet1` → `archian_data_sheet1`) |

LLM lake output is validated in `lake_validator.py`, which **replaces** LLM-generated bronze names with the deterministic slug. Lineage columns retain full `entity_id` (`spreadsheetId::Sheet1`).

### Stage summary

| Stage | Primary | Fallback | Task key |
|-------|---------|----------|----------|
| Cross-connector mapping | `entity_linking_llm.propose_entity_mappings_llm` | Heuristic linker + pairwise LLM band | `ega_entity_mapping` |
| Lake | `lake_designer_llm.propose_lake_logical_model_llm` | `build_bronze_tables` + `build_silver_views` | `ega_lake_design` |
| Warehouse | `warehouse_designer_llm.propose_logical_model_llm` | Rule-based star schema | `ega_warehouse_design` |

### Feature flags (env)

| Variable | Default | Meaning |
|----------|---------|---------|
| `EGA_ENTITY_LINKING_LLM` | `1` | LLM-first entity mapping |
| `EGA_ENTITY_LINKING_LLM_FALLBACK` | `1` | Heuristic linker on LLM failure |
| `EGA_LAKE_LLM` | `1` | LLM-first lake layout |
| `EGA_LAKE_LLM_FALLBACK` | `1` | Rules lake on LLM failure |
| `EGA_WAREHOUSE_LLM_FALLBACK` | `1` | Rules warehouse on LLM failure |
| `EGA_MAPPING_MIN_CONFIDENCE` | `0.55` | Min cluster confidence from mapping LLM |
| `EGA_MAX_ENTITIES_PER_MEMBER` | `40` | Cap entities sent to mapping/lake LLM per member |

---

## Problem statement (stale artifacts)

Saving the enterprise graph canvas **does not** rerun assessment. After connector changes, UI can show stale warehouse/lake/mappings.

| Issue | Behavior |
|-------|----------|
| Save ≠ assessment | Wizard `PATCH` + `PUT members` only |
| Member replace wipes mappings | `replace_members()` new UUIDs; CASCADE mappings |
| Canvas edges ignored (linking) | **Fixed:** `cross_map_intent` in intelligence bundle |
| GSheets scope drift | **Fixed:** `connector_ref.sheet_entity_allowlist` is source of truth |

---

## Completed phases

### P0 — Data correctness ✅

- Canonical sync-delete, member prune, `assessment_stale`, fresh canonical in lake/warehouse

### P1 — Stale UX ✅

- Dirty save, stale banner, proposal refetch after assessment

### P2 — Version picker ✅

- Lake/warehouse proposal version dropdown

### P3 — GSheets scope ✅

- Sheet allowlist on members, catalog labels on assessment page, `Sheet1` alias fix

### P4 — Mapping alignment ✅

- Flow prior resolution, orphan prune, label-aware UI

### P5 — LLM-first intelligence ✅

| Task | File(s) |
|------|---------|
| Intelligence bundle | `semantic_mapping/ega_intelligence_bundle.py` |
| Semantics inventory | `schema_inventory.build_schema_aware_inventory` |
| LLM entity mapping | `entity_linking_llm.py`, `linking_validator.py`, `prompts/entity_linking_v1.py` |
| LLM lake | `lake_designer_llm.py`, `lake_validator.py`, `prompts/lake_design_v1.py` |
| Linker integration | `linker.run_entity_linking` (LLM first) |
| Lake integration | `lake_designer.build_lake_proposal` (LLM first) |
| Tests | `tests/test_ega_intelligence_linking.py` |

### P6 — Semantics-first filtering & mapping quality ✅

| Task | File(s) |
|------|---------|
| Replace hardcoded platform regex | `entity_filters.py` → `entity_catalog_policy.is_metadata_entity` |
| Inventory filter before rename | `schema_inventory.build_schema_aware_inventory` |
| Pairwise cluster validator | `linking_validator.py` (max one entity per connector per cluster) |
| Lake bronze slug (no spreadsheet id) | `lake_designer.bronze_table_name`, `lake_validator.py` |
| Mapping cluster UI | `SemanticMappingsEditor.js`, `EnterpriseGraphsPage.css` |
| GSheets scope labels | `enterpriseGraphConnectors.js`, `GoogleSheetsNodeScope.js` |
| Entity resolution | `entity_resolution.py` |
| Tests | `test_entity_filters.py`, `test_lake_bronze_naming.py` |

---

## Assessment pipeline

All depths run the same rollup; depth only changes DAA entity scope.

1. **DAA** per source member (warehouse/lake members skipped for entity inventory).
2. **Inventory** — semantics-enriched; metadata/system/junction entities excluded.
3. **Linking** — LLM proposes pairwise clusters; validator enforces structure.
4. **Canonical** — one entity per mapping cluster; FK neighbors may be added as draft nodes.
5. **Lake** — bronze per business source; silver `stg__*` views union mapped bronze branches.
6. **Warehouse** — star schema from canonical bundle (see gap below).
7. **Report** — readiness scores, schema map, cross-links.

Lake silver is produced by **lake LLM** (or rules fallback) using **semantic mappings**.

---

## Known gaps / next work

| Gap | Impact | Suggested fix |
|-----|--------|----------------|
| Warehouse FK augmentation | `canonical_builder._augment_with_fk_neighbors` adds unmapped Test DB tables → broad `fact_*` / `dim_*` model even when cross-map is one pair | Apply `should_exclude_from_ega_modeling` in FK augmentation; or scope warehouse bundle to **confirmed** mappings only |
| Silver branch count vs bronze | LLM lake may emit more UNION branches than validated bronze tables | Tighten `lake_validator` to require `branch_count === len(source_bronze_tables)` |
| Unlabeled entities | No semantics → name fallback only | Optionally exclude from mapping inventory until `labeling_status=ready` |
| Bundle scope | Up to 40 entities/member still noisy for large app DBs | Optionally restrict mapping bundle to `cross_map_intent` member pairs only |
| Tab name in bronze slug | `archian_data_sheet1` includes default tab `Sheet1` | Optional: slug from workbook name only when tab is generic |

---

## Settings contract

```json
{
  "lake_enabled": true,
  "layout": {
    "version": 1,
    "nodes": [{ "id": "...", "optionKey": "google_sheets:<connId>", "sheetAllowlist": ["<sid>::Sheet1"] }],
    "edges": [{ "fromNodeId": "...", "toNodeId": "...", "linkType": "cross_map" }]
  },
  "assessment_stale": false
}
```

---

## Test plan

### Automated

| Test file | Covers |
|-----------|--------|
| `tests/test_ega_intelligence_linking.py` | Bundle `cross_map_intent`, pairwise validator, mega-cluster rejection |
| `tests/test_entity_filters.py` | Semantics-first metadata/junction exclusion |
| `tests/test_lake_bronze_naming.py` | GSheets slug without spreadsheet id |
| `tests/test_enterprise_graph_linker.py` | Heuristic linker fallback |
| `tests/test_schema_inventory.py` | GSheets alias collision, semantics enrichment |
| `tests/test_enterprise_graph_connector_ref.py` | Allowlist on `connector_ref` |
| `tests/test_enterprise_graph_entity_resolution.py` | Entity label resolution |

### Manual (SQL + GSheets graph)

1. Canvas: Test DB ↔ Archian AI with `cross_map` edge; GSheets allowlist = one workbook/tab.
2. Run **partial assessment**.
3. **Cross-connector mappings:** one AI-matched pair (e.g. `users ↔ Archian Data / Sheet1`), not mega-cluster.
4. **Lake:** 2 bronze tables, clean names (`raw__*__archian_data_sheet1`), silver `stg__*` view.
5. **Warehouse:** review for over-scoping (expected gap until P7).
6. Re-save graph with different sheet → `assessment_stale` banner → re-run assessment.

---

## Frontend (edisonai-frontend)

| Component | Role |
|-----------|------|
| `EnterpriseGraphWizard.js` | Save `sheet_entity_allowlist` on `connector_ref` |
| `EnterpriseGraphsPage.js` | Stale banner, GSheets chip labels from catalog API |
| `SemanticMappingsEditor.js` | Pairwise / multi-entity cluster display, Edit/Confirm/Reject |
| `EnterpriseGraphResultsTabs.js` | Mappings / lake / warehouse tabs |
| `ProposalVersionPicker.js` | Lake & warehouse version dropdown |
| `GoogleSheetsNodeScope.js` | Per-node sheet allowlist in wizard |
| `useLakeProposal.js` / `useWarehouseProposal.js` | Proposal fetch, regenerate, approve |

---

## Out of scope

- Auto-confirm mappings without user review
- Routing EGA through chat `/architect/stream`
- Executing canvas load edges in orchestration
- EGA-automated bulk load into bronze (chat flows own data movement)
