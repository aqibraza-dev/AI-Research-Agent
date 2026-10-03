create function public.reserve_tokens(p_user uuid,p_job uuid,p_step text,p_feature text,p_model text,p_amount integer,p_external boolean default false)
returns uuid language plpgsql set search_path=public as $$
declare d date := (now() at time zone 'UTC')::date; lim integer; used daily_usage; event_id uuid; p profiles;
begin
 select * into p from profiles where id=p_user for update;
 if not found or p.suspended or not coalesce((p.features->>p_feature)::boolean,false) then raise exception 'FEATURE_DISABLED'; end if;
 select coalesce(p.daily_limit,default_daily_limit) into lim from app_settings where id;
 insert into daily_usage(user_id,day) values(p_user,d) on conflict do nothing;
 select * into used from daily_usage where user_id=p_user and day=d for update;
 if p_amount<0 or (not p_external and used.charged+used.reserved+p_amount>lim) then raise exception 'QUOTA_EXCEEDED'; end if;
 insert into usage_events(user_id,job_id,step,feature,model,day,reservation,external)
 values(p_user,p_job,p_step,p_feature,p_model,d,case when p_external then 0 else p_amount end,p_external) returning id into event_id;
 if not p_external then update daily_usage set reserved=reserved+p_amount where user_id=p_user and day=d; end if;
 return event_id;
end $$;
create function public.settle_tokens(p_id uuid,p_status text,p_input integer,p_output integer,p_cost numeric,p_response jsonb,p_model text,p_provider text,p_latency integer)
returns void language plpgsql set search_path=public as $$
declare e usage_events; actual integer;
begin
 select * into e from usage_events where id=p_id for update;
 if not found or e.status<>'reserved' then return; end if;
 actual := case when p_status='confirmed' then greatest(0,p_input)+greatest(0,p_output) when p_status='rejected' then 0 else e.reservation end;
 update usage_events set status=p_status,input_tokens=p_input,output_tokens=p_output,charged_tokens=case when e.external then 0 else actual end,
 cost_usd=p_cost,response=p_response,model=p_model,provider_id=p_provider,latency_ms=p_latency where id=p_id;
 if not e.external then update daily_usage set reserved=reserved-e.reservation,charged=charged+actual where user_id=e.user_id and day=e.day; end if;
end $$;

-- Anchor wall-clock recurrence in the chosen timezone. PostgreSQL chooses standard
-- time in an ambiguous hour, so only one occurrence is produced. Gap times skipped.
create function public.schedule_next(s public.schedules, after_time timestamptz) returns timestamptz
language plpgsql stable set search_path=public as $$
declare local_start timestamp; dt date; local_candidate timestamp; candidate timestamptz; month_day integer; i integer;
begin
 if s.frequency='once' then
  if s.start_at>after_time and (s.end_at is null or s.start_at<=s.end_at) then return s.start_at; end if;
  return null;
 end if;
 local_start:=s.start_at at time zone s.timezone;
 for i in 0..370 loop
  dt:=greatest(local_start::date,(after_time at time zone s.timezone)::date)+i;
  if s.frequency='weekly' and not(extract(isodow from dt)::integer=any(s.weekdays)) then continue; end if;
  month_day:=least(extract(day from local_start)::integer,extract(day from (date_trunc('month',dt)+interval '1 month - 1 day'))::integer);
  if s.frequency='monthly' and extract(day from dt)::integer<>month_day then continue; end if;
  local_candidate:=dt+local_start::time;
  candidate:=local_candidate at time zone s.timezone;
  if candidate at time zone s.timezone<>local_candidate then continue; end if;
  if candidate>=s.start_at and candidate>after_time then
   if s.end_at is not null and candidate>s.end_at then return null; end if;
   return candidate;
  end if;
 end loop;
 return null;
end $$;
create function public.enqueue_due() returns integer language plpgsql set search_path=public as $$
declare s schedules; candidate timestamptz; following timestamptz; jid uuid; rid uuid; total integer:=0; n integer;
begin
 for s in select sc.* from schedules sc join profiles p on p.id=sc.user_id
 where not sc.paused and sc.next_run_at<=now() and not p.suspended
 and coalesce((p.features->>'schedule')::boolean,false) and coalesce((p.features->>'research')::boolean,false)
 order by sc.next_run_at limit 100 for update of sc skip locked loop
  candidate:=s.next_run_at; n:=0;
  loop
   following:=schedule_next(s,candidate);
   exit when following is null or following>now() or n>=3660;
   insert into schedule_runs(user_id,schedule_id,occurrence_at,status) values(s.user_id,s.id,candidate,'skipped') on conflict do nothing;
   candidate:=following; n:=n+1;
  end loop;
  -- An exceptionally old backlog is drained in bounded transactions.
  if following is not null and following<=now() then
   update schedules set next_run_at=following where id=s.id; continue;
  end if;
  if exists(select 1 from jobs where schedule_id=s.id and occurrence_at=candidate) then
   update schedules set next_run_at=following where id=s.id; continue;
  end if;
  insert into reports(user_id,title,topic,depth,model) values(s.user_id,s.title,s.topic,s.depth,s.model) returning id into rid;
  insert into jobs(user_id,kind,payload,report_id,schedule_id,occurrence_at)
   values(s.user_id,'research',jsonb_build_object('topic',s.topic,'depth',s.depth,'model',s.model,'fresh',true),rid,s.id,candidate) returning id into jid;
  insert into schedule_runs(user_id,schedule_id,occurrence_at,job_id,status) values(s.user_id,s.id,candidate,jid,'queued') on conflict do nothing;
  update schedules set next_run_at=following where id=s.id;
  total:=total+1;
 end loop;
 delete from job_credentials where expires_at<now();
 delete from request_limits where window_start<now()-interval '1 day';
 return total;
end $$;
revoke all on function public.reserve_tokens(uuid,uuid,text,text,text,integer,boolean) from public,anon,authenticated;
revoke all on function public.settle_tokens(uuid,text,integer,integer,numeric,jsonb,text,text,integer) from public,anon,authenticated;
revoke all on function public.schedule_next(public.schedules,timestamptz) from public,anon,authenticated;
revoke all on function public.enqueue_due() from public,anon,authenticated;
