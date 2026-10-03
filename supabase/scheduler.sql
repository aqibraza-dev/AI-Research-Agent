-- Run AFTER deploying Render. Replace these placeholders in the SQL editor only.
-- Keep the same SCHEDULER_SECRET in Render and Vault; never commit its actual value.
create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;
select vault.create_secret('https://YOUR-SERVICE.onrender.com/api/v1/internal/wake','research_backend_url');
select vault.create_secret('REPLACE_WITH_RANDOM_SCHEDULER_SECRET','research_scheduler_secret');
create or replace function public.scheduler_tick() returns void language plpgsql security definer set search_path='' as $$
begin
 perform public.enqueue_due();
 if exists(select 1 from public.jobs where status in ('queued','running')) then
  perform net.http_post(
   url := (select decrypted_secret from vault.decrypted_secrets where name='research_backend_url'),
   headers := jsonb_build_object('Content-Type','application/json','X-Scheduler-Secret',
      (select decrypted_secret from vault.decrypted_secrets where name='research_scheduler_secret')),
   body := '{}'::jsonb, timeout_milliseconds := 5000);
 end if;
end $$;
revoke all on function public.scheduler_tick() from public,anon,authenticated;
select cron.schedule('research-dispatch','* * * * *','select public.scheduler_tick()');
-- To disable: select cron.unschedule('research-dispatch');
