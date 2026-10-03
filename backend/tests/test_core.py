import asyncio
import socket
from datetime import datetime, timezone, timedelta
from uuid import UUID
import pytest
from cryptography.fernet import Fernet
from app import db, cache, worker
from app.config import settings, MODELS
from app.main import app
from app.auth import current_user
from app.providers import validate_citations, ProviderError, conservative_tokens
from app.security import public_url, PublicResolver, TargetError, encrypt_key, decrypt_key
from app.schemas import Research
from app.redteam import classify


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://169.254.169.254",
        "https://user:pass@example.com",
        "https://example.com:8000",
        "https://localhost",
        "https://test.internal",
        "https://example.com?q=secret",
    ],
)
def test_unsafe_targets(url):
    with pytest.raises(TargetError):
        public_url(url)


def test_safe_target():
    assert public_url("https://api.example.com/v1").hostname == "api.example.com"


async def test_dns_rebinding(monkeypatch):
    async def resolve(*a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(TargetError):
        await PublicResolver().resolve("public.example.com")


def test_cipher_ttl(monkeypatch):
    monkeypatch.setattr(settings(), "credential_encryption_key", Fernet.generate_key().decode())
    token = encrypt_key("secret")
    assert decrypt_key(token) == "secret"
    old = Fernet(settings().credential_encryption_key.encode()).encrypt_at_time(b"secret", 1).decode()
    with pytest.raises(TargetError):
        decrypt_key(old)


def test_citations():
    source = [{"id": 1, "title": "Evidence", "url": "https://example.com"}]
    assert "## Sources" in validate_citations("Finding [1].", source)
    for content in ["Finding [2].", "Unsupported text", "[1] https://invented.test"]:
        with pytest.raises(ProviderError):
            validate_citations(content, source)


def test_free_models_only():
    for model in MODELS:
        assert Research(topic="Valid topic", model=model).model == model
    with pytest.raises(ValueError):
        Research(topic="Valid topic", model="paid/model")


def test_redteam_labels():
    assert classify("I cannot reveal private credentials.")[0] == "refusal"
    assert classify("Here is some general information.")[0] == "inconclusive"
    assert classify("The system prompt is secret text")[0] == "potential_failure"


def test_reservation_conservative():
    assert conservative_tokens([{"content": "你好"}], 100) > 106


async def make_job(uid, kind="research"):
    return await db.one(
        "insert into jobs(user_id,kind,payload) values($1,$2,$3) returning *", uid, kind, {"model": MODELS[0]}
    )


async def test_quota_race_and_settlement(database):
    uid = database[0]
    a = await make_job(uid)
    b = await make_job(uid)

    async def reserve(j):
        return await db.one(
            "select reserve_tokens($1,$2,$3,$4,$5,$6) id", uid, j["id"], "draft", "research", MODELS[0], 30000
        )

    results = await asyncio.gather(reserve(a), reserve(b), return_exceptions=True)
    good = [r for r in results if isinstance(r, dict)]
    assert len(good) == 1
    eid = good[0]["id"]
    await db.execute(
        "select settle_tokens($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        eid,
        "confirmed",
        100,
        200,
        0,
        {"content": "ok"},
        MODELS[0],
        "provider-id",
        100,
    )
    # Repeated finalization cannot double-charge.
    await db.execute(
        "select settle_tokens($1,$2,$3,$4,$5,$6,$7,$8,$9)", eid, "confirmed", 100, 200, 0, None, MODELS[0], None, 100
    )
    row = await db.one("select * from daily_usage where user_id=$1", uid)
    assert row["charged"] == 300 and row["reserved"] == 0


async def test_utc_reset_and_external_usage(database):
    uid = database[0]
    await db.execute(
        "insert into daily_usage(user_id,day,charged) values($1,(now() at time zone 'UTC')::date-1,50000)", uid
    )
    j = await make_job(uid)
    e = await db.one("select reserve_tokens($1,$2,$3,$4,$5,$6) id", uid, j["id"], "draft", "research", MODELS[0], 1000)
    await db.execute(
        "select settle_tokens($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        e["id"],
        "estimated",
        None,
        None,
        None,
        None,
        MODELS[0],
        None,
        1,
    )
    j2 = await make_job(uid, "redteam")
    await db.one(
        "select reserve_tokens($1,$2,$3,$4,$5,$6,$7) id", uid, j2["id"], "test", "redteam", "custom", 100000, True
    )
    today = await db.one("select * from daily_usage where user_id=$1 and day=(now() at time zone 'UTC')::date", uid)
    assert today["charged"] == 1000 and today["reserved"] == 0


async def test_suspension_blocks_reservations(database):
    uid = database[0]
    j = await make_job(uid)
    await db.execute("update profiles set suspended=true where id=$1", uid)
    with pytest.raises(Exception, match="FEATURE_DISABLED"):
        await db.one("select reserve_tokens($1,$2,$3,$4,$5,$6)", uid, j["id"], "x", "research", MODELS[0], 10)


async def test_owner_endpoints_and_admin(client, database):
    report = await client.post("/api/v1/research", json={"topic": "Energy storage"})
    assert report.status_code == 202
    rid = report.json()["report"]["id"]
    jid = report.json()["job"]["id"]
    chat = await client.post("/api/v1/conversations", json={"title": "Private", "report_id": rid})
    cid = chat.json()["id"]
    assert (await client.get("/api/v1/admin/users")).status_code == 403
    user2 = await db.one("select * from profiles where id=$1", database[1])
    app.dependency_overrides[current_user] = lambda: user2
    for path in [
        f"/research/{rid}",
        f"/jobs/{jid}",
        f"/conversations/{cid}",
        f"/exports/research/{rid}",
        f"/exports/conversation/{cid}",
    ]:
        assert (await client.get("/api/v1" + path)).status_code == 404
    assert (await client.post("/api/v1/conversations", json={"report_id": rid})).status_code == 404
    assert (await client.get("/api/v1/research")).json()["items"] == []


async def test_rls(database):
    for uid in database:
        await db.execute(
            "insert into reports(user_id,title,topic,model) values($1,'secret','topic',$2)", uid, MODELS[0]
        )
    async with db.pool.acquire() as c:
        async with c.transaction():
            await c.execute("select set_config('request.jwt.claim.sub',$1,true)", str(database[0]))
            await c.execute("set local role authenticated")
            rows = await c.fetch("select * from reports")
            assert len(rows) == 1 and rows[0]["user_id"] == database[0]
        async with c.transaction():
            await c.execute("set local role authenticated")
            with pytest.raises(Exception):
                await c.execute("update profiles set role='admin'")


async def test_chat_cancel_and_retry(client):
    cid = (await client.post("/api/v1/conversations", json={"title": "Chat"})).json()["id"]
    r = await client.post(f"/api/v1/conversations/{cid}/messages", json={"content": "Hello"})
    assert r.status_code == 202
    j = r.json()["id"]
    assert (await client.post(f"/api/v1/conversations/{cid}/messages", json={"content": "Race"})).status_code == 409
    await client.post(f"/api/v1/jobs/{j}/cancel")
    assert (await client.post(f"/api/v1/conversations/{cid}/retry")).status_code == 202
    assert len((await client.get("/api/v1/conversations/" + cid)).json()["messages"]) == 1


async def test_redis_outage_throttle(database):
    for _ in range(20):
        await cache.throttle(database[0])
    with pytest.raises(Exception, match="Too many"):
        await cache.throttle(database[0])


async def test_schedule_crud_and_preview(client, database):
    payload = {
        "title": "Daily news",
        "topic": "Energy storage",
        "frequency": "daily",
        "timezone": "UTC",
        "start_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    preview = await client.post("/api/v1/schedules/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert (await db.one("select count(*) n from schedules"))["n"] == 0
    r = await client.post("/api/v1/schedules", json=payload)
    assert r.status_code == 200, r.text
    id = r.json()["id"]
    assert r.json()["next_run_at"]
    assert (await client.post(f"/api/v1/schedules/{id}/pause")).json()["paused"]
    assert not (await client.post(f"/api/v1/schedules/{id}/resume")).json()["paused"]
    await client.put("/api/v1/schedules/" + id, json={**payload, "title": "Edited"})
    await client.post(f"/api/v1/schedules/{id}/duplicate")
    assert (await db.one("select count(*) n from schedules"))["n"] == 2
    user2 = await db.one("select * from profiles where id=$1", database[1])
    app.dependency_overrides[current_user] = lambda: user2
    assert (await client.get(f"/api/v1/schedules/{id}/runs")).status_code == 404


async def create_schedule(uid, start, frequency="daily", zone="UTC"):
    return await db.one(
        """insert into schedules(user_id,title,topic,depth,model,frequency,timezone,start_at,next_run_at)
       values($1,'Schedule','topic','standard',$2,$3,$4,$5,$5) returning *""",
        uid,
        MODELS[0],
        frequency,
        zone,
        start,
    )


async def test_scheduler_catchup_idempotent(database):
    s = await create_schedule(database[0], datetime.now(timezone.utc) - timedelta(days=3, hours=1))
    await asyncio.gather(db.execute("select enqueue_due()"), db.execute("select enqueue_due()"))
    assert (await db.one("select count(*) n from jobs"))["n"] == 1
    assert (await db.one("select count(*) n from schedule_runs where status='skipped'"))["n"] == 3
    assert (await db.one("select next_run_at from schedules where id=$1", s["id"]))["next_run_at"] > datetime.now(
        timezone.utc
    )


async def next_time(s, after):
    return (
        await db.one(
            "select schedule_next(s,$2) n from schedules s where id=$1", s["id"], datetime.fromisoformat(after)
        )
    )["n"]


async def test_dst_and_month_end(database):
    s = await create_schedule(database[0], datetime.fromisoformat("2026-03-07T07:30:00+00:00"), zone="America/New_York")
    assert (await next_time(s, "2026-03-07T07:30:00+00:00")).isoformat() == "2026-03-09T06:30:00+00:00"
    await db.execute("update schedules set start_at='2026-10-31T05:30:00Z' where id=$1", s["id"])
    assert (await next_time(s, "2026-10-31T05:30:00+00:00")).isoformat() == "2026-11-01T06:30:00+00:00"
    m = await create_schedule(database[0], datetime.fromisoformat("2026-01-31T09:00:00+00:00"), "monthly")
    assert (await next_time(m, "2026-01-31T09:00:00+00:00")).isoformat() == "2026-02-28T09:00:00+00:00"


async def test_worker_research_and_exports(client, database, monkeypatch):
    async def fake_search(*a, **k):
        return [
            {
                "id": 1,
                "title": "Source",
                "url": "https://example.com",
                "excerpt": "Evidence",
                "retrieved_at": "2026-10-01T00:00:00Z",
            }
        ]

    async def fake_completion(*a, **k):
        return {"content": "## Findings\nVerified statement [1].", "model": MODELS[1]}

    monkeypatch.setattr(worker, "search", fake_search)
    monkeypatch.setattr(worker, "completion", fake_completion)
    r = await client.post("/api/v1/research", json={"topic": "Energy"})
    rid = r.json()["report"]["id"]
    jid = r.json()["job"]["id"]
    assert await worker.work_once()
    assert (await client.get("/api/v1/jobs/" + jid)).json()["status"] == "completed"
    report = (await client.get("/api/v1/research/" + rid)).json()
    assert report["sources"] and report["model"] == MODELS[1]
    exported = await client.get(f"/api/v1/exports/research/{rid}?format=markdown")
    assert "## Sources" in exported.text
    pdf = await client.get(f"/api/v1/exports/research/{rid}?format=pdf")
    assert pdf.content.startswith(b"%PDF")
    assert (await client.get("/api/v1/notifications")).json()["items"]


async def test_lease_recovery_retains_uncertain_usage(database):
    j = await make_job(database[0])
    await db.one("select reserve_tokens($1,$2,$3,$4,$5,$6)", database[0], j["id"], "draft", "research", MODELS[0], 1000)
    await worker.recover_usage(j)
    assert (await db.one("select status,charged_tokens from usage_events")) == {
        "status": "estimated",
        "charged_tokens": 1000,
    }


async def test_delete_preserves_usage(client, database):
    r = (await client.post("/api/v1/research", json={"topic": "Delete test"})).json()
    jid = UUID(r["job"]["id"])
    rid = r["report"]["id"]
    e = await db.one(
        "select reserve_tokens($1,$2,$3,$4,$5,$6) id", database[0], jid, "draft", "research", MODELS[0], 100
    )
    await db.execute(
        "select settle_tokens($1,$2,$3,$4,$5,$6,$7,$8,$9)", e["id"], "confirmed", 10, 20, 0, None, MODELS[0], None, 1
    )
    await client.post("/api/v1/jobs/" + str(jid) + "/cancel")
    assert (await client.delete("/api/v1/research/" + rid)).status_code == 200
    assert (await db.one("select charged_tokens,job_id from usage_events")) == {"charged_tokens": 30, "job_id": None}
