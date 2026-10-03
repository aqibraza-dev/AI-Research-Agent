import asyncio
import contextlib
import difflib
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID
import httpx
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from . import db, worker, cache
from .config import settings, MODELS
from .auth import current_user, admin, feature
from .schemas import Research, Rename, Conversation, Message, Schedule, RedTeam, UserUpdate, SettingsUpdate
from .security import encrypt_key, public_url, TargetError
from .exports import pdf_bytes


@asynccontextmanager
async def lifespan(app):
    await db.connect()
    task = asyncio.create_task(worker.run()) if settings().worker_enabled else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if cache.client:
        await cache.client.aclose()
    await db.close()


app = FastAPI(title="AI Research Agent", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[s.strip() for s in settings().allowed_origins.split(",")],
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": str(exc.status_code), "message": str(exc.detail)}}
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation",
                "message": "; ".join(".".join(str(x) for x in e["loc"][1:]) + ": " + e["msg"] for e in exc.errors()),
            }
        },
    )


@app.exception_handler(Exception)
async def unexpected(request, exc):
    logging.getLogger(__name__).error(
        "Request failed: %s %s (%s)", request.method, request.url.path, type(exc).__name__
    )
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal", "message": "Request failed. Check server configuration and try again."}},
    )


async def owned(table, id, user):
    if table not in {"reports", "conversations", "messages", "schedules", "jobs", "notifications"}:
        raise ValueError("Unknown table")
    row = await db.one(f"select * from {table} where id=$1 and user_id=$2", id, user["id"])
    if not row:
        raise HTTPException(404, "Not found")
    return row


async def guard(user, name):
    feature(user, name)
    await cache.throttle(user["id"])


@app.get("/health")
async def health():
    try:
        await db.one("select 1 as ok")
        return {"status": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})


@app.post("/api/v1/internal/wake")
async def wake(request: Request):
    secret = settings().scheduler_secret
    if not secret or not hmac.compare_digest(request.headers.get("X-Scheduler-Secret", ""), secret):
        raise HTTPException(401, "Unauthorized")
    worker.wake.set()
    return {"accepted": True}


@app.get("/api/v1/profile")
async def profile(user=Depends(current_user)):
    usage = await db.one(
        "select charged,reserved from daily_usage where user_id=$1 and day=(now() at time zone 'UTC')::date", user["id"]
    ) or {"charged": 0, "reserved": 0}
    global_settings = await db.one("select * from app_settings where id")
    return {
        **user,
        "usage": usage,
        "effective_limit": user["daily_limit"]
        if user["daily_limit"] is not None
        else global_settings["default_daily_limit"],
        "reset_timezone": "UTC",
    }


@app.get("/api/v1/models")
async def models(user=Depends(current_user)):
    return {
        "items": [{"id": id, "default": i == 0, "read_only": True, "cost_per_token": 0} for i, id in enumerate(MODELS)]
    }


async def create_research(c, user_id, req):
    r = await c.fetchrow(
        "insert into reports(user_id,title,topic,depth,model) values($1,$2,$2,$3,$4) returning *",
        user_id,
        req.topic,
        req.depth,
        req.model,
    )
    j = await c.fetchrow(
        "insert into jobs(user_id,kind,payload,report_id) values($1,'research',$2,$3) returning *",
        user_id,
        req.model_dump(),
        r["id"],
    )
    return {"report": dict(r), "job": dict(j)}


@app.post("/api/v1/research", status_code=202)
async def start_research(req: Research, user=Depends(current_user)):
    await guard(user, "research")
    async with db.pool.acquire() as c:
        async with c.transaction():
            result = await create_research(c, user["id"], req)
    worker.wake.set()
    return result


@app.get("/api/v1/research")
async def research_history(
    q: str = "",
    status: str = "",
    after: datetime | None = None,
    before: datetime | None = None,
    page: int = Query(1, ge=1),
    user=Depends(current_user),
):
    clause = "user_id=$1 and ($2='' or title ilike '%'||$2||'%') and ($3='' or status=$3) and ($4::timestamptz is null or created_at >= $4) and ($5::timestamptz is null or created_at <= $5)"
    args = (user["id"], q, status, after, before)
    items = await db.many(
        f"select id,title,topic,depth,model,status,created_at from reports where {clause} order by created_at desc,id desc limit 20 offset $6",
        *args,
        (page - 1) * 20,
    )
    count = await db.one(f"select count(*) as total from reports where {clause}", *args)
    return {"items": items, "page": page, **count}


@app.get("/api/v1/research/{id}")
async def report(id: UUID, user=Depends(current_user)):
    r = await owned("reports", id, user)
    r["job"] = await db.one(
        "select id,status,stage,error from jobs where report_id=$1 and user_id=$2 order by created_at desc limit 1",
        id,
        user["id"],
    )
    return r


@app.patch("/api/v1/research/{id}")
async def rename_report(id: UUID, req: Rename, user=Depends(current_user)):
    await owned("reports", id, user)
    return await db.one("update reports set title=$2 where id=$1 returning *", id, req.title)


@app.delete("/api/v1/research/{id}")
async def delete_report(id: UUID, user=Depends(current_user)):
    await owned("reports", id, user)
    active = await db.one("select id from jobs where report_id=$1 and status in ('queued','running')", id)
    if active:
        raise HTTPException(409, "Cancel active research before deleting it")
    await db.execute("delete from reports where id=$1 and user_id=$2", id, user["id"])
    return {"deleted": True}


@app.post("/api/v1/research/{id}/rerun", status_code=202)
async def rerun_report(id: UUID, user=Depends(current_user)):
    r = await owned("reports", id, user)
    return await start_research(
        Research(topic=r["topic"], depth=r["depth"], model=r["model"] if r["model"] in MODELS else MODELS[0]), user
    )


@app.get("/api/v1/research/{id}/diff")
async def diff(id: UUID, other: UUID, user=Depends(current_user)):
    r = await owned("reports", id, user)
    old = await owned("reports", other, user)
    return {
        "diff": "\n".join(
            difflib.unified_diff(
                old["content"].splitlines(),
                r["content"].splitlines(),
                fromfile=old["title"],
                tofile=r["title"],
                lineterm="",
            )
        )
        or "No changes."
    }


@app.get("/api/v1/jobs")
async def jobs(kind: str = "", page: int = Query(1, ge=1), user=Depends(current_user)):
    return {
        "items": await db.many(
            "select id,kind,status,stage,error,report_id,conversation_id,created_at from jobs where user_id=$1 and ($2='' or kind=$2) order by created_at desc limit 20 offset $3",
            user["id"],
            kind,
            (page - 1) * 20,
        )
    }


@app.get("/api/v1/jobs/{id}")
async def job(id: UUID, user=Depends(current_user)):
    return await owned("jobs", id, user)


@app.post("/api/v1/jobs/{id}/cancel")
async def cancel(id: UUID, user=Depends(current_user)):
    await owned("jobs", id, user)
    async with db.pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "update jobs set cancel_requested=true,status=case when status='queued' then 'cancelled' else status end,finished_at=case when status='queued' then now() else finished_at end where id=$1 and status in ('queued','running') returning *",
                id,
            )
            if row and row["status"] == "cancelled":
                if row["report_id"]:
                    await c.execute("update reports set status='cancelled' where id=$1", row["report_id"])
                await c.execute("update schedule_runs set status='cancelled' where job_id=$1", id)
                await c.execute("delete from job_credentials where job_id=$1", id)
    return {"cancel_requested": True}


@app.get("/api/v1/conversations")
async def conversations(page: int = Query(1, ge=1), user=Depends(current_user)):
    return {
        "items": await db.many(
            "select * from conversations where user_id=$1 order by created_at desc limit 20 offset $2",
            user["id"],
            (page - 1) * 20,
        )
    }


@app.post("/api/v1/conversations")
async def new_conversation(req: Conversation, user=Depends(current_user)):
    feature(user, "chat")
    if req.report_id:
        await owned("reports", req.report_id, user)
    return await db.one(
        "insert into conversations(user_id,title,report_id) values($1,$2,$3) returning *",
        user["id"],
        req.title,
        req.report_id,
    )


@app.get("/api/v1/conversations/{id}")
async def conversation(id: UUID, page: int = Query(1, ge=1), user=Depends(current_user)):
    r = await owned("conversations", id, user)
    r["messages"] = await db.many(
        "select * from messages where conversation_id=$1 and user_id=$2 order by created_at desc,id desc limit 50 offset $3",
        id,
        user["id"],
        (page - 1) * 50,
    )
    r["messages"].reverse()
    r["job"] = await db.one(
        "select id,status,stage,error from jobs where conversation_id=$1 and user_id=$2 order by created_at desc limit 1",
        id,
        user["id"],
    )
    return r


@app.patch("/api/v1/conversations/{id}")
async def rename_conversation(id: UUID, req: Rename, user=Depends(current_user)):
    await owned("conversations", id, user)
    return await db.one("update conversations set title=$2 where id=$1 returning *", id, req.title)


@app.delete("/api/v1/conversations/{id}")
async def delete_conversation(id: UUID, user=Depends(current_user)):
    await owned("conversations", id, user)
    if await db.one("select id from jobs where conversation_id=$1 and status in ('queued','running')", id):
        raise HTTPException(409, "Cancel the active reply before deleting")
    await db.execute("delete from conversations where id=$1 and user_id=$2", id, user["id"])
    return {"deleted": True}


async def enqueue_chat(id, user, model, content=None, retry=False):
    await guard(user, "chat")
    async with db.pool.acquire() as c:
        async with c.transaction():
            conv = await c.fetchrow("select * from conversations where id=$1 and user_id=$2 for update", id, user["id"])
            if not conv:
                raise HTTPException(404, "Conversation not found")
            if await c.fetchval(
                "select exists(select 1 from jobs where conversation_id=$1 and status in ('queued','running'))", id
            ):
                raise HTTPException(409, "A reply is already in progress")
            if retry:
                last = await c.fetchrow(
                    "select status,payload from jobs where conversation_id=$1 order by created_at desc limit 1", id
                )
                if not last or last["status"] not in ("failed", "cancelled"):
                    raise HTTPException(409, "Only failed or cancelled replies can be retried")
                model = last["payload"]["model"]
            else:
                await c.execute(
                    "insert into messages(user_id,conversation_id,role,content) values($1,$2,'user',$3)",
                    user["id"],
                    id,
                    content,
                )
            job = await c.fetchrow(
                "insert into jobs(user_id,kind,payload,conversation_id) values($1,'chat',$2,$3) returning *",
                user["id"],
                {"model": model},
                id,
            )
    worker.wake.set()
    return dict(job)


@app.post("/api/v1/conversations/{id}/messages", status_code=202)
async def send_message(id: UUID, req: Message, user=Depends(current_user)):
    return await enqueue_chat(id, user, req.model, req.content)


@app.post("/api/v1/conversations/{id}/retry", status_code=202)
async def retry_message(id: UUID, user=Depends(current_user)):
    return await enqueue_chat(id, user, MODELS[0], retry=True)


@app.get("/api/v1/schedules")
async def schedules(page: int = Query(1, ge=1), user=Depends(current_user)):
    return {
        "items": await db.many(
            "select * from schedules where user_id=$1 order by created_at desc limit 20 offset $2",
            user["id"],
            (page - 1) * 20,
        )
    }


async def schedule_write(req, user, id=None, preview=False):
    feature(user, "schedule")
    async with db.pool.acquire() as c:
        async with c.transaction():
            await c.execute("select id from profiles where id=$1 for update", user["id"])
            if id:
                if not await c.fetchrow(
                    "select id from schedules where id=$1 and user_id=$2 for update", id, user["id"]
                ):
                    raise HTTPException(404, "Schedule not found")
            elif not preview and await c.fetchval("select count(*) from schedules where user_id=$1", user["id"]) >= 20:
                raise HTTPException(409, "Maximum 20 schedules per user")
            fields = req.model_dump()
            values = [user["id"], *fields.values()]
            names = ["user_id", *fields.keys()]
            if id:
                assignments = ",".join(f"{name}=${i + 1}" for i, name in enumerate(names))
                row = await c.fetchrow(
                    f"update schedules set {assignments} where id=${len(values) + 1} returning *", *values, id
                )
            else:
                sql = f"insert into schedules({','.join(names)}) values({','.join('$' + str(i + 1) for i in range(len(values)))}) returning *"
                row = await c.fetchrow(sql, *values)
            # New/edited schedules begin with the next future occurrence.
            row = await c.fetchrow(
                "update schedules s set next_run_at=schedule_next(s,now()-interval '1 second') where id=$1 returning *",
                row["id"],
            )
            result = dict(row)
            if preview:
                await c.execute("delete from schedules where id=$1", row["id"])
            return result


@app.post("/api/v1/schedules/preview")
async def schedule_preview(req: Schedule, user=Depends(current_user)):
    return await schedule_write(req, user, preview=True)


@app.post("/api/v1/schedules")
async def new_schedule(req: Schedule, user=Depends(current_user)):
    await cache.throttle(user["id"])
    return await schedule_write(req, user)


@app.put("/api/v1/schedules/{id}")
async def edit_schedule(id: UUID, req: Schedule, user=Depends(current_user)):
    return await schedule_write(req, user, id)


@app.get("/api/v1/schedules/{id}/runs")
async def schedule_runs(id: UUID, page: int = Query(1, ge=1), user=Depends(current_user)):
    await owned("schedules", id, user)
    return {
        "items": await db.many(
            "select * from schedule_runs where schedule_id=$1 and user_id=$2 order by occurrence_at desc limit 20 offset $3",
            id,
            user["id"],
            (page - 1) * 20,
        )
    }


@app.post("/api/v1/schedules/{id}/{action}")
async def schedule_action(id: UUID, action: str, user=Depends(current_user)):
    s = await owned("schedules", id, user)
    feature(user, "schedule")
    if action == "run-now":
        await guard(user, "research")
        async with db.pool.acquire() as c:
            async with c.transaction():
                result = await create_research(
                    c, user["id"], Research(topic=s["topic"], depth=s["depth"], model=s["model"])
                )
                j = await c.fetchrow(
                    "update jobs set payload=payload||'{\"fresh\":true}'::jsonb,schedule_id=$2,occurrence_at=now() where id=$1 returning *",
                    result["job"]["id"],
                    id,
                )
                await c.execute(
                    "insert into schedule_runs(user_id,schedule_id,occurrence_at,job_id,status) values($1,$2,$3,$4,'queued')",
                    user["id"],
                    id,
                    j["occurrence_at"],
                    j["id"],
                )
                result["job"] = dict(j)
        worker.wake.set()
        return result
    if action == "duplicate":
        fields = {k: s[k] for k in Schedule.model_fields}
        fields["title"] = s["title"][:190] + " (copy)"
        return await schedule_write(Schedule(**fields), user)
    if action not in ("pause", "resume"):
        raise HTTPException(404, "Unknown action")
    return await db.one(
        "update schedules s set paused=$2,next_run_at=case when $2 then next_run_at else schedule_next(s,now()) end where id=$1 returning *",
        id,
        action == "pause",
    )


@app.delete("/api/v1/schedules/{id}")
async def delete_schedule(id: UUID, user=Depends(current_user)):
    await owned("schedules", id, user)
    await db.execute("delete from schedules where id=$1 and user_id=$2", id, user["id"])
    return {"deleted": True}


@app.post("/api/v1/redteam", status_code=202)
async def start_redteam(req: RedTeam, user=Depends(current_user)):
    await guard(user, "redteam")
    ciphertext = None
    if req.target == "custom":
        try:
            public_url(req.base_url)
            ciphertext = encrypt_key(req.api_key)
        except TargetError as e:
            raise HTTPException(400, str(e))
    payload = req.model_dump(exclude={"api_key"})
    async with db.pool.acquire() as c:
        async with c.transaction():
            j = await c.fetchrow(
                "insert into jobs(user_id,kind,payload) values($1,'redteam',$2) returning *", user["id"], payload
            )
            if ciphertext:
                await c.execute("insert into job_credentials(job_id,ciphertext) values($1,$2)", j["id"], ciphertext)
    worker.wake.set()
    return dict(j)


@app.get("/api/v1/analytics")
async def analytics(days: int = Query(30, ge=1, le=366), page: int = Query(1, ge=1), user=Depends(current_user)):
    args = (user["id"], days)
    totals = await db.one(
        """select coalesce(sum(charged_tokens),0) tokens,coalesce(sum(reservation) filter(where status='reserved'),0) reserved,
       coalesce(sum(cost_usd),0) known_cost,count(*) filter(where cost_usd is null) unknown_cost_requests,
       count(*) requests,count(*) filter(where status='estimated') estimated_requests,coalesce(avg(latency_ms),0) latency_ms
       from usage_events where user_id=$1 and created_at>now()-make_interval(days=>$2)""",
        *args,
    )
    daily = await db.many(
        "select day,sum(charged_tokens) tokens from usage_events where user_id=$1 and created_at>now()-make_interval(days=>$2) group by day order by day",
        *args,
    )
    breakdown = await db.many(
        "select feature,model,sum(charged_tokens) tokens,count(*) requests from usage_events where user_id=$1 and created_at>now()-make_interval(days=>$2) group by feature,model",
        *args,
    )
    events = await db.many(
        "select id,feature,model,input_tokens,output_tokens,charged_tokens,cost_usd,status,external,latency_ms,created_at from usage_events where user_id=$1 and created_at>now()-make_interval(days=>$2) order by created_at desc limit 20 offset $3",
        *args,
        (page - 1) * 20,
    )
    counts = await db.one(
        "select count(*) total,count(*) filter(where status='failed') failed from jobs where user_id=$1 and created_at>now()-make_interval(days=>$2)",
        *args,
    )
    return {"totals": totals, "daily": daily, "breakdown": breakdown, "events": events, "jobs": counts}


@app.get("/api/v1/notifications")
async def notifications(page: int = Query(1, ge=1), user=Depends(current_user)):
    return {
        "items": await db.many(
            "select * from notifications where user_id=$1 order by created_at desc limit 20 offset $2",
            user["id"],
            (page - 1) * 20,
        )
    }


@app.post("/api/v1/notifications/{id}/read")
async def read_notification(id: UUID, user=Depends(current_user)):
    await owned("notifications", id, user)
    await db.execute("update notifications set read=true where id=$1", id)
    return {"read": True}


@app.get("/api/v1/exports/{kind}/{id}")
async def export(kind: str, id: UUID, format: str = "markdown", user=Depends(current_user)):
    if kind == "research":
        r = await owned("reports", id, user)
        if r["status"] != "completed":
            raise HTTPException(409, "Report is not ready")
        title = r["title"]
        content = r["content"]
    elif kind == "conversation":
        r = await owned("conversations", id, user)
        title = r["title"]
        rows = await db.many(
            "select role,content from messages where conversation_id=$1 and user_id=$2 order by created_at,id",
            id,
            user["id"],
        )
        content = "\n\n".join("## " + m["role"].title() + "\n\n" + m["content"] for m in rows)
    elif kind == "message":
        r = await owned("messages", id, user)
        if r["role"] != "assistant":
            raise HTTPException(400, "Only assistant messages can be exported")
        title = "Assistant response"
        content = r["content"]
    elif kind == "redteam":
        r = await owned("jobs", id, user)
        if r["kind"] != "redteam":
            raise HTTPException(404, "Not found")
        title = "Red-team screening results"
        results = (r["result"] or r["checkpoint"]).get("results", [])
        content = "Heuristic screening; review each result.\n\n" + "\n\n".join(
            f"## {x['suite']} — {x['outcome']}\n\n{x['reason']}\n\n### Prompt\n{x['prompt']}\n\n### Response\n{x['response']}"
            for x in results
        )
    else:
        raise HTTPException(404, "Unknown export type")
    if format not in ("markdown", "pdf"):
        raise HTTPException(400, "Use markdown or pdf")
    body = await asyncio.to_thread(pdf_bytes, title, content) if format == "pdf" else "# " + title + "\n\n" + content
    return Response(
        body,
        media_type="application/pdf" if format == "pdf" else "text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{kind}-{id}.{"pdf" if format == "pdf" else "md"}"'},
    )


@app.get("/api/v1/admin/overview")
async def overview(user=Depends(admin)):
    return {
        "users": await db.one("select count(*) total,count(*) filter(where suspended) suspended from profiles"),
        "jobs": await db.many("select status,count(*) total from jobs group by status"),
        "usage": await db.one(
            "select coalesce(sum(charged_tokens),0) tokens,coalesce(sum(cost_usd),0) known_cost,count(*) filter(where status='estimated') estimated from usage_events"
        ),
        "settings": await db.one("select * from app_settings where id"),
        "search": await db.many("select * from search_usage order by month desc limit 12"),
    }


@app.get("/api/v1/admin/users")
async def users(q: str = "", page: int = Query(1, ge=1), user=Depends(admin)):
    return {
        "items": await db.many(
            """select p.*,coalesce(d.charged,0) today_tokens,coalesce(d.reserved,0) reserved
      from profiles p left join daily_usage d on d.user_id=p.id and d.day=(now() at time zone 'UTC')::date
      where p.email ilike '%'||$1||'%' or p.display_name ilike '%'||$1||'%' order by p.created_at desc limit 20 offset $2""",
            q,
            (page - 1) * 20,
        )
    }


@app.get("/api/v1/admin/users/{id}/analytics")
async def user_analytics(id: UUID, user=Depends(admin)):
    target = await db.one("select * from profiles where id=$1", id)
    if not target:
        raise HTTPException(404, "User not found")
    return await analytics(30, 1, target)


@app.patch("/api/v1/admin/users/{id}")
async def update_user(id: UUID, req: UserUpdate, user=Depends(admin)):
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "No changes")
    if id == user["id"] and fields.get("suspended"):
        raise HTTPException(400, "Cannot suspend yourself")
    async with db.pool.acquire() as c:
        async with c.transaction():
            old = await c.fetchrow("select * from profiles where id=$1 for update", id)
            if not old:
                raise HTTPException(404, "User not found")
            if "features" in fields:
                fields["features"] = {**old["features"], **fields["features"]}
            sql = ",".join(f"{key}=${i + 2}" for i, key in enumerate(fields))
            new = await c.fetchrow(f"update profiles set {sql} where id=$1 returning *", id, *fields.values())
            await c.execute(
                "insert into audit_events(actor_id,action,target_id,before_value,after_value) values($1,$2,$3,$4,$5)",
                user["id"],
                "update_user",
                str(id),
                jsonable_encoder(dict(old)),
                jsonable_encoder(dict(new)),
            )
    return dict(new)


@app.put("/api/v1/admin/settings")
async def update_settings(req: SettingsUpdate, user=Depends(admin)):
    async with db.pool.acquire() as c:
        async with c.transaction():
            old = await c.fetchrow("select * from app_settings where id for update")
            new = await c.fetchrow(
                "update app_settings set default_daily_limit=$1,search_monthly_limit=$2 where id returning *",
                req.default_daily_limit,
                req.search_monthly_limit,
            )
            await c.execute(
                "insert into audit_events(actor_id,action,before_value,after_value) values($1,$2,$3,$4)",
                user["id"],
                "settings",
                dict(old),
                dict(new),
            )
    return dict(new)


@app.get("/api/v1/admin/audit")
async def audit(page: int = Query(1, ge=1), user=Depends(admin)):
    return {
        "items": await db.many(
            "select * from audit_events order by created_at desc limit 30 offset $1", (page - 1) * 30
        )
    }


@app.get("/api/v1/admin/jobs")
async def admin_jobs(user=Depends(admin)):
    return {
        "items": await db.many(
            "select id,user_id,kind,status,stage,error,created_at from jobs order by created_at desc limit 50"
        )
    }


@app.post("/api/v1/admin/jobs/{id}/cancel")
async def admin_cancel(id: UUID, user=Depends(admin)):
    j = await db.one("select * from jobs where id=$1", id)
    if not j:
        raise HTTPException(404, "Not found")
    result = await cancel(id, {"id": j["user_id"]})
    await db.execute(
        "insert into audit_events(actor_id,action,target_id) values($1,$2,$3)", user["id"], "cancel_job", str(id)
    )
    return result


@app.post("/api/v1/admin/users/{id}/pause-schedules")
async def pause_schedules(id: UUID, user=Depends(admin)):
    async with db.pool.acquire() as c:
        async with c.transaction():
            await c.execute("update schedules set paused=true where user_id=$1", id)
            await c.execute(
                "insert into audit_events(actor_id,action,target_id) values($1,$2,$3)",
                user["id"],
                "pause_schedules",
                str(id),
            )
    return {"paused": True}


@app.get("/api/v1/admin/health")
async def admin_health(user=Depends(admin)):
    redis_ok = False
    try:
        conn = cache.connection()
        redis_ok = bool(conn and await conn.ping())
    except Exception:
        pass
    provider = {}
    try:
        if not settings().open_router_free_api_key:
            raise ValueError("Provider is not configured")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": "Bearer " + settings().open_router_free_api_key},
            )
        if r.status_code == 200:
            raw = r.json()["data"]
            provider = {k: raw.get(k) for k in ("free_model_daily_requests", "usage_daily", "limit_remaining")}
    except Exception:
        pass
    return {
        "database": "ok",
        "redis": "ok" if redis_ok else "degraded — database throttle fallback",
        "worker_enabled": settings().worker_enabled,
        "search_configured": bool(settings().tavily_api_key),
        "provider": provider,
    }
