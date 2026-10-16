# Archian AI

Open-source AI data migration platform — extract, map, and migrate across Salesforce, Google Sheets, SQL databases, and files.

**Hosted:** [https://app.archian.ai](https://app.archian.ai)

> Demo GIF: add a short (~20s) product walkthrough under `docs/assets/demo.gif` and embed here.

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
| Files (CSV/Excel/PDF, …) | Yes | Yes | Yes | Works out of the box |
| SQL (Postgres, MySQL, MSSQL, …) | Yes | Yes | Yes | Needs a DB connection string |
| Salesforce | Yes | Yes | Yes | Optional OAuth connected app |
| Google Sheets | Yes | Yes | Yes | Optional Google OAuth |

Billing / Stripe plans are **not** in this community tree. Hosted cloud uses a private `archian-cloud` package when `ARCHIAN_EDITION=cloud`.

## Quickstart (Docker)

**Requirements:** Docker, an OpenAI API key.

```bash
cp .env.example .env
# set OPENAI_API_KEY=... and keep ARCHIAN_EDITION=community

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
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
cp .env.example .env   # REACT_APP_API_URL=http://localhost:8000
npm install && npm start
```

## Edition

| `ARCHIAN_EDITION` | Behavior |
|-------------------|----------|
| `community` (default) | No billing UI/APIs; unlimited local usage gates |
| `cloud` | Requires private `archian-cloud` at build/install time |

## License

[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) (DCO sign-off required). Security reports: [SECURITY.md](SECURITY.md).
