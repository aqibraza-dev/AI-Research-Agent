# Setup and free-tier deployment

This guide assumes the `research-agent-v2` folder stays inside the existing Git repository. If you publish this folder as a separate repository, remove the `research-agent-v2/` prefix from Render/Vercel root-directory settings and from `render.yaml`.

## 1. Supabase database and authentication

1. Create a new Supabase project. Keep the database password in a password manager.
2. In Connect, copy the PostgreSQL **transaction pooler** URL (port 6543). URL-encode special password characters. The backend disables prepared statement caching for transaction pooling and uses a maximum of four connections.
3. Copy the project URL and publishable key (the older anon key is also supported).
4. Create `backend/.env` from the example. Set `DATABASE_URL`, `DATABASE_SSL=true`, `SUPABASE_URL`, and `SUPABASE_PUBLISHABLE_KEY`.
5. Install the backend requirements and run, from the v2 directory:

   ```bash
   backend/.venv/bin/python scripts/manage.py migrate
   ```

   The operator script records applied migrations. Alternatively, execute `001_core.sql` followed by `002_functions.sql` in Supabase SQL Editor **once**. Do not then run the migration script without recording those migrations in `schema_migrations`.
6. Supabase Authentication → Providers: enable email/password. Keep email confirmation enabled for a public deployment.
7. Authentication → URL Configuration: initially add `http://localhost:5173/auth/callback` and `http://localhost:5173/account`. Set the production Site URL and add the exact production callback/account paths after deploying Vercel.
8. Configure custom SMTP for public signup verification and password resets. Supabase's built-in mail service is restricted and is not a public production mail solution. [Supabase SMTP documentation](https://supabase.com/docs/guides/auth/auth-smtp)

No service-role secret belongs in the frontend. Backend database access uses an operator-level connection; every API query enforces ownership. All application tables have RLS enabled, and ordinary authenticated database clients cannot mutate protected tables or elevate their role.

## 2. Upstash Redis

1. Create a Redis database using the available free plan. [Current Redis plans](https://upstash.com/pricing/redis)
2. Copy its TLS connection string into backend `REDIS_URL`, for example `rediss://default:PASSWORD@HOST:6379`.
3. Use the Redis protocol URL, not the REST URL/token.
4. Choose a region near the backend/database. Do not store credentials in Vercel's frontend variables.

Redis holds short-lived, user-scoped search cache entries and rate counters. The application remains functional when Redis is unavailable: PostgreSQL takes over request throttling and continues to enforce token allowances.

## 3. Search and model credentials

Set these **backend-only** secrets:

| Variable | Purpose |
| --- | --- |
| `OPEN_ROUTER_FREE_API_KEY` | Existing OpenRouter credential; use the exact variable spelling |
| `TAVILY_API_KEY` | Live source search |
| `CREDENTIAL_ENCRYPTION_KEY` | Encrypt temporary custom red-team credentials |
| `SCHEDULER_SECRET` | Authenticate Supabase's backend wake request |

Generate independent secrets locally:

```bash
backend/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
backend/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Place the first output in `CREDENTIAL_ENCRYPTION_KEY`, the second in `SCHEDULER_SECRET`. Keep both private.

The default is `openrouter/free`; Qwen `qwen/qwen3.8-27b:free` and NVIDIA `nvidia/nemotron-3.5-lightning:free` are selectable. Backend validation rejects other research/chat models, and provider routing sets zero prompt/completion price ceilings. An unavailable selected model produces an actionable failure; it never falls back to a paid model.

Tavily basic search currently provides a free monthly allowance. The app conservatively caps its own search attempts at 1,000 per UTC calendar month. **Disable paid/pay-as-you-go billing in Tavily and use a dedicated key**; the app cannot account for requests made outside this deployment. [Tavily credits](https://docs.tavily.com/documentation/api-credits)

OpenRouter's account-wide free request allowance is separate from per-user application tokens. Use Admin → Service health to inspect the provider's available counters. [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits)

## 4. Render backend — default

1. Push your source to a Git repository, excluding `.env`, virtual environments, and `node_modules`.
2. Create a Render Web Service connected to the repository, using **Docker** and the **Free** instance.
3. Set Root Directory to `research-agent-v2/backend`. Dockerfile Path is `./Dockerfile`; Docker build context is that same root directory.
4. Set the backend environment variables from `backend/.env.example`. Use Render's secret environment fields.
5. Set `ALLOWED_ORIGINS` to `http://localhost:5173` initially; replace/add your Vercel production origin after deployment. Comma-separated exact origins are supported. Do not use `*`.
6. Health Check Path: `/health`. The Docker command uses Render's supplied `PORT`. Keep **one Uvicorn worker**.
7. Deploy and wait for `/health` to return `{"status":"ok"}`.

You may instead select the included `research-agent-v2/render.yaml` as a Render Blueprint. Set its `sync: false` secrets in the dashboard. No Render Redis or Render PostgreSQL service is required.

Render Free sleeps after inactivity and may restart unexpectedly. The application does not require local persistence. Cold starts can delay scheduled and interactive jobs. Do not rely on a sleeping web service for exact-time execution. [Render free-service limitations](https://render.com/docs/free)

## 5. Vercel frontend

1. Import the same Git repository into Vercel.
2. Root Directory: `research-agent-v2/frontend`.
3. Framework: Vite. Build Command: `npm run build`. Output Directory: `dist`. Install Command: `npm ci`. Use Node 22 or newer.
4. Set these variables before building:

   ```text
   VITE_API_URL=https://YOUR-SERVICE.onrender.com/api/v1
   VITE_SUPABASE_URL=https://YOUR-PROJECT.supabase.co
   VITE_SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLIC_KEY
   ```

5. Deploy. The included rewrite supports deep links such as `/research/ID` and `/chat/ID`.
6. Add the exact Vercel origin to Render's `ALLOWED_ORIGINS` and redeploy Render.
7. Set Supabase Site URL to the Vercel origin, and add `https://YOUR-APP.vercel.app/auth/callback` and `https://YOUR-APP.vercel.app/account` to allowed redirect URLs.
8. Preview deployment origins must be added explicitly if you want them to access the backend. Do not expose secrets in `VITE_*` variables.

Vercel Hobby is for personal/noncommercial use. Commercial deployment requires an eligible plan. [Vercel Hobby documentation](https://vercel.com/docs/plans/hobby)

## 6. Bootstrap administrator

Sign up normally in the frontend and verify your email. From a trusted local environment with database access:

```bash
backend/.venv/bin/python scripts/manage.py promote-admin your-email@example.com
```

Reload the app. The Admin panel appears. Promotion is not available through public signup metadata or an ordinary user API. Subsequent admin changes are audited.

## 7. Enable hosted scheduling

1. First confirm the backend is deployed and healthy.
2. Open `supabase/scheduler.sql` locally. Replace the two placeholder values with the backend URL ending `/api/v1/internal/wake` and the same `SCHEDULER_SECRET` stored in Render.
3. Execute it in Supabase SQL Editor. Do not commit the filled-in secret-bearing SQL.
4. This enables `pg_cron` and `pg_net`, stores secrets in Vault, and creates one cron job that runs each minute.
5. Every tick transactionally enqueues due work. An HTTP wake request is made only while queued/running jobs exist. Wake responses are fast; the durable worker processes the queue separately.
6. Create a once-only schedule a few minutes ahead. Confirm a schedule-run record, report, and notification appear. Inspect `cron.job_run_details` and `net._http_response` if dispatch fails.

To stop dispatch:

```sql
select cron.unschedule('research-dispatch');
```

The worker also checks schedules while the app is awake, making local development possible without Supabase Cron. Concurrent checks are safe through row locks and occurrence uniqueness. [Supabase Cron](https://supabase.com/docs/guides/cron)

## 8. Hugging Face alternative

Use this only if your Hugging Face account is eligible to run Docker Spaces. Current documentation requires a paid subscription for compute Spaces even when CPU Basic has no hourly hardware charge; do not assume it is universally free. [Spaces overview](https://huggingface.co/docs/hub/spaces-overview)

1. Create a **Docker Space**. Copy the contents of `backend/` into the Space root, excluding `.venv`, `.env`, tests, and caches.
2. Add a Space-root `README.md` with front matter:

   ```yaml
   ---
   title: AI Research API
   emoji: 🔎
   colorFrom: yellow
   colorTo: blue
   sdk: docker
   app_port: 7860
   ---
   ```

3. Add the same backend secrets in Space Settings; set `PORT=7860` and your Vercel origin.
4. Deploy. The image runs as UID 1000 and stores no durable state on disk.
5. Change `VITE_API_URL` and the Supabase Vault backend URL to the Space's public `https://OWNER-SPACE.hf.space/api/v1` endpoint (append `/internal/wake` only in Vault).
6. Verify unauthenticated `/health` and authenticated feature calls from Vercel. A private Space needs an additional hosting-access arrangement; Supabase Auth alone does not bypass a private Space's outer access gate.

## 9. Final deployment checks

- Sign up, confirm email, sign out, sign back in, refresh, reset password, and change it.
- Use two accounts and verify private history, conversations, schedules, and exports remain isolated.
- Run one short live report and verify the cited URLs against the saved excerpts.
- Send a chat follow-up; download PDF and Markdown.
- Run a schedule and verify one report per scheduled occurrence.
- Verify Analytics reflects actual provider usage and Admin can change a limit.
- Run built-in red-team tests; use a custom endpoint only if you own it or have permission.
- Run `scripts/smoke.py` with `API_URL` and a current Supabase access `TOKEN` in your local environment.
- Keep external-service accounts within their free allowances. Supabase Free projects may pause after inactivity; export/backup important data and verify current account limits. [Supabase production checklist](https://supabase.com/docs/guides/deployment/going-into-prod)
