# Testing and verification

## Verified locally

Final local run: **38 backend tests passed and 7 browser workflows passed**. Backend lint, frontend lint, and the Vite production build passed. The production Docker image built successfully; container checks imported all 47 API routes and generated a PDF. Desktop/mobile layouts and a four-page PDF export were visually reviewed.

The acceptance suite uses real PostgreSQL migrations, SQL functions, RLS, transactions, FastAPI endpoints, and the actual React application. External identity, search, and model calls are simulated at their service boundary. This avoids spending provider allowances and allows repeatable failures.

Backend coverage includes ownership checks, RLS read/write restrictions, administrator authorization/auditing, atomic quota races, UTC rollover, provider usage reconciliation, missing usage, provider 429, ambiguous timeout/no replay, Redis fallback, cancellation, schedule CRUD/previews, missed-run recovery, duplicate dispatch, DST, timezone aliases, month-end, worker checkpoints, PDF/Markdown exports, credential expiry/cleanup, and unsafe target destinations.

Browser coverage includes signup/signin, session persistence, password change, protected routes, verification/reset messaging, live-stage report rendering, downloads, persistent chat, analytics/model catalog, red-team results, schedule edits/pause/manual runs/deletion, admin settings/audit, mobile navigation, public-home rendering, and login gates for every workspace service.

PDF output is also rendered and visually reviewed using Poppler. The backend Docker image is built separately from the browser harness; it contains only `backend/app` and production dependencies.

## Reproduce

Prerequisites: PostgreSQL server/client binaries, Python 3.12+, Node 22+, and curl. Run as an ordinary user; PostgreSQL `initdb` refuses root.

From the v2 directory:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.lock -r backend/requirements-dev.txt
cd frontend
npm ci
npx playwright install chromium
cd ..
```

Backend only:

```bash
scripts/test-local.sh
```

Backend plus browser workflows (ports 8001, 5174, and 55441 must be available):

```bash
E2E=1 scripts/test-local.sh
```

The script creates its own temporary PostgreSQL cluster and cleans it up afterward. Set `PG_BIN` if PostgreSQL binaries are not found through `pg_config`, or `TEST_PORT` to change the database port.

For an existing **disposable local** test database:

```bash
TEST_DATABASE_URL=postgresql://USER@127.0.0.1:PORT/TEST_DB PYTHONPATH=backend backend/.venv/bin/pytest backend/tests -q
```

**The database fixture drops and recreates its public/auth schemas. Never point it at useful data.** It rejects remote URLs. Without `TEST_DATABASE_URL`, database tests skip while pure unit tests can still run.

Static/build checks:

```bash
backend/.venv/bin/ruff check backend scripts
cd frontend
npm run lint
npm run build
```

Production container:

```bash
docker build -t research-agent-v2:local backend
# Run from the v2 root after filling backend/.env:
docker run --rm --env-file backend/.env -e PORT=7860 -p 7860:7860 research-agent-v2:local
```

Run that container command from the v2 root; the `--env-file` path is relative to that root.

## Boundary of verification

Local tests do not prove email delivery, a real Supabase token refresh exchange, real OpenRouter/Tavily availability, hosted cron wake-up after a cold start, free-tier quotas, or third-party endpoint behaviour. Complete the hosted smoke checklist in DEPLOYMENT.md after supplying service credentials.

The browser harness is in `backend/tests/browser_server.py`. It provides fake identity/provider behaviour **only for local tests** and requires a dedicated local database named `research_browser`. It is excluded from the Docker image. Never run that test module as your deployed server; deploy `app.main:app`.
