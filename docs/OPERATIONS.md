# Operations and troubleshooting

## Startup

- `/health` returns 503: check database URL/password, SSL, and Supabase project pause status. Use Supabase's transaction pooler if the host does not support the direct database's address family.
- Missing tables: run the migrations before starting the backend. Migrations are explicit, not performed on every boot.
- Browser cannot connect: ensure `VITE_API_URL` ends in `/api/v1`, backend CORS includes the exact frontend origin, and rebuild Vercel after changing frontend variables.
- Signup email missing: configure Supabase custom SMTP, allowed redirects, and email confirmation settings. In-app scheduling does not require an email delivery service.
- Profile missing: ensure the auth-user trigger and migration backfill were applied.
- Blank setup page: the frontend intentionally shows setup guidance when its Supabase environment variables are missing.

## Jobs and quotas

- A cold Render service can take time to wake. The frontend displays saved status and can reconnect after refresh.
- A quota rejection can happen before the displayed balance reaches zero. Reservations intentionally overestimate input tokens so parallel calls cannot spend the same allowance.
- Provider 429 means the shared upstream account/model limit, not necessarily the user's 50k cap. The app does not silently switch to a paid model.
- Jobs with an uncertain provider response fail safely instead of blindly repeating potentially billable work. Users can explicitly retry/rerun. Estimated ledger entries stay visible.
- A restart during an active call retains the conservative reservation. Completed model responses are reused when checkpoints resume. The three-claim recovery bound prevents endless worker restart loops.
- Cancellation is cooperative between bounded provider calls. A call already sent may finish and count toward usage. Its output is not committed as a completed result after cancellation is observed.
- Do not delete `usage_events` or reset database counters to “fix” limits. Use per-user/default controls or reconcile verified provider usage.

To reconcile an estimated ledger event after verifying the counts in the provider dashboard:

```bash
backend/.venv/bin/python scripts/manage.py reconcile-usage EVENT_UUID INPUT_TOKENS OUTPUT_TOKENS --cost 0
```

The command only accepts estimated events and adjusts the original UTC day. It is an operator action and writes an audit record.

## Scheduling

- Check the schedule's timezone, future `next_run_at`, pause state, user's feature permissions, and suspension status.
- Check Supabase `cron.job_run_details` and `net._http_response`; inspect `jobs`, `schedule_runs`, and notifications.
- Cron enqueues due work regardless of whether the backend is awake. A backend wake request may time out during cold start; the queued work remains and later ticks retry waking it.
- Cron only wakes when jobs exist. It is not a general keep-alive.
- Deleting a schedule stops future creation; an already-created job remains independently cancellable.
- Supabase project pausing stops Cron as well. Restore the project and backend, then inspect catch-up results.

## Secrets and red teaming

- Never publish `.env`, database passwords, custom target keys, or scheduler/encryption secrets.
- API exceptions intentionally avoid echoing provider response bodies or credentials.
- Invalid/expired custom credentials require a new red-team run and re-entry of the key.
- Rotating the Fernet key invalidates outstanding encrypted credentials. Cancel those runs first; completed history does not need the key.
- The default tests rely on the same safety instructions as normal platform calls. They are evidence for review, not formal scoring by an independent judge.
- Enable custom-target access only for trusted users through the redteam feature permission. Public endpoints can still be expensive for their owners; bounded runs and per-user throttling reduce accidental load.

## Deployment and data

- Keep one application process for the free default deployment. PostgreSQL leases protect work if multiple processes accidentally overlap, but extra workers increase provider pressure.
- Redis is disposable. PostgreSQL is not: arrange exports/backups of important research and database records.
- In-app content remains until users delete it; usage/audit records intentionally outlive report deletion. Account deletion/retention policy is an operator responsibility.
- No account billing/subscription UI or paid-model fallback is included.
