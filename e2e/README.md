# Edison planner E2E (Playwright)

Optional browser smoke tests for planner v2 UI (`.planner-plan-panel`, `.planner-clarification-panel`).

## Setup

```bash
cd edison-ai/e2e
npm install
npm run install-browsers
```

## Run locally

1. Start backend with planner flags (see `backend/docs/PLANNER_ROLLOUT.md`).
2. **Start frontend** (must be running before Playwright — otherwise `ERR_CONNECTION_REFUSED`):

```bash
cd edisonai-frontend
HTTPS=true SSL_CRT_FILE=certs/localhost.crt SSL_KEY_FILE=certs/localhost.key npm start
```

Wait until you see `https://localhost:3000` in the terminal.
3. Log in once and save auth state (optional but recommended):

```bash
npx playwright codegen --save-storage=auth.json https://localhost:3000
```

4. Run tests:

```bash
export E2E_FRONTEND_URL=https://localhost:3000
export E2E_CHAT_PATH=/chat
export E2E_STORAGE_STATE_PATH=./auth.json
npm test
```

`E2E_CHAT_PATH` defaults to `/chat` (architect route).

Tests **skip** when `E2E_FRONTEND_URL` is unset (safe for CI without browser infra).

### Google Sheets planner F2B (`tests/gsheets-planner-scenarios.spec.ts`)

Browser flows (auth required):

1. Workbook picker appears for SF → Google Sheets export  
2. Clicking **contact list** → plan has no “Create spreadsheet”; Run plan must not show “Spreadsheet created successfully”  
3. **Create new spreadsheet** panel option → create step or name prompt  

Backend API matrix (no browser):

```bash
cd edison-ai/backend
./venv/bin/python scripts/e2e_gsheets_planner_f2b.py
./venv/bin/python scripts/e2e_gsheets_planner_f2b.py --scenario pick_existing --execute  # full SF extract (slow)
```

## CI

Add a job that sets `E2E_FRONTEND_URL`, provides `E2E_STORAGE_STATE_PATH` from secrets, and runs `npm test` after deploy to staging.
