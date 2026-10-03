# AI Research Agent v2

A standalone research workspace: React/Vite frontend, FastAPI backend, Supabase PostgreSQL + Auth, and optional Upstash Redis acceleration. The original project is unchanged.

**Start here:** [Detailed online hosting tutorial](docs/HOSTING-TUTORIAL.md), or the shorter [Setup and deployment reference](docs/DEPLOYMENT.md). See [Architecture and API](docs/ARCHITECTURE.md), [Testing](docs/TESTING.md), and [Operations](docs/OPERATIONS.md).

## Included

- Public home page at `/` with a feature overview and signup/sign-in links. Research starts at `/research`; all workspace services require authentication.

- Live Tavily research, draft/review stages, citations and saved source provenance; optional deeper research.
- Private searchable research history, report comparison, rename, delete, rerun, PDF/Markdown export.
- Persistent chat with report context, cancellation/retry, conversation and individual response export.
- Once/daily/weekly/monthly schedules with timezone-aware previews, pause/resume, edit, duplicate, delete, run-now, history, and notifications.
- Real token/cost ledger, conservative reservations, atomic daily allowances, and platform/external usage separation.
- Read-only free model catalog: `openrouter/free`, `qwen/qwen3.8-27b:free`, `nvidia/nemotron-3.5-lightning:free`.
- Administrator dashboard for usage, limits, access, feature permissions, jobs, service health, and audit logs.
- Built-in adversarial screening and custom OpenAI-compatible targets with encrypted temporary credentials and private-network protections.

## Local startup

Requires Python 3.12+, Node 22+, a configured Supabase project, OpenRouter key, and Tavily key. Redis is recommended but optional; the database throttle remains active without it.

From this directory:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.lock
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Fill in the configuration following the deployment guide. The existing parent `.env` is not copied or exposed. Transfer only its `OPEN_ROUTER_FREE_API_KEY` value into the backend environment yourself, or inject it using your secret manager.

```bash
backend/.venv/bin/python scripts/manage.py migrate
cd backend
.venv/bin/uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`, create an account, confirm your email, and sign in. Promote your account separately:

```bash
backend/.venv/bin/python scripts/manage.py promote-admin your-email@example.com
```

The frontend never receives OpenRouter, Tavily, Redis, database, or encryption secrets. The Supabase publishable/anon key is intentionally public; the database uses RLS and backend authorization.

## Directory layout

```text
backend/app/             API, workers, providers, quotas, exports, security
backend/tests/           Unit/integration tests and isolated browser harness
frontend/src/            Responsive application and all feature pages
frontend/tests/          Browser acceptance tests
supabase/migrations/     Schema, RLS, quota and scheduling functions
supabase/scheduler.sql   Optional hosted Cron + Vault setup
scripts/                 Migrations, admin bootstrap, smoke checks
render.yaml              Render free web-service blueprint
```

## Expectations

The default application allowance is 50,000 input+output tokens per user per UTC day. This is a cap, not a promise of provider capacity. OpenRouter and Tavily quotas are shared across the deployment. Only free inference routes are permitted; there is no automatic paid fallback.

Scheduling is best effort. Free services can sleep, restart, pause, or exhaust their allowances. The database stores work durably; an interrupted in-flight provider call may need an explicit retry, with conservative usage retained until reconciled. Built-in red-team scoring is heuristic, not a security certification.

No hosted services were provisioned or deployed by this implementation. Configure your accounts and secrets, then follow the supplied deployment and verification steps.
