# Database migrations by edition

Self-hosted **community** installs should not apply hosted billing schema.

| Path | Edition | Contents |
|------|---------|----------|
| `*.sql` (this directory) | **community** (+ shared product features) | Connectors, chat, extraction/migration, labeling, LLM task rows, etc. |
| `cloud/` | **cloud only** | Credit / plan billing tables (`add_credit_billing_tables_and_columns.sql`) — used with private `archian-cloud` |
| Optional RAG | community, opt-in | `add_pipeline_learning.sql`, `add_learning_retrieval_log.sql` need **pgvector**; skip unless you enable pipeline learning |

## Apply (community)

```bash
# From backend/, with DB_URL or DATABASE_URL set
./scripts/apply_migrations.sh community
```

Cloud hosts after community baseline:

```bash
./scripts/apply_migrations.sh cloud
```

Pipeline learning (pgvector):

```bash
./scripts/apply_migrations.sh learning
```

> Note: These SQL files are incremental deltas used by the product over time. A greenfield community DB still needs a compatible baseline schema (export from a working env or future bootstrap dump). The edition split prevents applying **billing** tables to community by default.
