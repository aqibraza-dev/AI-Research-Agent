"""LOCAL TEST HARNESS ONLY. Fake identity/provider services; never deploy this module.
Start only on 127.0.0.1. Uses a dedicated disposable research_browser database.
"""

import asyncio
import base64
import json
import os
import time
import uuid
from pathlib import Path
import asyncpg
from fastapi import Request, HTTPException
from app import db, worker
from app.config import settings, MODELS

URL = os.environ["TEST_DATABASE_URL"]
if "@127.0.0.1:" not in URL or not URL.endswith("/research_browser"):
    raise RuntimeError("Harness requires the dedicated local research_browser database")
s = settings()
s.database_url = URL
s.database_ssl = False
s.supabase_url = "http://127.0.0.1:8001"
s.supabase_publishable_key = "test-key"
s.allowed_origins = "http://127.0.0.1:5174"
s.worker_enabled = True
s.open_router_free_api_key = ""
s.tavily_api_key = ""
s.credential_encryption_key = "c29tZS1maXhlZC10ZXN0LWtleS1vbmx5LTMyeHh4eHg="
from app.main import app  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5174"], allow_methods=["*"], allow_headers=["*"])


async def bootstrap():
    conn = await asyncpg.connect(URL)
    await conn.execute(
        "drop schema if exists public cascade; drop schema if exists auth cascade; create schema public; create schema auth;"
    )
    await conn.execute("""do $$ begin create role anon; exception when duplicate_object then null; end $$;
      do $$ begin create role authenticated; exception when duplicate_object then null; end $$;
      create table auth.users(id uuid primary key,email text,raw_user_meta_data jsonb default '{}');
      create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
      grant usage on schema public,auth to anon,authenticated;""")
    for p in sorted((Path(__file__).parents[2] / "supabase/migrations").glob("*.sql")):
        await conn.execute(p.read_text())
    await conn.close()


async def fake_search(*args, **kwargs):
    return [
        {
            "id": 1,
            "title": "Local test evidence",
            "url": "https://example.com/evidence",
            "excerpt": "Storage capacity increased in this test dataset.",
            "retrieved_at": "2026-10-02T00:00:00Z",
        }
    ]


async def fake_completion(job, step, messages, max_tokens=1500, custom=None):
    await asyncio.sleep(0.2)
    response = {
        "content": "## Findings\n\nStorage research shows progress [1].\n\n- Evidence is limited to this test dataset.\n- Further validation is needed.",
        "model": MODELS[1],
    }
    if job["kind"] == "chat":
        response["content"] = "Here is a follow-up explanation based on your research."
    if job["kind"] == "redteam":
        response["content"] = "I cannot reveal private instructions or credentials."
    e = await db.one(
        "select reserve_tokens($1,$2,$3,$4,$5,$6) id", job["user_id"], job["id"], step, job["kind"], MODELS[1], 500
    )
    await db.execute(
        "select settle_tokens($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        e["id"],
        "confirmed",
        100,
        100,
        0,
        response,
        MODELS[1],
        "test-provider",
        200,
    )
    return response


worker.search = fake_search
worker.completion = fake_completion
users = {}
passwords = {}
tokens = {}


def encoded(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def session(user):
    token = (
        encoded({"alg": "HS256", "typ": "JWT"})
        + "."
        + encoded({"sub": user["id"], "exp": int(time.time()) + 3600, "aud": "authenticated"})
        + ".dGVzdA"
    )
    tokens[token] = user
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 3600,
        "expires_at": int(time.time()) + 3600,
        "refresh_token": user["id"],
        "user": user,
    }


def auth_user(request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if token not in tokens:
        raise HTTPException(401, "Invalid test session")
    return tokens[token]


@app.post("/auth/v1/signup")
async def signup(request: Request):
    p = await request.json()
    email = p["email"]
    uid = str(uuid.uuid4())
    user = {
        "id": uid,
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "email_confirmed_at": "2026-10-01T00:00:00Z",
        "created_at": "2026-10-01T00:00:00Z",
        "app_metadata": {"provider": "email"},
        "user_metadata": p.get("data", {}),
        "identities": [],
    }
    users[email] = user
    passwords[email] = p["password"]
    await db.execute(
        "insert into auth.users(id,email,raw_user_meta_data) values($1,$2,$3)",
        uuid.UUID(uid),
        email,
        user["user_metadata"],
    )
    if email.startswith("admin@"):
        await db.execute("update profiles set role='admin' where id=$1", uuid.UUID(uid))
    return session(user)


@app.post("/auth/v1/token")
async def token(request: Request):
    p = await request.json()
    if "refresh_token" in p:
        u = next((u for u in users.values() if u["id"] == p["refresh_token"]), None)
    else:
        u = users.get(p.get("email")) if passwords.get(p.get("email")) == p.get("password") else None
    if not u:
        raise HTTPException(400, "Invalid credentials")
    return session(u)


@app.get("/auth/v1/user")
async def get_user(request: Request):
    return auth_user(request)


@app.put("/auth/v1/user")
async def update_user(request: Request):
    u = auth_user(request)
    p = await request.json()
    if "password" in p:
        passwords[u["email"]] = p["password"]
    return u


@app.post("/auth/v1/logout")
async def logout(request: Request):
    auth_user(request)
    tokens.pop(request.headers["Authorization"].removeprefix("Bearer "), None)
    return {}


@app.post("/auth/v1/recover")
async def recover():
    return {}


if __name__ == "__main__":
    asyncio.run(bootstrap())
