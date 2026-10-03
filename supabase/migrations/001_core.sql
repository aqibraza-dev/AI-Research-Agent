-- Apply once, in order, using Supabase SQL Editor or the migration script.
create extension if not exists pgcrypto;
create table public.app_settings (
 id boolean primary key default true check(id), default_daily_limit integer not null default 50000 check(default_daily_limit between 0 and 10000000),
 search_monthly_limit integer not null default 1000 check(search_monthly_limit between 0 and 1000)
);
insert into public.app_settings(id) values(true);
create table public.profiles (
 id uuid primary key references auth.users(id) on delete cascade,
 display_name text not null default '', email text not null default '',
 role text not null default 'user' check(role in ('user','admin')),
 suspended boolean not null default false, daily_limit integer check(daily_limit between 0 and 10000000),
 features jsonb not null default '{"research":true,"chat":true,"redteam":true,"schedule":true}',
 created_at timestamptz not null default now()
);
create function public.create_profile() returns trigger language plpgsql security definer set search_path='' as $$
begin
 insert into public.profiles(id,email,display_name) values(new.id,coalesce(new.email,''),left(coalesce(new.raw_user_meta_data->>'display_name',''),100));
 return new;
end $$;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.create_profile();
insert into public.profiles(id,email,display_name) select id,coalesce(email,''),left(coalesce(raw_user_meta_data->>'display_name',''),100) from auth.users on conflict do nothing;
create table public.reports (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references public.profiles(id) on delete cascade,
 title text not null, topic text not null, depth text not null default 'standard', model text not null,
 content text not null default '', sources jsonb not null default '[]', status text not null default 'queued',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.conversations (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references public.profiles(id) on delete cascade,
 title text not null default 'New chat', report_id uuid references public.reports(id) on delete set null,
 created_at timestamptz not null default now()
);
create table public.messages (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references public.profiles(id) on delete cascade,
 conversation_id uuid not null references public.conversations(id) on delete cascade,
 role text not null check(role in ('user','assistant')), content text not null, created_at timestamptz not null default now()
);
create table public.schedules (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references public.profiles(id) on delete cascade,
 title text not null, topic text not null, depth text not null, model text not null,
 frequency text not null check(frequency in ('once','daily','weekly','monthly')),
 timezone text not null, start_at timestamptz not null, end_at timestamptz,
 weekdays integer[] not null default '{1}', paused boolean not null default false,
 next_run_at timestamptz, created_at timestamptz not null default now()
);
create table public.jobs (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references public.profiles(id) on delete cascade,
 kind text not null check(kind in ('research','chat','redteam')), status text not null default 'queued' check(status in ('queued','running','completed','failed','cancelled')),
 stage text not null default 'Queued', payload jsonb not null, checkpoint jsonb not null default '{}', result jsonb,
 report_id uuid references public.reports(id) on delete cascade, conversation_id uuid references public.conversations(id) on delete cascade,
 schedule_id uuid references public.schedules(id) on delete set null, occurrence_at timestamptz,
 cancel_requested boolean not null default false, error text, attempts integer not null default 0,
 available_at timestamptz not null default now(), lease_until timestamptz, lease_token uuid,
 created_at timestamptz not null default now(), finished_at timestamptz,
 unique(schedule_id,occurrence_at)
);
create unique index one_chat_job on public.jobs(conversation_id) where kind='chat' and status in ('queued','running');
create table public.schedule_runs (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references public.profiles(id) on delete cascade,
 schedule_id uuid not null references public.schedules(id) on delete cascade,
 occurrence_at timestamptz not null, job_id uuid references public.jobs(id) on delete set null,
 status text not null, unique(schedule_id,occurrence_at)
);
create table public.daily_usage (
 user_id uuid not null references public.profiles(id) on delete cascade, day date not null,
 charged bigint not null default 0, reserved bigint not null default 0,
 primary key(user_id,day), check(charged>=0 and reserved>=0)
);
create table public.usage_events (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references public.profiles(id) on delete cascade,
 job_id uuid references public.jobs(id) on delete set null, step text not null, feature text not null, model text not null,
 day date not null default (now() at time zone 'UTC')::date, reservation integer not null,
 input_tokens integer, output_tokens integer, charged_tokens integer not null default 0,
 cost_usd numeric, external boolean not null default false,
 status text not null default 'reserved' check(status in ('reserved','confirmed','estimated','rejected')),
 response jsonb, provider_id text, latency_ms integer,
 created_at timestamptz not null default now(), unique(job_id,step)
);
create table public.job_credentials (
 job_id uuid primary key references public.jobs(id) on delete cascade,
 ciphertext text not null, expires_at timestamptz not null default now()+interval '1 hour'
);
create table public.notifications (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references public.profiles(id) on delete cascade,
 title text not null, message text not null, job_id uuid references public.jobs(id) on delete set null,
 read boolean not null default false, created_at timestamptz not null default now()
);
create table public.audit_events (
 id bigint generated always as identity primary key, actor_id uuid references public.profiles(id) on delete set null,
 action text not null, target_id text, before_value jsonb, after_value jsonb, created_at timestamptz not null default now()
);
create table public.request_limits (key text primary key, window_start timestamptz not null, count integer not null);
create table public.search_usage (month date primary key, credits integer not null default 0);
create index jobs_pending on public.jobs(status,available_at);
create index reports_owner_date on public.reports(user_id,created_at desc);
create index messages_owner_date on public.messages(conversation_id,created_at);
create index usage_owner_date on public.usage_events(user_id,created_at desc);
create index schedules_due on public.schedules(next_run_at) where not paused;

-- All browser access is read-only. Mutation and protected fields go through the API.
do $$ declare t text; begin
 foreach t in array array['profiles','reports','conversations','messages','schedules','jobs','schedule_runs','daily_usage','usage_events','notifications','job_credentials','audit_events','app_settings','request_limits','search_usage'] loop
  execute format('alter table public.%I enable row level security',t);
  execute format('revoke all on public.%I from anon, authenticated',t);
 end loop;
 foreach t in array array['reports','conversations','messages','schedules','schedule_runs','daily_usage','notifications'] loop
  execute format('grant select on public.%I to authenticated',t);
  execute format('create policy own_read on public.%I for select to authenticated using (user_id = (select auth.uid()))',t);
 end loop;
end $$;
-- Profiles are readable only by their owner, never writable by JWT user metadata.
grant select on public.profiles to authenticated;
create policy own_profile on public.profiles for select to authenticated using(id=(select auth.uid()));
revoke all on function public.create_profile() from public, anon, authenticated;
