import httpx
import pytest
from app import db, providers, worker
from app.config import settings, MODELS
from app.auth import current_user
from app.main import app


async def job(uid):
    return await db.one(
        "insert into jobs(user_id,kind,payload) values($1,'research',$2) returning *", uid, {"model": MODELS[0]}
    )


def mock_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(settings(), "open_router_free_api_key", "test-only-key")


async def test_confirmed_usage_and_idempotent_response(database, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={
                "id": "gen-test",
                "model": MODELS[1],
                "choices": [{"message": {"content": "Response"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 34, "cost": 0},
            },
        )

    mock_http(monkeypatch, handler)
    j = await job(database[0])
    r = await providers.completion(j, "draft", [{"role": "user", "content": "Question"}], 100)
    assert r["model"] == MODELS[1]
    assert await providers.completion(j, "draft", [{"role": "user", "content": "Question"}], 100) == r
    assert len(calls) == 1
    e = await db.one("select * from usage_events")
    assert e["charged_tokens"] == 46 and e["cost_usd"] == 0 and e["status"] == "confirmed"
    import json

    body = json.loads(calls[0].content)
    assert body["provider"]["max_price"] == {"prompt": 0, "completion": 0}


async def test_missing_usage_is_estimated(database, monkeypatch):
    mock_http(monkeypatch, lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "Response"}}]}))
    j = await job(database[0])
    await providers.completion(j, "draft", [{"role": "user", "content": "Q"}], 100)
    e = await db.one("select * from usage_events")
    assert e["status"] == "estimated" and e["charged_tokens"] == e["reservation"] and e["cost_usd"] is None


async def test_provider_429_releases_reservation(database, monkeypatch):
    mock_http(monkeypatch, lambda _: httpx.Response(429, json={"error": {"message": "Not returned to client"}}))
    with pytest.raises(providers.ProviderError, match="rate limit"):
        await providers.completion(await job(database[0]), "draft", [{"role": "user", "content": "Q"}], 100)
    e = await db.one("select * from usage_events")
    assert e["status"] == "rejected" and e["charged_tokens"] == 0
    d = await db.one("select * from daily_usage")
    assert d["charged"] == 0 and d["reserved"] == 0


async def test_timeout_is_not_replayed(database, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("timeout")

    mock_http(monkeypatch, handler)
    j = await job(database[0])
    with pytest.raises(providers.ProviderError):
        await providers.completion(j, "draft", [{"role": "user", "content": "Q"}], 100)
    with pytest.raises(providers.ProviderError, match="uncertain"):
        await providers.completion(j, "draft", [{"role": "user", "content": "Q"}], 100)
    assert len(calls) == 1
    e = await db.one("select * from usage_events")
    assert e["status"] == "estimated" and e["charged_tokens"] > 0


async def test_auth_missing_expired_and_suspended(database, monkeypatch):
    app.dependency_overrides.clear()
    # Create the API client before replacing provider-facing clients.
    real = httpx.AsyncClient
    async with real(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/api/v1/profile")).status_code == 401
        monkeypatch.setattr(settings(), "supabase_url", "https://auth.example.test")
        mock_http(monkeypatch, lambda _: httpx.Response(401, json={"message": "expired"}))
        assert (await c.get("/api/v1/profile", headers={"Authorization": "Bearer expired"})).status_code == 401
        # Replace the factory without recursively using its already patched value.
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **k: real(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": str(database[0])}))
            ),
        )
        r = await c.get("/api/v1/profile", headers={"Authorization": "Bearer valid"})
        assert r.status_code == 200
        await db.execute("update profiles set suspended=true where id=$1", database[0])
        assert (await c.get("/api/v1/profile", headers={"Authorization": "Bearer valid"})).status_code == 403


async def test_admin_changes_are_audited(client, database):
    await db.execute("update profiles set role='admin' where id=$1", database[0])
    u = await db.one("select * from profiles where id=$1", database[0])
    app.dependency_overrides[current_user] = lambda: u
    r = await client.patch(
        "/api/v1/admin/users/" + str(database[1]), json={"daily_limit": 1000, "features": {"chat": False}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["daily_limit"] == 1000 and not r.json()["features"]["chat"]
    assert (await db.one("select count(*) n from audit_events"))["n"] == 1
    assert (await client.patch("/api/v1/admin/users/" + str(database[1]), json={"role": "admin"})).status_code == 422
    assert (await client.patch("/api/v1/admin/users/" + str(database[1]), json={"features": None})).status_code == 422
    assert (await client.get("/api/v1/admin/users/" + str(database[1]) + "/analytics")).status_code == 200
    assert (await client.put("/api/v1/admin/settings", json={"default_daily_limit": 75000})).status_code == 200


async def test_redteam_transport_error_and_key_cleanup(client, database, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings(), "credential_encryption_key", Fernet.generate_key().decode())
    r = await client.post(
        "/api/v1/redteam",
        json={
            "target": "custom",
            "model": "model-x",
            "base_url": "https://api.example.com/v1",
            "api_key": "do-not-expose",
            "authorized": True,
            "max_tests": 1,
        },
    )
    assert r.status_code == 202, r.text
    jid = r.json()["id"]
    assert "do-not-expose" not in r.text
    from uuid import UUID

    credential = await db.one("select * from job_credentials where job_id=$1", UUID(jid))
    assert "do-not-expose" not in credential["ciphertext"]

    async def fail(*a, **k):
        raise providers.ProviderError("Target failed")

    monkeypatch.setattr(worker, "completion", fail)
    await worker.work_once()
    result = (await client.get("/api/v1/jobs/" + jid)).json()
    assert result["result"]["results"][0]["outcome"] == "transport_error"
    assert not await db.one("select * from job_credentials where job_id=$1", UUID(jid))


async def test_scheduled_run_now_uses_fresh_sources(client):
    r = await client.post(
        "/api/v1/schedules",
        json={
            "title": "Weekly",
            "topic": "Evidence",
            "frequency": "weekly",
            "timezone": "Asia/Calcutta",
            "start_at": "2027-01-01T09:00:00+05:30",
        },
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    result = await client.post(f"/api/v1/schedules/{sid}/run-now")
    assert result.status_code == 200, result.text
    assert result.json()["job"]["payload"]["fresh"]
    assert len((await client.get(f"/api/v1/schedules/{sid}/runs")).json()["items"]) == 1


async def test_checkpoint_recovery_skips_completed_stage(client, database, monkeypatch):
    r = (await client.post("/api/v1/research", json={"topic": "Recovery"})).json()
    from uuid import UUID

    jid = UUID(r["job"]["id"])
    cp = {"sources": [{"id": 1, "title": "Source", "url": "https://example.com"}], "draft": "Saved draft [1]."}
    await db.execute(
        "update jobs set status='running',lease_until=now()-interval '1 minute',checkpoint=$2,attempts=1 where id=$1",
        jid,
        cp,
    )
    steps = []

    async def complete(j, step, *a, **k):
        steps.append(step)
        return {"content": "Recovered result [1].", "model": MODELS[0]}

    monkeypatch.setattr(worker, "completion", complete)
    await worker.work_once()
    assert steps == ["review"]
    assert (await client.get("/api/v1/jobs/" + str(jid))).json()["status"] == "completed"
