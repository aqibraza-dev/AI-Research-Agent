# Host AI Research Agent v2 online: step-by-step tutorial

This tutorial deploys the existing `research-agent-v2/` application. Follow the numbered steps in order. Commands assume Linux/macOS and are run from the v2 directory unless stated otherwise. Dashboard labels can change; use the corresponding settings if a label differs.

The finished deployment is:

| Service | What it runs |
| --- | --- |
| Vercel | React frontend: the website users open |
| Render | FastAPI backend and its background research worker |
| Supabase | PostgreSQL database and user authentication |
| Upstash | Redis cache and request throttling |
| OpenRouter | Free-model inference |
| Tavily | Live web search |
| SMTP provider | Signup verification and password-reset emails |

Scheduled reports appear inside the app. SMTP is for authentication emails, not report delivery.

## 1. Understand the hosting limits

Use this setup for a personal or hobby deployment. Render Free sleeps after 15 minutes without inbound traffic, and waking can take about a minute. Its filesystem is temporary; this application stores durable data in Supabase. Schedules are best effort, not guaranteed to run at an exact minute. [Render limitations](https://render.com/docs/free)

Vercel Hobby permits personal, noncommercial use. Select an eligible paid plan for commercial use. [Vercel Hobby](https://vercel.com/docs/plans/hobby)

Tavily currently includes 1,000 monthly credits; basic search consumes one credit. Leave paid overage disabled for a free deployment. OpenRouter has shared provider/account limits in addition to the app's 50,000-token daily limit per user. [Tavily pricing](https://docs.tavily.com/documentation/api-credits), [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits)

Supabase, Redis, and email providers have their own allowances. Check the plan selected during account creation. Custom SMTP may require a verified sending domain; domain registration or your chosen email plan can introduce a cost.

## 2. Prepare the code and accounts

Create accounts for GitHub, Supabase, Upstash, OpenRouter, Tavily, Render, and Vercel. Render and Vercel need permission to read the GitHub repository containing your application.

For this tutorial, keep the folder structure as:

```text
YOUR-REPOSITORY/
  research-agent-v2/
    backend/
    frontend/
    supabase/
    scripts/
    docs/
    render.yaml
```

If you instead create a repository whose root directly contains `backend/` and `frontend/`, use `backend` and `frontend` as the hosting root directories later. The supplied Render Blueprint also needs its `rootDir` adjusted in that arrangement.

On your computer, open a terminal:

```bash
cd "/home/ubml/Desktop/projects/Ai-Research-Agent (Copy)/research-agent-v2"
python3 --version
node --version
```

Use Python 3.12+ and Node 22+. Prepare the operator tools:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.lock
```

If `backend/.env` does not exist, copy `backend/.env.example` to `backend/.env`. Do not overwrite an environment file you already configured. Edit it locally with your editor. The frontend's example may already have project-specific public values: use the project you actually created below, rather than blindly copying those values.

Publish the source to a private GitHub repository using your normal Git workflow or GitHub Desktop. Include the Dockerfile, lockfiles, migrations, scripts, `vercel.json`, and `.gitignore`. Exclude actual `.env` files, `.venv`, `node_modules`, `dist`, and test artifacts. Review the files before publishing; do not upload the entire parent project without checking its contents. No hosting credentials need to be committed.

**Checkpoint:** GitHub shows `research-agent-v2/backend/Dockerfile` and `research-agent-v2/frontend/package.json`.

## 3. Create a fresh Supabase project

1. Open the Supabase dashboard and create a new project.
2. Choose your organization and available Free plan.
3. Give the project a name such as `research-agent`.
4. Generate a strong database password and save it privately.
5. Select a region close to the Render region you intend to use.
6. Wait for the database to finish provisioning.

From the project's **Connect** dialog, choose the **transaction pooler** connection string, normally on port `6543`. Copy the exact hostname and username from your own project; do not invent them.

The format resembles:

```text
postgresql://postgres.PROJECT_REF:ENCODED_PASSWORD@POOLER_HOST:6543/postgres
```

Replace only the password placeholder with your database password. Special characters in a URL password need percent encoding. To encode it without placing it in shell history, run locally:

```bash
python3 - <<'PY'
from getpass import getpass
from urllib.parse import quote
print(quote(getpass('Database password: '), safe=''))
PY
```

The output is still a sensitive password representation: copy it only into the private connection string. The app disables prepared-statement caching for transaction pooling. [Supabase connection options](https://supabase.com/docs/guides/database/connecting-to-postgres)

Find the project's API settings or Connect dialog and copy:

- Project URL, such as `https://PROJECT_REF.supabase.co`.
- Publishable API key. A legacy `anon` key is also supported.

The publishable key is intended for browser use. Do not substitute a secret key or `service_role` key.

Set these values in local `backend/.env`:

```dotenv
DATABASE_URL=postgresql://postgres.PROJECT_REF:ENCODED_PASSWORD@POOLER_HOST:6543/postgres
DATABASE_SSL=true
SUPABASE_URL=https://PROJECT_REF.supabase.co
SUPABASE_PUBLISHABLE_KEY=YOUR_PROJECT_PUBLISHABLE_KEY
```

## 4. Install the database tables and policies

Use the migration command from the v2 directory:

```bash
backend/.venv/bin/python scripts/manage.py migrate
```

On a fresh database, expect:

```text
Applied 001_core.sql
Applied 002_functions.sql
```

Open Supabase's Table Editor and confirm tables such as `profiles`, `reports`, `conversations`, `schedules`, `jobs`, and `usage_events` exist.

The script records completed migrations, so rerunning the command skips already-applied files. Use this method consistently. Do not also paste the migrations into SQL Editor after running the command. If you previously installed them manually, consult the existing deployment guide before using the migration runner.

**Checkpoint:** Migrations finish without errors. Do not proceed with a failed or partially configured database.

## 5. Configure Supabase email authentication

1. Open **Authentication → Providers** or **Sign In / Providers**.
2. Enable Email authentication.
3. Keep email confirmation enabled for public signup.
4. Open **Authentication → Email / SMTP Settings** and configure a custom SMTP service for public users.
5. Enter the SMTP host, port, username, password, sender email, and sender name supplied by your email provider.
6. Complete any sender/domain verification requested by that provider.
7. Leave authentication email templates using Supabase's generated confirmation links unless you are deliberately implementing a custom flow.

Supabase's default email sender is restricted to project-team addresses and currently has a very small hourly allowance. A public visitor may receive an “Email address not authorized” error until custom SMTP is configured. For an initial private test, use an authorized team email. [Supabase SMTP setup](https://supabase.com/docs/guides/auth/auth-smtp)

You will set production Site URL and redirect URLs in step 10, after Vercel gives you the website address.

## 6. Create Upstash Redis

1. Sign in to the Upstash console.
2. Create a Redis database and select the available free plan.
3. Choose a nearby region.
4. Open its connection details and copy the Redis protocol connection string with TLS.
5. Put it into `backend/.env`:

```dotenv
REDIS_URL=rediss://default:YOUR_PASSWORD@YOUR_REDIS_HOST:6379
```

Use the host, port, and password shown by Upstash. This backend expects `rediss://`, not an HTTPS REST endpoint or REST token. If constructing the URL yourself, percent-encode password characters as needed. [Upstash setup](https://upstash.com/docs/redis/overall/getstarted)

Redis is recommended but optional. Leaving `REDIS_URL` empty uses database throttling without the Redis cache. Redis failure does not disable daily token limits or discard queued work.

## 7. Add provider keys and generate internal secrets

In OpenRouter, create an API key or use your existing `OPEN_ROUTER_FREE_API_KEY`. In Tavily, create a key dedicated to this application and keep paid overage disabled.

Add both to local `backend/.env`:

```dotenv
OPEN_ROUTER_FREE_API_KEY=YOUR_OPENROUTER_KEY
TAVILY_API_KEY=YOUR_TAVILY_KEY
```

The exact OpenRouter variable name includes underscores as shown. Users do not enter this key in the browser. The application supplies its fixed free-model catalog; unavailable models can still fail because provider capacity changes.

Generate the encryption key:

```bash
backend/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Save that output as `CREDENTIAL_ENCRYPTION_KEY`. Generate a different secret for scheduler wake requests:

```bash
backend/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Save that output as `SCHEDULER_SECRET`. Keep both in your password manager. The encryption key must be a Fernet key, not an arbitrary password. The scheduler secret must match the value installed in Supabase Vault later.

## 8. Deploy the backend on Render

1. Open Render and choose **New → Web Service**.
2. Connect GitHub and select your repository.
3. Choose the branch containing v2.
4. Configure the service:

| Setting | Value |
| --- | --- |
| Name | A unique name, such as `research-agent-api` |
| Runtime | Docker |
| Region | Near Supabase and Redis |
| Root Directory | `research-agent-v2/backend` |
| Dockerfile Path | `./Dockerfile` |
| Docker Build Context, if shown | `.` relative to the root directory |
| Instance type | Free |
| Health Check Path | `/health` |
| Docker Command override | Leave empty; use the Dockerfile command |

Add all these environment variables through Render's environment settings. Paste values without surrounding quotation marks:

| Variable | Value/source |
| --- | --- |
| `DATABASE_URL` | Your Supabase transaction-pooler URL |
| `DATABASE_SSL` | `true` |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_PUBLISHABLE_KEY` | That project's publishable/anon key |
| `OPEN_ROUTER_FREE_API_KEY` | Your OpenRouter key |
| `TAVILY_API_KEY` | Your Tavily key |
| `REDIS_URL` | Your Upstash TLS URL, or empty if intentionally unused |
| `CREDENTIAL_ENCRYPTION_KEY` | Generated Fernet key |
| `SCHEDULER_SECRET` | Generated scheduler secret |
| `ALLOWED_ORIGINS` | `http://localhost:5173` temporarily |
| `WORKER_ENABLED` | `true` |

Render supplies `PORT`; the Dockerfile uses it. Keep the included single-worker configuration. No additional Render database, Redis service, background-worker service, or local storage volume is required.

Click **Deploy Web Service**. Watch the build and runtime logs until the service is live. Record its URL, for example:

```text
https://YOUR-SERVICE.onrender.com
```

Open this in your browser:

```text
https://YOUR-SERVICE.onrender.com/health
```

Expected result:

```json
{"status":"ok"}
```

If the free service was asleep, allow it to wake and try again. A successful health response verifies application/database availability, not model credentials or SMTP; test those later.

**Checkpoint:** The deployed `/health` endpoint responds successfully.

## 9. Deploy the frontend on Vercel

1. Open Vercel and choose **Add New → Project**.
2. Import the same GitHub repository.
3. Set **Root Directory** to `research-agent-v2/frontend`.
4. Select the **Vite** framework preset.
5. Set Install Command to `npm ci`, Build Command to `npm run build`, and Output Directory to `dist`.
6. Use Node 22.x where available, or a supported newer version.
7. Add the following environment variables for **Production** before deploying:

```dotenv
VITE_API_URL=https://YOUR-SERVICE.onrender.com/api/v1
VITE_SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=YOUR_PROJECT_PUBLISHABLE_KEY
```

`VITE_API_URL` must end in `/api/v1`. Use HTTPS and your real Render address, not `localhost`. Both Supabase values must match the backend's project.

Do not put OpenRouter, Tavily, Redis, database, encryption, or scheduler secrets into Vercel frontend variables. Values prefixed with `VITE_` are bundled into browser code.

Click **Deploy** and save the stable production domain, for example:

```text
https://YOUR-APP.vercel.app
```

The project includes an SPA rewrite in `vercel.json`, which allows direct visits and refreshes on routes such as `/history`. Vite variables are read at build time: redeploy after changing them. [Vite deployment on Vercel](https://vercel.com/docs/frameworks/frontend/vite)

## 10. Connect the production URLs

There are three different settings; configure all three:

| Where | Setting | Example |
| --- | --- | --- |
| Vercel | `VITE_API_URL` | `https://YOUR-SERVICE.onrender.com/api/v1` |
| Render | `ALLOWED_ORIGINS` | `https://YOUR-APP.vercel.app` |
| Supabase Auth | Site URL | `https://YOUR-APP.vercel.app` |

In Render, replace the temporary origin with your exact Vercel production origin and save/redeploy. An origin has no path or trailing slash. For local development too, use:

```dotenv
ALLOWED_ORIGINS=https://YOUR-APP.vercel.app,http://localhost:5173
```

In Supabase **Authentication → URL Configuration**, set Site URL to your Vercel production origin. Add these redirect entries:

```text
https://YOUR-APP.vercel.app/auth/callback
https://YOUR-APP.vercel.app/account
https://YOUR-APP.vercel.app/account?recovery=1
```

The last URL is the application's password-recovery destination. Save the settings. If you test Vercel preview domains, configure those domains separately; the production origin does not automatically authorize every preview URL.

**Checkpoint:** The frontend opens, backend requests succeed, and both services refer to the same Supabase project.

## 11. Create your account and become administrator

1. Open your Vercel production site.
2. Select **Create an account** and register with your email.
3. Open the verification email and follow its link.
4. Sign in and wait for your profile to load.
5. On your trusted local computer, confirm `backend/.env` still points to this deployment's database.
6. From the v2 directory, run:

```bash
backend/.venv/bin/python scripts/manage.py promote-admin your-email@example.com
```

Replace the email with the account you just created. Expected output starts with `Administrator promoted:`. Reload the website; **Admin panel** should appear.

If the script says no matching user exists, ensure you signed up against the same Supabase project and successfully opened the authenticated application. Do not grant admin rights by editing browser storage or signup metadata.

The default app allowance is 50,000 input-plus-output tokens per user per UTC day. Use the Admin panel to change the default or an individual user's allowance. This does not increase OpenRouter's account-wide capacity.

## 12. Enable scheduled research with Supabase Cron

First ensure an ordinary research request works. Then activate hosted scheduling:

1. Open `supabase/scheduler.sql` in your local editor to inspect the template.
2. Copy its contents into Supabase **SQL Editor → New query**.
3. In the SQL Editor copy, replace `https://YOUR-SERVICE.onrender.com/api/v1/internal/wake` with your actual backend wake URL.
4. Replace `REPLACE_WITH_RANDOM_SCHEDULER_SECRET` with the same `SCHEDULER_SECRET` stored in Render.
5. Run the query once.
6. Keep the repository's template unchanged so the real secret is not committed.

This installs the required extensions, stores the wake URL/secret in Vault, and registers `research-dispatch`. It runs every minute, enqueues due occurrences, and contacts the backend only while work is pending. [Supabase Cron](https://supabase.com/docs/guides/cron)

Check registration in SQL Editor:

```sql
select jobid, jobname, schedule, active
from cron.job
where jobname = 'research-dispatch';
```

Check recent cron executions:

```sql
select status, return_message, start_time, end_time
from cron.job_run_details
where jobid in (
  select jobid from cron.job where jobname = 'research-dispatch'
)
order by start_time desc
limit 10;
```

Check recent HTTP delivery results:

```sql
select id, status_code, timed_out, error_msg, created
from net._http_response
order by created desc
limit 10;
```

A cron SQL success does not prove the HTTP wake succeeded; inspect both when diagnosing dispatch. An initial HTTP timeout during a Render cold start can occur. Pending jobs remain stored and later ticks can wake the backend again.

Create a **once** schedule in the application for 5–10 minutes ahead. Select the correct timezone. Confirm a run appears, a report completes, and an in-app notification arrives. Test pause/resume and editing afterward.

Do not rerun the entire installation script just to change the backend URL or scheduler secret: its `vault.create_secret` statements create new records. Instead, open Supabase Vault and edit the existing secrets named `research_backend_url` and `research_scheduler_secret`.

To disable hosted dispatch:

```sql
select cron.unschedule('research-dispatch');
```

The worker also checks schedules while it is awake. Pause schedules in the application if you want to stop their execution entirely.

## 13. Verify the online application

Run these tests against the hosted site:

| Test | Expected result |
| --- | --- |
| Signup and verification | Email arrives and its link returns to your production site |
| Login, refresh, logout, login again | Session restores correctly and logout protects private pages |
| Forgot password | Recovery email opens the account page; new password works |
| Short standard research | Search, draft, and review complete; source links are saved |
| History | Report remains after refreshing; rename and search work |
| Chat | Messages persist; attached report context works |
| Exports | Markdown and PDF downloads open correctly |
| Scheduler | One occurrence creates one report and a notification |
| Analytics | Usage and remaining allowance update |
| Admin | Limit changes save; audit records appear |
| Red teaming | A small built-in run finishes with classified results |
| Second account | Cannot see the first account's private work |
| Mobile and direct page refresh | Navigation and nested URLs work |

Start with short research and a small red-team run to conserve provider allowances. Confirm a report's source URLs actually match its evidence.

For optional read-only API smoke checks, obtain your own current Supabase user access token from an authenticated API request in your browser's Network panel. Treat it as a password. Use a local terminal, not a shared log or screenshot:

```bash
export API_URL='https://YOUR-SERVICE.onrender.com'
read -r -s -p 'Supabase user access token: ' TOKEN
export TOKEN
backend/.venv/bin/python scripts/smoke.py
unset TOKEN
```

Here `API_URL` is the backend origin **without** `/api/v1`, because the smoke script adds endpoint paths. Use the access-token value without the `Bearer ` prefix. Do not use the publishable API key as the token.

The implementation previously passed 38 backend tests and 5 local browser workflows. Those tests simulate external services; these hosted checks establish that your own credentials, email, providers, and deployments work together.

## 14. Troubleshoot common failures

| Symptom | What to check |
| --- | --- |
| Render build cannot find Dockerfile | Root is `research-agent-v2/backend`; Dockerfile is `./Dockerfile` |
| Backend exits on startup | Migration completion, `DATABASE_URL`, password encoding, `DATABASE_SSL=true`, and Render logs |
| Database “Tenant or user not found” | Copy the exact pooler username and hostname from Supabase Connect |
| Database connection cannot reach host | Use the pooler URL; check project availability and database network restrictions |
| “Connect your workspace” screen | Missing frontend Supabase variables; save them in Vercel and rebuild |
| Browser “Failed to fetch” | Wake `/health`, check `VITE_API_URL`, then exact Render `ALLOWED_ORIGINS` |
| Email address not authorized | Configure custom SMTP or use an authorized team address for private testing |
| Verification/reset links open localhost | Fix Supabase Site URL and redirect entries; request a new email |
| API returns 401 | Sign in again; confirm frontend and backend use the same Supabase project |
| App quota is exhausted | Check UTC reset time and user allowance in Admin; provider limits are separate |
| Provider returns 429 | Wait for provider reset/capacity; inspect Admin health and provider dashboard |
| Search fails | Check Tavily key, remaining credits, and the app's monthly search-attempt cap |
| A named free model is unavailable | Select `openrouter/free` from the allowed catalog and retry |
| Jobs remain queued | `WORKER_ENABLED=true`, Render runtime logs, DB access, Cron wake results |
| Wake endpoint returns 401 | Render and Vault scheduler secrets must match exactly |
| Redis reports connection errors | Use TLS Redis URL, not REST URL; check credentials and plan status |
| Scheduled job starts late | Free-service sleep/cold start; inspect history and missed-occurrence recovery |
| Deep-link refresh gives Vercel 404 | Confirm frontend root and included `vercel.json` are deployed |
| Admin panel is missing | Promote the correct account in the correct database, then reload |

Do not repeatedly retry ambiguous model failures without inspecting usage: the app can retain a conservative charge when a provider's actual usage is unknown. Operator reconciliation instructions are in `OPERATIONS.md`.

## 15. Publish future updates

1. Make changes locally and run the relevant checks.
2. If a release introduces database migrations, review and apply them with the operator script before deploying code that needs them.
3. Push your source to the connected branch.
4. Check Render and Vercel deployment status; enable their Git auto-deploy options or trigger manual deployment as appropriate.
5. After frontend environment changes, rebuild Vercel. After backend environment changes, redeploy/restart Render through its dashboard.
6. Recheck health, login, one research request, and scheduler history.

Keep the database and secrets stable across code deployments. Replacing an encryption key invalidates credentials encrypted under the old key. Back up important database contents before schema changes. Do not run the local destructive test-database fixture against your hosted Supabase project.

## 16. Optional: Hugging Face instead of Render

As checked for this guide, creating a Docker compute Space requires a paid plan: PRO for personal accounts, or Team/Enterprise for organizations. CPU Basic having no hourly hardware cost does not remove that requirement. Render is the default free backend path in this tutorial. [Hugging Face Spaces requirements](https://huggingface.co/docs/hub/spaces-overview)

If your account is eligible:

1. Create a Docker Space with CPU Basic hardware and public app access.
2. Put `Dockerfile`, `.dockerignore`, `requirements.lock`, and the `app/` directory from `backend/` at the Space repository root. Do not copy `.env`, `.venv`, or test code.
3. Add a root `README.md` containing:

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

4. Add the same backend configuration in Space Settings. Store credentials as Secrets, and set `PORT=7860`, `WORKER_ENABLED=true`, and your Vercel origin.
5. Wait for the Space build and verify the public `https://YOUR-SPACE-SUBDOMAIN.hf.space/health` endpoint. Use the actual app URL shown by Hugging Face.
6. Set Vercel `VITE_API_URL` to that origin plus `/api/v1`, then redeploy Vercel.
7. Edit the existing Supabase Vault `research_backend_url` to that origin plus `/api/v1/internal/wake`.
8. Repeat hosted authentication, research, export, and scheduler tests.

Private Spaces have an additional access gate and cannot be used directly by ordinary Vercel visitors under this configuration. Supabase authentication does not bypass that gate. Stop the old backend when intentionally switching hosts so deployment ownership remains clear.
