<p align="center">
  <img src="docs/assets/archian-logo.svg" alt="Archian AI" width="120" />
</p>

# Archian AI

Open-source AI data migration platform — extract, map, and migrate across Salesforce, Google Sheets, SQL databases, and files.

**Hosted:** [https://app.archian.ai](https://app.archian.ai)

> Demo GIF: add a short (~20s) product walkthrough under `docs/assets/demo.gif` and embed here.

## Stack

| Component | Community default |
|-----------|-------------------|
| Postgres | App data (users, chats, flows, extraction/migration) |
| Redis | Job / status stores |
| File uploads | **Local disk** (Docker volume `/data/uploads`) — no Archian S3 |
| LLM | Your `OPENAI_API_KEY` (or other configured providers) |

Optional: Salesforce / Google OAuth, self-hosted S3, pipeline learning (pgvector). See [backend/db/migrations/EDITION.md](backend/db/migrations/EDITION.md).

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the planning pipeline, agent runtime, and connector model.

```text
archian-ai/
  backend/     FastAPI API, planning, connectors
  frontend/    React architect chat UI
  e2e/         Playwright smoke tests
  docs/        Architecture and module guides
```

## Connectors

| Connector | Extract | Map | Migrate | Notes |
|-----------|---------|-----|---------|-------|
| Files (CSV/Excel/PDF, …) | Yes | Yes | Yes | Stored on **your** disk / volume |
| SQL (Postgres, MySQL, MSSQL, …) | Yes | Yes | Yes | Needs a DB connection string |
| Salesforce | Yes | Yes | Yes | Optional OAuth connected app |
| Google Sheets | Yes | Yes | Yes | Optional Google OAuth |

**Pipeline learning (optional):** similar past runs can be retrieved via embeddings + pgvector to improve planning. Enable only if you apply the learning migrations and set an embedding-capable API key.

Billing / Stripe plans are **not** in this community tree. Hosted cloud uses a private `archian-cloud` package when `ARCHIAN_EDITION=cloud`. Credit/billing SQL lives under `backend/db/migrations/cloud/` and is not applied by the community migration helper.

## Quickstart (Docker)

**Requirements:** Docker, an OpenAI API key. Postgres + Redis start with Compose. Uploads persist on the `archian_uploads` volume (your machine — not Archian cloud storage).

```bash
cp .env.example .env
# set OPENAI_API_KEY=... and keep ARCHIAN_EDITION=community
# FILE_STORAGE=local is already the default for Compose

docker compose up --build
```

- UI: http://localhost:3000  
- API: http://localhost:8000  

File and SQL/CSV flows work without Salesforce or Google credentials. For OAuth setup, see `docs/` and `backend/.env.example`.

### Local (without Compose)

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill DB_URL, REDIS_URL, JWT_SECRET, OPENAI_API_KEY
# FILE_STORAGE=local and LOCAL_UPLOAD_DIR=./data/uploads (default auto → local)
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
cp .env.example .env   # REACT_APP_API_URL=http://localhost:8000
npm install && npm start
```

Migrations: see [backend/db/migrations/EDITION.md](backend/db/migrations/EDITION.md) (`./scripts/apply_migrations.sh community`).

## Edition

| `ARCHIAN_EDITION` | Behavior |
|-------------------|----------|
| `community` (default) | No billing UI/APIs; local file storage by default; unlimited local usage gates |
| `cloud` | Requires private `archian-cloud` at build/install time; optional S3 |

CORS, Trusted Host, and CSP allowlists are **env-driven** (local defaults only). Hosted installs must set `FRONTEND_URL`, `BACKEND_URL`, and usually `CORS_ALLOWED_ORIGINS` / `TRUSTED_HOSTS` / `SUPPORT_EMAIL` — see `backend/.env.example`.

## License

[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) (DCO sign-off required). Security reports: [SECURITY.md](SECURITY.md).
